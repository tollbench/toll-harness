from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import logging
import platform
import shutil
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from toll_harness import __version__
from toll_harness.config import (
    build_model_adapter,
    build_runtime,
    default_config,
    load_config,
)
from toll_harness.core.runtime import HarnessRuntime
from toll_harness.core.types import AutonomyMode, ModelMessage
from toll_harness.email.book_of_houses import BookOfHousesApiError
from toll_harness.fleet import market_target_key
from toll_harness.models.bedrock import BedrockModelAdapter
from toll_harness.models.probe import BedrockProbe
from toll_harness.onboarding import (
    READY,
    WAITING_FOR_COMPANY_VERIFICATION,
    InitAnswers,
    advance_connected_onboarding,
    create_configuration,
    load_onboarding,
    save_onboarding,
)
from toll_harness.onboarding import (
    load_config as load_onboarding_config,
)
from toll_harness.onboarding import (
    save_config as save_onboarding_config,
)
from toll_harness.operator.channel import OperatorChannel
from toll_harness.storage.filesystem import FilesystemArtifactStore
from toll_harness.storage.local import SQLiteStore
from toll_harness.tools.registry import WAKE_TIMERS_NAMESPACE, build_standard_registry
from toll_harness.worker import install_market_worker, market_worker_status

MARKET_SCAN_CANDIDATE_LIMIT = 1
MARKET_SCAN_TOOLS = [
    "state.save",
    "result.complete",
    "result.fail",
    "toll_bench.guide",
    "toll_bench.proposal_schema",
    "toll_bench.capability_taxonomy",
    "toll_bench.read_brief",
    "toll_bench.list_act_kinds",
    "toll_bench.validate_proposal",
    "toll_bench.submit_proposal",
]


def _print(value: Any) -> None:
    print(json.dumps(value, indent=2, default=lambda item: getattr(item, "value", str(item))))


def _result_payload(result) -> dict[str, Any]:
    return {
        "run_id": result.run_id,
        "status": result.status.value,
        "result": result.result,
        "checkpoint": asdict(result.checkpoint),
        "usage": asdict(result.usage),
        "iterations": result.iterations,
        "observed_mode": result.observed_mode.value,
    }


def _prompt(label: str, *, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}:\n> ").strip()
    if not value and default is not None:
        return default
    if not value:
        raise ValueError(f"{label} is required")
    return value


def _yes_no(label: str, *, default: bool) -> bool:
    marker = "Y/n" if default else "y/N"
    value = input(f"{label} [{marker}]:\n> ").strip().lower()
    if not value:
        return default
    if value in {"y", "yes"}:
        return True
    if value in {"n", "no"}:
        return False
    raise ValueError(f"{label} must be answered yes or no")


def _choose(label: str, options: list[tuple[str, str]], *, default_key: str) -> str:
    print(f"{label}:")
    for index, (_key, description) in enumerate(options, 1):
        print(f"  {index}. {description}")
    default_index = next(i for i, (key, _) in enumerate(options, 1) if key == default_key)
    value = input(f"Choose 1-{len(options)} [{default_index}]:\n> ").strip().lower()
    if not value:
        return default_key
    if value.isdigit() and 1 <= int(value) <= len(options):
        return options[int(value) - 1][0]
    for key, _description in options:
        if value == key:
            return key
    raise ValueError(f"{label} must be a number 1-{len(options)}")


def _secret_prompt(label: str) -> str:
    value = getpass.getpass(f"{label} (input hidden):\n> ").strip()
    if not value:
        raise ValueError(f"{label} is required")
    return value


def _require_cli(binary: str, hint: str) -> None:
    if shutil.which(binary) is None:
        raise ValueError(f"The '{binary}' CLI is not on PATH. {hint}")


def _configuration_path(value: str) -> Path:
    path = Path(value).resolve()
    return path if path.name.endswith((".yaml", ".yml")) else path / "agent.yaml"


def _normalize_model_label(value: str) -> str:
    return " ".join(
        "".join(character.lower() if character.isalnum() else " " for character in value).split()
    )


def _resolve_bedrock_model(
    selection: str,
    *,
    intelligence: str,
    profile: str | None,
    region: str,
) -> str:
    probe = BedrockProbe(region=region, profile_name=profile)
    models, profiles = probe.discover()
    known_identifiers = {
        identifier for model in models for identifier in probe._identifiers(model, profiles)
    }
    if selection in known_identifiers:
        return selection
    normalized_selection = _normalize_model_label(selection)
    wanted = set(normalized_selection.split())
    family = intelligence.lower().strip()
    ranked: list[tuple[int, tuple[int, str], dict[str, Any]]] = []
    for model in models:
        provider = str(model.get("providerName") or "")
        label = probe._provider_label(provider, str(model.get("modelId") or ""))
        if family and family not in {label.lower(), provider.lower()}:
            continue
        searchable = _normalize_model_label(
            f"{model.get('modelName', '')} {model.get('modelId', '')}"
        )
        words = set(searchable.split())
        overlap = len(wanted & words)
        if wanted and overlap != len(wanted):
            continue
        exact = int(
            _normalize_model_label(str(model.get("modelName") or "")) == normalized_selection
        )
        ranked.append((exact * 100 + overlap, probe._score(model), model))
    if not ranked:
        raise ValueError(
            f"No Bedrock {intelligence} model matched {selection!r}; run toll-harness bedrock probe"
        )
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return probe._identifiers(ranked[0][2], profiles)[0]


def _test_model_and_browser(config_path: Path) -> dict[str, Any]:
    config = load_config(config_path)
    model_config = config["model"]
    root = config_path.parent
    data_dir = (root / config.get("storage", {}).get("directory", ".toll-harness")).resolve()
    adapter = build_model_adapter(config, root=root, data_dir=data_dir)
    response = adapter.invoke(
        system="This is a provider connectivity check.",
        messages=[ModelMessage.text("user", "Reply with OK.")],
        tools=[],
    )
    providers = config.get("providers") or {}
    browser_name = providers.get("browser")
    browser = None
    if browser_name == "agentcore":
        from toll_harness.browser.agentcore import AgentCoreBrowserProvider

        browser = AgentCoreBrowserProvider(
            region=model_config.get("region", "us-west-2"),
            profile_name=model_config.get("profile"),
            browser_identifier=providers.get("browser_identifier", "aws.browser.v1"),
        )
    elif browser_name == "playwright":
        from toll_harness.browser.playwright import PlaywrightBrowserProvider

        browser = PlaywrightBrowserProvider(headless=providers.get("browser_headless", True))
    try:
        browser_ok = browser is not None
    finally:
        if browser is not None:
            browser.close()
    return {
        "model_connected": True,
        "model_id": adapter.model_id,
        "model_test_tokens": response.usage.total_tokens,
        "browser_connected": browser_ok,
    }


def _run_init_canary(config_path: Path) -> dict[str, Any]:
    resources = build_runtime(config_path)
    original_tools = resources.runtime.enabled_tools
    try:
        config = load_config(config_path)
        mode = AutonomyMode(config["runtime"]["autonomy"])
        resources.runtime.enabled_tools = ["state.save", "result.complete", "result.fail"]
        result = resources.runtime.start(
            "Initialization canary. Call state.save with status='ready', then call "
            "result.complete with a short confirmation.",
            mode,
        )
    finally:
        resources.runtime.enabled_tools = original_tools
        resources.close()
    actions = [
        event.payload.get("name")
        for event in resources.store.list_events(result.run_id)
        if event.kind == "tool.called"
    ]
    return {
        "canary_completed": result.status.value == "completed",
        "run_id": result.run_id,
        "actions": actions,
        "tokens": result.usage.total_tokens,
    }


def _set_worker_preference(config_path: Path, enabled: bool) -> None:
    config = load_onboarding_config(config_path)
    config["worker"] = {"enabled": enabled}
    save_onboarding_config(config_path, config)


def _finish_init(
    config_path: Path,
    result: dict[str, Any],
    *,
    checks: dict[str, Any] | None = None,
    enable_worker: bool,
) -> int:
    checks = checks or _test_model_and_browser(config_path)
    canary = _run_init_canary(config_path)
    config = load_onboarding_config(config_path)
    connected = bool((config.get("toll_bench") or {}).get("connected"))
    worker = None
    if connected and enable_worker and canary["canary_completed"]:
        worker = install_market_worker(config_path)
    state = load_onboarding(config_path, config)
    state["checks"] = checks
    state["canary"] = canary
    if worker is not None:
        state["worker"] = worker
    save_onboarding(config_path, config, state)
    payload = {**result, "checks": checks, "canary": canary, "config": str(config_path)}
    if worker is not None:
        payload["worker"] = worker
    _print(payload)
    worker_ok = worker is None or worker.get("active") is True
    return 0 if canary["canary_completed"] and worker_ok else 2


def command_init(arguments: argparse.Namespace) -> int:
    config_path = _configuration_path(arguments.directory)
    if arguments.resume:
        if not config_path.exists():
            raise FileNotFoundError(config_path)
        result = advance_connected_onboarding(config_path, approve_registration=True)
        if result["status"] == WAITING_FOR_COMPANY_VERIFICATION:
            _set_worker_preference(config_path, not arguments.no_worker)
            return _finish_init(
                config_path,
                {
                    **result,
                    "next": f"toll-harness init {config_path.parent} --resume",
                },
                enable_worker=not arguments.no_worker,
            )
        if result["status"] != READY:
            _print(result)
            return 2
        _set_worker_preference(config_path, not arguments.no_worker)
        return _finish_init(
            config_path,
            result,
            enable_worker=not arguments.no_worker,
        )

    if config_path.exists() and not arguments.force:
        raise FileExistsError(f"Refusing to overwrite {config_path}; pass --force")
    print("Toll Harness\n")
    agent_name = _prompt("Agent name")
    adapter = _choose(
        "Model provider",
        [
            (
                "claude_code",
                "Claude subscription (Pro/Max) - sign in once with the Claude Code "
                "CLI, no API key",
            ),
            ("codex", "ChatGPT subscription - sign in once with `codex login`, no API key"),
            ("anthropic", "Anthropic API key - paste it now"),
            ("openai", "OpenAI API key - paste it now"),
            ("bedrock", "AWS Bedrock - IAM credentials via an AWS profile"),
        ],
        default_key="claude_code",
    )
    aws_profile = None
    aws_region = "us-west-2"
    model_api_key = None
    if adapter == "bedrock":
        intelligence = _prompt("Intelligence family", default="Mistral")
        aws_profile = _prompt("AWS profile", default="default")
        if aws_profile == "default":
            aws_profile = None
        aws_region = _prompt("AWS region", default="us-west-2")
        model_selection = _prompt("Intelligence/model")
        model_id = _resolve_bedrock_model(
            model_selection,
            intelligence=intelligence,
            profile=aws_profile,
            region=aws_region,
        )
    elif adapter == "claude_code":
        _require_cli(
            "claude",
            "Install Claude Code (https://claude.com/claude-code) and run `claude` "
            "once to sign in with your subscription, or set CLAUDE_CODE_OAUTH_TOKEN "
            "from `claude setup-token`. Then run init again.",
        )
        intelligence = "Claude"
        model_id = _prompt("Model (as your claude CLI names it)", default="opus")
    elif adapter == "codex":
        _require_cli(
            "codex",
            "Install the Codex CLI (https://github.com/openai/codex) and run "
            "`codex login` to sign in with ChatGPT. Then run init again.",
        )
        intelligence = "GPT"
        model_id = _prompt("Model (as your codex CLI names it)", default="gpt-5-codex")
    elif adapter == "anthropic":
        intelligence = "Claude"
        model_id = _prompt("Model id", default="claude-opus-4-8")
        model_api_key = _secret_prompt("Anthropic API key")
    else:
        intelligence = "GPT"
        model_id = _prompt("Model id (for example gpt-5.2)")
        model_api_key = _secret_prompt("OpenAI API key")
    company = _prompt("Company")
    mode = _prompt("Operating mode", default="Autonomous").title()
    connect = _yes_no("Connect to Toll Bench / Book of Houses?", default=True)
    use_email = _yes_no("Use Book of Houses agent email?", default=True) if connect else False
    company_url = responsible_name = jurisdiction = verification_recipient = None
    if connect:
        company_url = _prompt("Company public URL")
        responsible_name = _prompt("Responsible party legal name", default=company)
        jurisdiction = _prompt("Responsible party jurisdiction (for example US-OR)")
        verification_recipient = _prompt("Company verification email")
    answers = InitAnswers(
        agent_name=agent_name,
        intelligence=intelligence,
        model_id=model_id,
        company=company,
        mode=mode,
        aws_profile=aws_profile,
        aws_region=aws_region,
        connect_toll_bench=connect,
        use_book_of_houses_email=use_email,
        company_url=company_url,
        responsible_legal_name=responsible_name,
        responsible_jurisdiction=jurisdiction,
        verification_recipient=verification_recipient,
        model_adapter=adapter,
        model_api_key=model_api_key,
    )
    config_path = create_configuration(config_path.parent, answers)
    _set_worker_preference(config_path, connect and not arguments.no_worker)
    checks = _test_model_and_browser(config_path)
    if connect:
        approved = arguments.yes or _yes_no(
            "Validation is no-write. Register this agent after validation passes?", default=True
        )
        result = advance_connected_onboarding(config_path, approve_registration=approved)
        if result["status"] == WAITING_FOR_COMPANY_VERIFICATION:
            result = {
                **result,
                "next": f"toll-harness init {config_path.parent} --resume",
            }
        elif result["status"] != READY:
            _print({**result, "checks": checks, "config": str(config_path)})
            return 2
    else:
        result = {"status": READY, "connection": "standalone"}
    return _finish_init(
        config_path,
        result,
        checks=checks,
        enable_worker=connect and not arguments.no_worker,
    )


def command_doctor(arguments: argparse.Namespace) -> int:
    checks: dict[str, Any] = {
        "toll_harness_version": __version__,
        "python": platform.python_version(),
    }
    configured_profile = arguments.profile
    configured_region = arguments.region or "us-west-2"
    if arguments.config:
        try:
            config = load_config(arguments.config)
            checks["config"] = {"ok": True, "path": str(Path(arguments.config).resolve())}
            model_config = config.get("model", {})
            checks["model_id"] = model_config.get("model_id")
            configured_profile = arguments.profile or model_config.get("profile")
            configured_region = arguments.region or model_config.get("region", "us-west-2")
        except Exception as error:
            checks["config"] = {"ok": False, "error": str(error)}
    try:
        probe = BedrockProbe(region=configured_region, profile_name=configured_profile)
        checks["aws_identity"] = {"ok": True, **probe.identity()}
        try:
            models, profiles = probe.discover()
            checks["bedrock_catalog"] = {
                "ok": True,
                "foundation_models": len(models),
                "inference_profiles": len(profiles),
            }
        except Exception as error:
            checks["bedrock_catalog"] = {"ok": False, "error": str(error)}
        try:
            sessions = probe.session.client("bedrock-agentcore").list_browser_sessions(
                browserIdentifier="aws.browser.v1",
                maxResults=100,
                status="READY",
            )
            checks["agentcore_browser"] = {
                "ok": True,
                "active_system_browser_sessions": len(sessions.get("items", [])),
            }
        except Exception as error:
            checks["agentcore_browser"] = {"ok": False, "error": str(error)}
    except Exception as error:
        checks["aws_identity"] = {"ok": False, "error": str(error)}
    if arguments.config and checks.get("config", {}).get("ok"):
        try:
            checks["runtime"] = {"ok": True, **_test_model_and_browser(Path(arguments.config))}
        except Exception as error:
            checks["runtime"] = {"ok": False, "error": str(error)}
        config = load_config(arguments.config)
        if (config.get("toll_bench") or {}).get("connected") and (
            config.get("worker") or {"enabled": True}
        ).get("enabled", True):
            try:
                worker = market_worker_status(arguments.config)
                checks["market_worker"] = {"ok": worker["active"] and worker["enabled"], **worker}
            except Exception as error:
                checks["market_worker"] = {"ok": False, "error": str(error)}
    _print(checks)
    required = checks.get("aws_identity", {}).get("ok")
    if arguments.config:
        required = required and checks.get("runtime", {}).get("ok")
        if "market_worker" in checks:
            required = required and checks["market_worker"].get("ok")
    else:
        required = required and checks.get("bedrock_catalog", {}).get("ok")
    return 0 if required else 1


def command_run(arguments: argparse.Namespace) -> int:
    resources = build_runtime(arguments.config)
    try:
        config = load_config(arguments.config)
        mode = AutonomyMode(config.get("runtime", {}).get("autonomy", "autonomous"))
        if arguments.resume:
            result = resources.runtime.resume(arguments.resume)
        else:
            if not arguments.goal:
                raise ValueError("--goal is required when starting a run")
            result = resources.runtime.start(arguments.goal, mode)
        _print(_result_payload(result))
        return 0 if result.status.value in {"completed", "waiting"} else 2
    finally:
        resources.close()


def command_market_connect(arguments: argparse.Namespace) -> int:
    resources = build_runtime(arguments.config)
    try:
        if resources.toll_bench is None:
            raise ValueError("This agent is not connected to Toll Bench")
        result = resources.toll_bench.ensure_reachable()
        _print(result)
        return 0 if result.get("ok") else 2
    finally:
        resources.close()


_LOGGER = logging.getLogger("toll_harness.cli")


# Attention "kinds" this worker dispatches on, in descending priority. Exactly
# one obligation is handled per watch cycle: the model is handed a single,
# focused instruction and only the tools that one obligation needs, instead of a
# manual covering every task type. The watch loop polls attention again on the
# next cycle for the remaining obligations.
_OBLIGATION_PRIORITY: tuple[str, ...] = (
    "deal_step",
    "draft_sent_back",
    "file_informed_plan",
    "unanswered_message",
    "feedback_returned",
)

# Shared bookkeeping tools every focused obligation goal needs: load/save a
# compact checkpoint and report a confirmed result or a correction.
_BOOKKEEPING_TOOLS: frozenset[str] = frozenset(
    {"state.load", "state.save", "result.complete", "result.fail"}
)

# Work tools a configured agent needs after it signs a deal. The dispatch still
# intersects this envelope with the agent's enabled tools, so an unavailable
# provider is never advertised to the model. human.request is intentionally
# absent: person-owned access must arrive as a disclosed, signed GRANT rather
# than as a mid-deal credential request.
_DEAL_WORK_TOOLS: frozenset[str] = frozenset(
    {
        "web.search",
        "web.fetch",
        "http.request",
        "browser.open",
        "browser.observe",
        "browser.click",
        "browser.type",
        "browser.type_secret",
        "browser.wait",
        "secret.generate",
        "files.list",
        "files.read",
        "files.write",
        "wake.set_timer",
        "email.list",
        "email.read",
    }
)

# Per-kind focused instruction + the minimal tool set. Each entry narrows what
# the model reads and can call for that one obligation; capability across all
# kinds is preserved because the watch loop returns for the next obligation.
_DEAL_STEP_INSTRUCTION = (
    "If owed_replies is non-empty, answer it before anything else: file an "
    "email act with in_reply_to (toll_bench.propose_act, kind email, "
    "in_reply_to set to that reply's id and your body_text); the bench refuses "
    "any other filing on the step until you do (reply_owed). Never re-send the "
    "thing they replied to. If the message is not a question -- spam, a bounce, "
    "an out-of-office -- say why in one plain sentence with "
    "toll_bench.dismiss_reply. "
    "An act with state sent_back carries the person's note: read it, file a "
    "corrected act, never re-file the same one and never wait on the dead one. "
    "Handle the single active deal step below and nothing else. The step's "
    "current state and history are included below as current_step (fetch it "
    "only if that field is null). Obey the step's progress-pulse cadence, and "
    "use a document outcome for "
    "APPROVE review steps and a short text outcome only where permitted. ACT "
    "(rule 212): if the step's work is sending an email, you do not send it and "
    "you do not ask the person to send it -- call toll_bench.propose_act with the "
    "exact to, subject and body_text (one act per email; read the selection answers "
    "for the recipients). The person approves it word for word on their step and "
    "Book of Houses sends it from your mailbox; wait for that (poll current_step), "
    "then file your outcome quoting the send receipt. If the step declared an act, "
    "the bench refuses your outcome until that act is approved and sent "
    "(acts_not_filed); file the act first, or withdraw the declaration with a "
    "reason (toll_bench.withdraw_act_declaration). If the person sends it back, "
    "read their note on the step thread and file a corrected act. Never write "
    "'click Approve to send' or 'from your mailbox' to the person: Approve on a "
    "document does not send anything. If confirmed_email_send_receipt is present "
    "below, the platform sent it already: file that receipt as evidence and do not "
    "send again. Do not call it inbox delivery unless the receipt explicitly "
    "confirms inbox delivery. WAIT (rule 216): once the act has gone out -- or "
    "once you have asked any outside person or provider for something -- and "
    "nothing can move until they answer, call toll_bench.wait_outside with on, "
    "who, and what in one plain sentence. Never sit silent at agent_working "
    "while the ball is outside. While the wait stands no overdue mark is "
    "written against you and the deal cannot end out of time; it ends by "
    "itself on your next check-in, on your outcome, or when the reply lands "
    "(watch waiting_outside, inbound_replies and owed_replies on "
    "current_step). "
    "A BLOCK STEP IS THE PLATFORM'S (rule 229): where your plan declared a "
    "registry block, the platform filed that act itself when the step opened "
    "and files this step's outcome when it executes. Read declared_acts and "
    "acts on current_step: while that act is held, approved or executed you "
    "file NOTHING on the step -- no second act, no outcome. After a deny or a "
    "failure the step is yours again with the person's words in acts[].note "
    "and the move in acts[].next, no review round spent: file ONE changed act "
    "that answers them, or say on the thread why there is nothing to change. "
    "A CALENDAR EVENT IS AN ACT TOO (rule 219): on a step whose deal holds a "
    "calendar grant, call toll_bench.propose_act with kind calendar_event and "
    "the exact summary, start and end -- never ask the person to put it on "
    "their own calendar, and never use a second door for it. "
    "You may create or "
    "use the agent's own external accounts only with the responsible party's "
    "legal and billing authority. Never request, receive, or use a person's "
    "password, OTP, session, or cookie. Person-owned access must already exist "
    "as a disclosed, signed GRANT; do not widen it mid-deal."
)
_FILE_INFORMED_PLAN_INSTRUCTION = (
    "File the single plan below and nothing else. You are the selected agent on "
    "this want. Read the owned proposal and the selection answers before filing; "
    "copy every required field "
    "of each execution step from the owned proposal and change only what the "
    "answers require. For an email-delivery want, author exactly ONE execution "
    "step that is YOURS (actor: agent, ask: APPROVE) and declare the act on it: "
    "\"acts\": [{\"kind\": \"email\", \"to\": <who>, \"purpose\": <why>}]. Its "
    "outcome_promise says you will file the exact email as an act, the person "
    "approves it word for word, and Book of Houses sends it; its single "
    "review_approve block is the person confirming the send receipt. For a want that arranges a "
    "TIME -- a call, a meeting or a visit -- the brief's template is TWO steps and the order "
    "is the law (rule 230). Step 1 connects the person's Google Calendar (a GRANT step). Step 2 "
    "is the meeting block: Book of Houses reads the open times, shows the person the email and "
    "the three times, and sends on their tap. Never plan a step where the person types their own "
    "times, and never ask the person for their availability (REJ-28). Declaring the block is the "
    "whole "
    "scheduling move, so do not hand-build a meeting from an email plus a wait. Never write "
    "a step that asks the person to click Send or send it from their mailbox -- "
    "the bench refuses it (REJ-26). Do not author a separate compose/draft step. "
    "If the person failed a prior attempt with a reason (prior_attempt in the "
    "brief), the new plan must answer that reason. For any other easy want, use exactly two "
    "execution steps. Every declared_odds value is your chance the PERSON ends up with "
    "the thing, judged from that step (never the chance you clear the step), strictly "
    "between 0 and 1; a plan is filed all at once, so its line may not fall from one "
    "step to the next (REJ-29). The selection answers carry answer_value and format "
    "beside the person's words: the option id they tapped, true or false, a number, a "
    "field map, a date. answer_value is ALWAYS present and is null only for a text "
    "answer -- read it, not only the prose. unanswered_questions carries format too. "
    "THE TEMPLATE IS A FORM (contract 3.0, rule 228 amended): read the brief "
    "(toll_bench.read_brief). plan_template is a BLANK skeleton and block_templates is "
    "the catalog to pull from; every step you keep you write in your own words, and a "
    "step filed with an empty title or promise is not a plan and is dropped. The "
    "platform writes a BLOCK step's title, promise and har_blocks at signing, files the "
    "act when the step opens and files that step's outcome when it runs, so your hands "
    "on a block are its fields. A meeting message carries no dates and no times, and "
    "`with` is left out unless the invitee's address is known. Pull a block in FULL: a "
    "block that runs on the person's connection is TWO steps and the GRANT comes first, "
    "and a meeting block with no calendar GRANT step before it is refused REJ-35. An "
    "older bench may name required_blocks and refuse a missing one REJ-32."
)
_UNANSWERED_MESSAGE_INSTRUCTION = (
    "Answer the single unanswered step message below and nothing else. The "
    "step's current state and history are included below as current_step "
    "(fetch it only if that field is null). Call "
    "toll_bench.reply_step_message before any check-in or outcome; a work pulse "
    "is not a reply."
)
_FEEDBACK_RETURNED_INSTRUCTION = (
    "The person failed the selected agent and said why; the feedback is in the "
    "obligation below, in the person's own words. Your bid was held behind that "
    "selection and is now back on the table. Read your own bid and the feedback, "
    "then re-file ONCE only if you can fix what they named -- a re-file "
    "supersedes your earlier bid and a second one is refused. If you cannot fix "
    "what they named, change nothing and call result.complete with 'let it "
    "stand'."
)
_GOAL_COMMON_TAIL = (
    " Do not bid on unrelated open targets and do not request the full protocol "
    "or proposal schema. If a previous attempt failure is present, correct it "
    "and never repeat the rejected payload. Save a compact checkpoint and report "
    "only confirmed results."
)

# H8: keep it cheap. Every dispatch measures the words it hands the model and
# the tools it exposes, and a cycle that blows past the budget logs a warning,
# so prompt growth is caught when it happens rather than on the bill.
_DISPATCH_WORD_BUDGET = 800


def _dispatch_meter(kind: str, goal: str, tools: list[str]) -> dict[str, Any]:
    words = len(goal.split())
    meter = {
        "kind": kind or "market_scan",
        "goal_words": words,
        "goal_chars": len(goal),
        "tool_count": len(tools),
        "word_budget": _DISPATCH_WORD_BUDGET,
    }
    if words > _DISPATCH_WORD_BUDGET:
        _LOGGER.warning(
            "Dispatch for %s spent %d words (budget %d)",
            meter["kind"],
            words,
            _DISPATCH_WORD_BUDGET,
        )
    return meter

# RULE 220 (second half). The person pressed Send back on an act and said why.
# That draft is DEAD -- it can never be approved and a send comes back
# EMAIL_APPROVAL_REJECTED -- so the only move is a NEW act carrying the change
# they asked for. Forced live on 2026-09-03: three drafts came back with "we
# need the time to be 11-1130" and the agent waited for approval on drafts that
# could not be approved.
_DRAFT_SENT_BACK_INSTRUCTION = (
    "The person sent one of your acts back and said why. Read the reason: it is "
    "in `note` on that act in the `acts` list, and in `sent_back_reason` on "
    "`drafts_sent_back`, both on toll_bench.current_step. That act is DEAD -- it "
    "can never be approved and a send is refused -- so do NOT wait on it and do "
    "NOT re-file the same words. File ONE new act with toll_bench.propose_act "
    "carrying exactly the change they asked for, on the same step, and nothing "
    "else. If their reason is not something you can act on, say so on the step "
    "thread rather than re-filing."
)

_OBLIGATION_DISPATCH: dict[str, dict[str, Any]] = {
    "deal_step": {
        "instruction": _DEAL_STEP_INSTRUCTION,
        "tools": frozenset(
            {
                "toll_bench.current_step",
                "toll_bench.propose_act",
                "toll_bench.dismiss_reply",
                "toll_bench.file_outcome",
                "toll_bench.post_check_in",
                "toll_bench.reply_step_message",
                "toll_bench.read_finalist_answers",
                "email.send",
                "email.reply",
            }
        )
        | _DEAL_WORK_TOOLS
        | _BOOKKEEPING_TOOLS,
    },
    "file_informed_plan": {
        "instruction": _FILE_INFORMED_PLAN_INSTRUCTION,
        "tools": frozenset(
            {
                "toll_bench.read_finalist_answers",
                "toll_bench.list_proposals",
                # Contract 2.44: the required blocks and the step to file for
                # each one ride the brief, so the plan cannot be filed without
                # reading it.
                "toll_bench.read_brief",
                "toll_bench.list_act_kinds",
                "toll_bench.submit_informed_plan",
                # The public exit rides the same dispatch: an agent that cannot
                # produce this plan says so out loud rather than retrying.
                "toll_bench.withdraw_proposal",
            }
        )
        | _BOOKKEEPING_TOOLS,
    },
    "feedback_returned": {
        "instruction": _FEEDBACK_RETURNED_INSTRUCTION,
        "tools": frozenset(
            {
                "toll_bench.read_brief",
                "toll_bench.list_proposals",
                "toll_bench.validate_proposal",
                "toll_bench.submit_proposal",
                "toll_bench.withdraw_proposal",
            }
        )
        | _BOOKKEEPING_TOOLS,
    },
    # Server contract 2.29 raises this kind when the person sends one of your
    # drafts back. It reached the model only through the unknown-kind fallback
    # (f635ae0), which hands over four instructions and every tool at once --
    # capability, but no aim. This is the same move, said in one sentence.
    "draft_sent_back": {
        "instruction": _DRAFT_SENT_BACK_INSTRUCTION,
        "tools": frozenset(
            {
                "toll_bench.read_brief",
                "toll_bench.current_step",
                "toll_bench.propose_act",
            }
        )
        | _BOOKKEEPING_TOOLS,
    },
    "unanswered_message": {
        "instruction": _UNANSWERED_MESSAGE_INSTRUCTION,
        "tools": frozenset(
            {
                "toll_bench.current_step",
                "toll_bench.reply_step_message",
            }
        )
        | _BOOKKEEPING_TOOLS,
    },
}


# Idle-step memo: step_id -> the step-payload fingerprint at the last dispatched
# run that ended without failing. When the next cycle fetches an IDENTICAL
# payload, the model has already inspected exactly this state and made no move
# -- the step is waiting on the person, and re-dispatching the model over it
# would burn a full run to reach the same conclusion while starving every
# lower-ranked obligation (a $0 plan request sat ~55 minutes behind
# one such step on 2026-08-29). Process-local by design: a restart just costs
# one extra inspection per step.
_IDLE_STEP_MEMO: dict[str, str] = {}

# Re-dispatch this long before a due progress pulse rather than after it, so
# the pulse never goes overdue waiting on the poll interval.
_IDLE_PULSE_MARGIN_SECONDS = 90.0


def _deal_step_fingerprint(step_payload: dict[str, Any] | None) -> str:
    """Digest of every part of a current_step payload an agent can act on.

    INCLUDES the person's decisions on the agent's acts (r220): a send-back
    with a reason is exactly the new input the model has to act on, and it was
    invisible here until 2026-09-03.

    Deliberately EXCLUDES latest_work_pulse AND the agent's own thread
    messages: pulses and self-authored messages are the agent's output, not
    input it can act on, and counting them would let a model that re-posts
    the same ask every run (one posted the identical question 20 times on
    2026-08-29) look permanently busy. Only the person's side of the thread,
    the step itself, and the materials count as state. Pulse timing is judged
    separately by _deal_step_pulse_due.
    """
    if not isinstance(step_payload, dict):
        return ""
    thread = step_payload.get("step_thread") or {}
    messages = thread.get("messages") or []
    basis = {
        "step": step_payload.get("current_step"),
        "message_ids": [
            item.get("id")
            for item in messages
            if isinstance(item, dict) and item.get("who") != "agent"
        ],
        "unread_from_person": thread.get("unread_from_person"),
        "unanswered_elsewhere": thread.get("unanswered_elsewhere"),
        # r220: the person's decision on an act IS new input. Before this the
        # fingerprint ignored acts entirely, so pressing Send back changed
        # nothing the memo could see and a live agent idled for hours with the
        # reason sitting in a column. id + state + note, because a redraft is
        # driven by the words, not just by the state flipping.
        "acts": [
            (item.get("act_id"), item.get("state"), item.get("note"))
            for item in (step_payload.get("acts") or [])
            if isinstance(item, dict)
        ],
        "drafts_sent_back": [
            (item.get("approval_id") or item.get("id"),
             item.get("sent_back_reason"))
            for item in (step_payload.get("drafts_sent_back") or [])
            if isinstance(item, dict)
        ],
        # An unanswered reply from an outside person is a debt the model must
        # act on, and the bench refuses every other filing until it does.
        "owed_replies": [
            (item.get("id"), item.get("answered_at"))
            for item in (step_payload.get("owed_replies") or [])
            if isinstance(item, dict)
        ],
        "grants": (step_payload.get("access") or {}).get("grants"),
        "released_materials_count": step_payload.get("released_materials_count"),
        "deal": step_payload.get("deal"),
    }
    return json.dumps(basis, sort_keys=True, default=str)


def _deal_step_pulse_due(step_payload: dict[str, Any], now: float | None = None) -> bool:
    """True when the step owes a progress pulse, or its schedule is unreadable.

    Unreadable means due: the pulse cadence (r100) is a promise to the person,
    so any doubt resolves toward dispatching the model, never toward skipping.
    """
    pulse = step_payload.get("latest_work_pulse")
    if not isinstance(pulse, dict):
        return True  # never pulsed; the first pulse is owed minutes after start
    if pulse.get("overdue"):
        return True
    next_due = pulse.get("next_due_at")
    if not next_due:
        return True
    try:
        due = datetime.fromisoformat(str(next_due).replace("Z", "+00:00"))
    except ValueError:
        return True
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    current = now if now is not None else time.time()
    return due.timestamp() <= current + _IDLE_PULSE_MARGIN_SECONDS


def _deal_step_is_idle(
    step_id: str, step_payload: dict[str, Any] | None, now: float | None = None
) -> bool:
    """A step is idle when a prior run saw this exact state and made no move,
    nothing from the person has arrived since, and no progress pulse is due."""
    if not step_id or not isinstance(step_payload, dict):
        return False
    memo = _IDLE_STEP_MEMO.get(step_id)
    if not memo:
        return False
    thread = step_payload.get("step_thread") or {}
    if thread.get("unread_from_person"):
        return False
    if _deal_step_pulse_due(step_payload, now=now):
        return False
    return _deal_step_fingerprint(step_payload) == memo


def _select_obligation(obligations: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the single highest-priority obligation to handle this cycle.

    Kinds are ranked by ``_OBLIGATION_PRIORITY``; within a kind the first item
    the server returned wins (attention arrives server-ordered). Any obligation
    of an unranked kind is a last-resort fallback so nothing is silently
    dropped.
    """
    for kind in _OBLIGATION_PRIORITY:
        match = next((item for item in obligations if item.get("kind") == kind), None)
        if match is not None:
            return match
    return obligations[0] if obligations else None


# Circuit breaker over an obligation that keeps failing the same way. A selected
# agent whose model could not emit a valid tool-use block for its plan payload
# had that one obligation re-dispatched 663 times in 11 hours on a flat
# 65-second delay -- no counter, no ceiling, and nothing telling the person
# waiting on the plan. Now every obligation is keyed, identical failures
# are counted, the delay doubles, and at the stall threshold the key stops being
# dispatched at all. A plan request the model cannot produce leaves through the
# public exit instead of retrying in silence (rule 97).
#
# Process-local by design, like the idle-step memo: a restart costs one extra
# attempt per key, which is the safe direction.
_OBLIGATION_FAILURES: dict[tuple[str, ...], dict[str, Any]] = {}
_STALL_THRESHOLD_DEFAULT = 5
_STALL_DELAY_CAP_SECONDS = 3600.0


def _obligation_key(obligation: dict[str, Any]) -> tuple[str, ...]:
    """The one piece of work an obligation names, independent of its wording."""
    return tuple(
        str(obligation.get(field) or "")
        for field in ("kind", "target_id", "proposal_id", "deal_id", "step_id")
    )


def _obligation_fingerprint(obligation: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(obligation, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _failure_signature(payload: Any) -> str:
    """The failure's own words, so two different failures never count as one."""
    if isinstance(payload, dict):
        for field in ("error", "last_error", "reason", "message"):
            value = payload.get(field)
            if value:
                return str(value)
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return str(payload or "unknown_failure")


def _breaker_skip(obligation: dict[str, Any]) -> bool:
    """True while this exact obligation is stalled and has not changed."""
    key = _obligation_key(obligation)
    state = _OBLIGATION_FAILURES.get(key)
    if not state or not state.get("stalled"):
        return False
    if state.get("fingerprint") != _obligation_fingerprint(obligation):
        # The server changed what it is asking for: this is new work, and a
        # stalled key must never outlive the payload that stalled it.
        _OBLIGATION_FAILURES.pop(key, None)
        return False
    return True


def _breaker_reset(obligation: dict[str, Any]) -> None:
    _OBLIGATION_FAILURES.pop(_obligation_key(obligation), None)


def _withdraw_unproducible_plan(
    resources: Any, obligation: dict[str, Any], attempts: int, error: str
) -> dict[str, Any]:
    """Leave the want out loud when this agent cannot file the plan it owes."""
    proposal_id = str(obligation.get("proposal_id") or "")
    if not proposal_id:
        return {"ok": False, "error": "withdraw_skipped_without_proposal_id"}
    reason = f"model could not produce a valid plan after {attempts} attempts: {error[:200]}"
    try:
        response = resources.toll_bench.withdraw_proposal(
            proposal_id, reason=reason, cause="cannot_deliver"
        )
    except Exception as failure:  # noqa: BLE001 - the exit must not kill the cycle
        _LOGGER.warning(
            "Withdrawal of unproducible plan %s failed: %s", proposal_id, failure
        )
        return {"ok": False, "error": "withdraw_failed", "message": str(failure)}
    _LOGGER.warning(
        "Withdrew proposal %s with cause cannot_deliver after %d identical failures",
        proposal_id,
        attempts,
    )
    return {"ok": True, "proposal_id": proposal_id, "reason": reason, "response": response}


def _breaker_record_failure(
    resources: Any,
    obligation: dict[str, Any],
    error: str,
    *,
    threshold: int,
) -> dict[str, Any]:
    """Count one failure on this key and say how long to wait before retrying."""
    key = _obligation_key(obligation)
    state = _OBLIGATION_FAILURES.get(key)
    if state is None or state.get("error") != error:
        # A different failure is a different problem: the count starts over.
        state = {"count": 0, "error": error, "stalled": False, "exited": False}
    state["count"] = int(state["count"]) + 1
    state["fingerprint"] = _obligation_fingerprint(obligation)
    attempts = int(state["count"])
    delay = min(_STALL_DELAY_CAP_SECONDS, 60.0 * (2**attempts))
    breaker: dict[str, Any] = {
        "key": list(key),
        "consecutive_failures": attempts,
        "error": error,
        "retry_after_seconds": delay,
        "stalled": False,
    }
    if attempts >= max(1, threshold) and not state["stalled"]:
        state["stalled"] = True
        # ONE line, at the fleet log level, carrying the count and the error.
        _LOGGER.warning(
            "Stalling obligation %s after %d identical failures: %s",
            "/".join(part for part in key if part),
            attempts,
            error,
        )
    if state["stalled"]:
        breaker["stalled"] = True
        if obligation.get("kind") == "file_informed_plan" and not state["exited"]:
            state["exited"] = True
            breaker["withdrawal"] = _withdraw_unproducible_plan(
                resources, obligation, attempts, error
            )
    _OBLIGATION_FAILURES[key] = state
    return breaker


def _configured_stall_threshold(config_path: Any) -> int:
    """agent.yaml fleet.stall_threshold, or the default when it is not set."""
    try:
        config = load_config(config_path)
    except Exception:  # noqa: BLE001 - an unreadable config must not stop the watch
        return _STALL_THRESHOLD_DEFAULT
    fleet = config.get("fleet") or {}
    try:
        return max(1, int(fleet.get("stall_threshold", _STALL_THRESHOLD_DEFAULT)))
    except (TypeError, ValueError):
        return _STALL_THRESHOLD_DEFAULT


def _process_market_attention(
    resources: Any,
    wait: int,
    previous_failure: dict[str, Any] | None = None,
    *,
    stall_threshold: int | None = None,
) -> dict[str, Any]:
    if resources.toll_bench is None:
        raise ValueError("This agent is not connected to Toll Bench")
    threshold = _STALL_THRESHOLD_DEFAULT if stall_threshold is None else int(stall_threshold)
    reachability = resources.toll_bench.ensure_reachable()
    if not reachability.get("ok"):
        return {"ok": False, "reachability": reachability}
    attention = resources.toll_bench.attention(wait=wait)
    obligations = [
        item for item in attention.get("attention") or [] if item.get("kind") != "reachability_ping"
    ]
    if not obligations:
        return {
            "ok": True,
            "reachability": reachability,
            "attention_count": 0,
            "run": None,
        }
    # Stalled keys drop out before anything is fetched or dispatched. They come
    # back the moment the server changes what it is asking for.
    _live: list[dict[str, Any]] = []
    _stalled = 0
    for item in obligations:
        if _breaker_skip(item):
            _stalled += 1
            continue
        _live.append(item)
    obligations = _live
    if not obligations:
        return {
            "ok": True,
            "reachability": reachability,
            "attention_count": 0,
            "stalled_obligations": _stalled,
            "retry_after_seconds": 300.0,
            "run": None,
        }
    finalist_proposal_ids = {
        str(item.get("proposal_id") or "")
        for item in obligations
        if item.get("kind") == "file_informed_plan"
    }
    if finalist_proposal_ids:
        owned_proposals = resources.toll_bench.list_proposals().get("proposals") or []
        paid_finalists = [
            proposal
            for proposal in owned_proposals
            if str(proposal.get("id") or "") in finalist_proposal_ids
            and int(proposal.get("total_ask_cents") or 0) > 0
        ]
        if paid_finalists:
            status = resources.toll_bench.status()
            payout = status.get("payout") or {}
            if not payout.get("ready"):
                # Free wants must not wait on payout (Steven 2026-08-28). A PAID
                # selection's plan needs a ready payout account, but a FREE
                # plan, a deal step, or a message does not. Drop only the blocked
                # paid obligations and service the rest this cycle; the paid one
                # resumes automatically once operator onboarding completes.
                _blocked_ids = {str(p.get("id") or "") for p in paid_finalists}
                obligations = [
                    item
                    for item in obligations
                    if not (
                        item.get("kind") == "file_informed_plan"
                        and str(item.get("proposal_id") or "") in _blocked_ids
                    )
                ]
                _LOGGER.warning(
                    "Deferring %d paid plan request(s) blocked on payout; "
                    "servicing %d remaining obligation(s)",
                    len(_blocked_ids),
                    len(obligations),
                )
                if not obligations:
                    return {
                        "ok": False,
                        "error": "payout_not_ready",
                        "message": (
                            "A paid plan request is waiting, but this agent's Stripe Connect "
                            "payout account is not ready. Complete operator onboarding; the worker "
                            "will resume the obligation automatically."
                        ),
                        "reachability": reachability,
                        "attention_count": 0,
                        "proposal_ids": sorted(_blocked_ids),
                        "payout": {
                            key: payout.get(key)
                            for key in ("ready", "onboarding_needed", "onboarding_link_call")
                        },
                        "retry_after_seconds": 300.0,
                        "run": None,
                    }
    # Idle deal steps: skip without a model run any step whose payload is
    # byte-identical to what the model already inspected and left untouched,
    # unless a progress pulse is due (r100 cadence still gets its one run per
    # window -- that run doubles as the retry chance for a model that misread
    # its move). Skipped steps drop out of this cycle's contention so plan
    # requests and message debts are not starved behind a person's silence.
    prefetched_steps: dict[str, dict[str, Any]] = {}
    _remaining: list[dict[str, Any]] = []
    _idle_step_ids: list[str] = []
    for item in obligations:
        step_id = str(item.get("step_id") or "")
        deal_id = str(item.get("deal_id") or "")
        if (
            item.get("kind") == "deal_step"
            and deal_id
            and step_id in _IDLE_STEP_MEMO
        ):
            try:
                payload = resources.toll_bench.current_step(deal_id)
            except Exception as error:  # noqa: BLE001 - a probe must not kill the cycle
                _LOGGER.warning(
                    "current_step idle probe failed for deal %s: %s", deal_id, error
                )
                payload = None
            if payload is not None:
                prefetched_steps[deal_id] = payload
                if _deal_step_is_idle(step_id, payload):
                    _idle_step_ids.append(step_id)
                    continue
        _remaining.append(item)
    if _idle_step_ids:
        _LOGGER.warning(
            "Skipping %d idle deal step(s) waiting on the person; "
            "servicing %d remaining obligation(s)",
            len(_idle_step_ids),
            len(_remaining),
        )
    obligations = _remaining
    # Forget steps that left the attention feed (ended, approved, reassigned).
    _live_step_ids = {
        str(item.get("step_id") or "") for item in obligations
    } | set(_idle_step_ids)
    for _sid in [sid for sid in _IDLE_STEP_MEMO if sid not in _live_step_ids]:
        _IDLE_STEP_MEMO.pop(_sid, None)
    deal_obligation = next((item for item in obligations if item.get("kind") == "deal_step"), None)
    email_provider = resources.runtime.email_provider
    mail_client = getattr(email_provider, "client", None)
    if deal_obligation and hasattr(mail_client, "configure_send_context"):
        try:
            mail_client.configure_send_context(
                proposal_id=str(deal_obligation.get("proposal_id") or ""),
                step_id=str(deal_obligation.get("step_id") or ""),
            )
        except RuntimeError:
            # A pending email approval parked on a *different* deal step must not
            # freeze work on this one. Resolving the parked send is the person's
            # move; defer this deal step for this cycle instead of crashing the
            # whole watch iteration.
            _LOGGER.warning(
                "Deferring deal step %s: another step has a pending email approval",
                deal_obligation.get("step_id"),
            )
            obligations = [item for item in obligations if item is not deal_obligation]
            deal_obligation = None
    has_unanswered_message = any(item.get("kind") == "unanswered_message" for item in obligations)
    resumed_email = (
        mail_client.resume_pending_send()
        if not has_unanswered_message and hasattr(mail_client, "resume_pending_send")
        else None
    )
    if resumed_email and resumed_email.get("status") == "pending_human_approval":
        # Parked on a human: nothing changes at machine speed. Slow the loop
        # instead of spinning the attention/resume pair every interval.
        return {
            "ok": True,
            "reachability": reachability,
            "attention_count": len(obligations),
            "email": resumed_email,
            "retry_after_seconds": 60.0,
            "run": None,
        }
    # Per-obligation dispatch: hand the model ONE obligation with only the
    # instruction and tools that obligation needs. The watch loop returns for
    # the next obligation on its next cycle.
    obligation = _select_obligation(obligations)
    if obligation is None:
        # Every obligation was deferred (e.g. a lone deal step blocked on a
        # parked email send). Nothing to hand the model this cycle, so the
        # loop has no reason to come back at machine speed.
        return {
            "ok": True,
            "reachability": reachability,
            "attention_count": len(obligations),
            "retry_after_seconds": 60.0,
            "run": None,
        }
    kind = str(obligation.get("kind") or "")
    dispatch = _OBLIGATION_DISPATCH.get(kind)
    if dispatch is None:
        # Unranked/unknown kind: fall back to the full obligation instruction and
        # tool set so capability is never lost for a kind we did not special-case.
        instruction = (
            _DEAL_STEP_INSTRUCTION
            + " "
            + _FILE_INFORMED_PLAN_INSTRUCTION
            + " "
            + _UNANSWERED_MESSAGE_INSTRUCTION
            + " "
            + _FEEDBACK_RETURNED_INSTRUCTION
        )
        obligation_tools = (
            _OBLIGATION_DISPATCH["deal_step"]["tools"]
            | _OBLIGATION_DISPATCH["file_informed_plan"]["tools"]
            | _OBLIGATION_DISPATCH["unanswered_message"]["tools"]
            | _OBLIGATION_DISPATCH["feedback_returned"]["tools"]
            | {"toll_bench.guide", "human.request"}
        )
    else:
        instruction = dispatch["instruction"]
        obligation_tools = set(dispatch["tools"])
    identity = resources.agent_identity
    mode = identity.autonomy_mode if identity else AutonomyMode.AUTONOMOUS
    # H6: put the step's history right in front of the model. Step-scoped
    # obligations get the current step prefetched into the payload so the run
    # starts with the full step state instead of spending its first call (or
    # skipping) fetching it. Best-effort: on failure the field is null and the
    # instruction tells the model to fetch it itself.
    step_state = None
    if kind in ("deal_step", "unanswered_message"):
        deal_id = str(obligation.get("deal_id") or "")
        step_state = prefetched_steps.get(deal_id)
        if deal_id and step_state is None:
            try:
                step_state = resources.toll_bench.current_step(deal_id)
            except Exception as error:  # noqa: BLE001 - prefetch must not kill the cycle
                _LOGGER.warning(
                    "current_step prefetch failed for deal %s: %s", deal_id, error
                )
    goal = (
        instruction
        + _GOAL_COMMON_TAIL
        + "\n\n"
        + json.dumps(
            {
                "obligation": obligation,
                "current_step": step_state,
                "confirmed_email_send_receipt": resumed_email,
                "previous_attempt_failure": previous_failure,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    original_tools = resources.runtime.enabled_tools
    resources.runtime.enabled_tools = [name for name in original_tools if name in obligation_tools]
    meter = _dispatch_meter(kind, goal, resources.runtime.enabled_tools)
    try:
        result = resources.runtime.start(goal, mode)
    finally:
        resources.runtime.enabled_tools = original_tools
    if kind == "deal_step" and step_state is not None:
        # Remember what the model was shown. If the next fetch of this step is
        # identical, the run above made no move and the step is idle until the
        # payload changes or a pulse comes due. limit_reached counts: the model
        # spent its whole run over exactly this state and got nowhere, and an
        # identical re-run wanders identically at full price (a looping model
        # burned 20-iteration runs every couple of minutes on 2026-08-29).
        # A FAILED run records nothing: an adapter or API error is transient
        # and must retry at full cadence, not be mistaken for a judged wait.
        _step_id = str(obligation.get("step_id") or "")
        if _step_id and result.status.value in {"completed", "waiting", "limit_reached"}:
            _IDLE_STEP_MEMO[_step_id] = _deal_step_fingerprint(step_state)
    ok = result.status.value in {"completed", "waiting"}
    payload: dict[str, Any] = {
        "ok": ok,
        "reachability": reachability,
        "attention_count": len(obligations),
        "dispatch": meter,
        "run": _result_payload(result),
    }
    if _stalled:
        payload["stalled_obligations"] = _stalled
    if ok:
        _breaker_reset(obligation)
        return payload
    breaker = _breaker_record_failure(
        resources, obligation, _failure_signature(result.result), threshold=threshold
    )
    payload["breaker"] = breaker
    payload["retry_after_seconds"] = breaker["retry_after_seconds"]
    return payload


def _market_target_key(target: dict[str, Any]) -> tuple[str, str, str | None]:
    target_id = str(target.get("target_id") or "")
    target_round = target.get("round")
    round_value = str(target_round) if target_round is not None else None
    return market_target_key(target_id, round_value), target_id, round_value


def _market_freshness(target: dict[str, Any]) -> str:
    # A repost keeps the want's original posted_at; reposted_at is when the
    # current round opened. Sorting by bare posted_at buried every repost
    # under weeks of newer wants, so freshness is whichever is latest.
    return max(
        str(target.get("posted_at") or ""),
        str(target.get("reposted_at") or ""),
    )


def _market_scan_candidates(
    resources: Any,
) -> tuple[int, list[dict[str, Any]], list[tuple[str, str, str | None]]]:
    response = resources.toll_bench.list_targets()
    targets = list(response.get("targets") or [])
    provider = resources.toll_bench
    fleet = getattr(provider, "fleet", None)
    fleet_limit = int(getattr(provider, "fleet_proposal_limit", 4))
    identity = resources.agent_identity
    reviewed = (
        fleet.reviewed_target_keys(identity.id)
        if fleet is not None and identity is not None
        else set()
    )
    eligible = []
    for target in targets:
        target_key, target_id, round_value = _market_target_key(target)
        if not target_id or target.get("your_bid") is not None:
            continue
        if target_key in reviewed:
            continue
        if fleet is not None and fleet.proposal_count(target_id, round_value) >= fleet_limit:
            continue
        # Optional crowding limit (fleet.open_bid_limit, default off): skip
        # targets whose brief reports at least this many live bids via the
        # additive open_bid_count field. Absent field or unset limit -> no skip.
        open_bid_limit = getattr(provider, "open_bid_limit", None)
        server_open_bids = target.get("open_bid_count")
        if (
            open_bid_limit is not None
            and server_open_bids is not None
            and int(server_open_bids) >= int(open_bid_limit)
        ):
            continue
        eligible.append(target)
    eligible.sort(key=_market_freshness, reverse=True)
    selected = eligible[:MARKET_SCAN_CANDIDATE_LIMIT]
    summaries = [
        {
            "target_id": target.get("target_id"),
            "want": target.get("want"),
            "lane": target.get("lane"),
            "posted_at": target.get("posted_at"),
            "practice": bool(target.get("practice")),
            "timeline_days": target.get("timeline_days"),
            "frozen_probability": target.get("frozen_probability"),
            "round": target.get("round"),
            "reposted_at": target.get("reposted_at"),
            "open_bid_count": target.get("open_bid_count"),
            # Contract 2.44: the want names the blocks it cannot be delivered
            # without. It rides the open listing as well as the brief, so the
            # model knows before it fetches anything that this one needs a
            # meeting act and not an email.
            "required_blocks": target.get("required_blocks"),
        }
        for target in selected
    ]
    return len(targets), summaries, [_market_target_key(target) for target in selected]


def _process_market_opportunities(
    resources: Any,
    reachability: dict[str, Any],
    previous_failure: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """One model-powered pass over the open board.

    `dry_run` runs the whole planning cycle and stops at the door: the filing
    call validates the exact payload the model built and returns the bench's
    own answer instead of writing a bid. Nothing is filed, nothing is counted,
    and the plan itself comes back on `dry_run_plans` so a release can be
    checked against a live bench without spending an agent's one bid.
    """
    target_count, candidates, review_targets = _market_scan_candidates(resources)
    if not candidates:
        return {
            "ok": True,
            "reachability": reachability,
            "attention_count": 0,
            "market_scan": True,
            "open_target_count": target_count,
            "candidate_count": 0,
            "run": None,
        }
    identity = resources.agent_identity
    mode = identity.autonomy_mode if identity else AutonomyMode.AUTONOMOUS
    goal = (
        "Respond to the single open Toll Bench want below by making and submitting one concrete, "
        "honest proposal. Existing obligations were checked first and none are pending. Do not "
        "merely review or summarize the want. Call toll_bench.guide(topic='bidding'), read the "
        "target "
        "brief, read the current proposal schema, validate the exact final proposal, and submit it "
        "once with a stable idempotency key. Never bid on a target whose brief reports your_bid. "
        "Do not inspect targets outside this candidate set. Do not request human input. Save a "
        "compact checkpoint and call result.complete only after submission succeeds. If no honest "
        "executable proposal is possible or production refuses it, call result.fail with the exact "
        "blocker so the next cycle can retry with that context. ACT (rule 212): whenever the want "
        "needs something to leave the platform -- an email, an invitation, a calendar event -- the "
        "plan's execution step is YOURS (actor: agent, ask: APPROVE) and declares the acts on it: "
        "\"acts\": [{\"kind\": \"email\", \"to\": <who>, \"purpose\": <why>}]. You file the exact "
        "email as an act, the person approves it word for word, Book of Houses sends it from your "
        "mailbox. To arrange a TIME (a call, a meeting, a visit) the act is kind meeting, NOT "
        "an email, and the plan carries the calendar grant with it (rule 230). Step 1 connects "
        "the person's Google Calendar (a GRANT step). Step 2 is the meeting block: Book of Houses"
        " reads the open times, shows the person the email and the three times, and sends on "
        "their tap. Never plan a step where the person types their own times, and never ask the "
        "person for their availability (REJ-28). Declare the meeting act with the "
        "invitee, a "
        "duration, a window and a message you "
        "write to open the invite. Never email someone to ask their times. "
        "The person never sends anything: a step that asks "
        "them to Send, or a plan where "
        "every step is the person's and you do nothing yourself, is refused (REJ-26). Say in the "
        "pitch what you will do yourself. Never a separate compose step and never a separate "
        "confirm-it-was-sent step. THE FOUR QUESTIONS ARE TAPS (rules 168 and 170): every "
        "entry of finalist_questions[0] is a HAR block -- the same {id, format, title, "
        "config} shape a step's har_blocks carries -- and AT MOST TWO of the four may be a "
        "text box (short_answer, written_response, or a bare string). Four bare strings are "
        "refused REJ-15. A two-way question is single_choice with both answers spelled out; "
        "a yes/no is yes_no; several related facts are ONE structured_form with named "
        "fields; dates are date_time or schedule. Pre-fill the options from the brief. "
        "Approve, grant and payment formats are refused on a question. "
        "THE TEMPLATE IS A FORM, NOT A PLAN (contract 3.0, rule 228 amended). The brief "
        "carries plan_template (a BLANK skeleton: mechanics filled, every agent-owned "
        "word an empty string or null), block_templates ({kind: [steps]}, the catalog to "
        "pull from), bid_template and bid_template_notes (one line per blank -- that is "
        "your to-do list). Fill every blank you keep IN YOUR OWN WORDS; a step still "
        "carrying an empty title or outcome_promise is dropped before filing and nothing "
        "is written in its place. Never file the form as handed to you. required_blocks "
        "is [] and that means YOU decide which blocks this want needs. When you need one, "
        "pull it out of block_templates IN FULL and in its order -- a block that runs on "
        "the person's connection is TWO steps, the GRANT first (REJ-35) -- and do not "
        "rewrite a block step's title, outcome_promise or har_blocks, which the platform "
        "writes at signing. On a meeting act put the person's context in message, with no "
        "dates and no times in it, and leave `with` out unless the invitee's address is "
        "actually known; read a kind's fields with toll_bench.list_act_kinds. Before you "
        "file, call toll_bench.validate_proposal with this target_id: it is free, files "
        "nothing and returns EVERY problem at once with a plain-words fix. Fix what it "
        "names, then submit.\n\n"
        + json.dumps(
            {
                "candidate_targets": candidates,
                "previous_attempt_failure": previous_failure,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    original_tools = resources.runtime.enabled_tools
    original_submit = resources.toll_bench.submit_proposal
    proposal_filed = False

    def submit_at_most_one(*args, **kwargs):
        nonlocal proposal_filed
        if proposal_filed:
            return {
                "ok": False,
                "error": "market_scan_proposal_limit",
                "message": "This market scan already filed its one allowed proposal.",
            }
        response = original_submit(*args, **kwargs)
        if response.get("ok") and response.get("proposal_id"):
            proposal_filed = True
        return response

    dry_run_plans: list[dict[str, Any]] = []

    def validate_instead_of_filing(
        target_id: str, proposal: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        answer = resources.toll_bench.validate_proposal(proposal, target_id)
        dry_run_plans.append({"target_id": target_id, "proposal": proposal, "validation": answer})
        return {
            "ok": False,
            "error": "dry_run",
            "terminal": True,
            "validation": answer,
            "message": (
                "DRY RUN: nothing was filed. The plan was validated and recorded. "
                "Call result.complete now."
            ),
        }

    resources.runtime.enabled_tools = list(MARKET_SCAN_TOOLS)
    resources.toll_bench.submit_proposal = (
        validate_instead_of_filing if dry_run else submit_at_most_one
    )
    meter = _dispatch_meter("market_scan", goal, resources.runtime.enabled_tools)
    try:
        result = resources.runtime.start(goal, mode)
    finally:
        resources.toll_bench.submit_proposal = original_submit
        resources.runtime.enabled_tools = original_tools
    fleet = getattr(resources.toll_bench, "fleet", None)
    if (
        result.status.value == "completed"
        and proposal_filed
        and fleet is not None
        and identity is not None
    ):
        fleet.mark_targets_reviewed(agent_id=identity.id, targets=review_targets)
    return {
        "ok": result.status.value == "completed",
        "reachability": reachability,
        "attention_count": 0,
        "market_scan": True,
        "open_target_count": target_count,
        "candidate_count": len(candidates),
        "proposal_filed": proposal_filed,
        "dispatch": meter,
        "dry_run": bool(dry_run),
        "dry_run_plans": dry_run_plans,
        "run": _result_payload(result),
    }


# Worker-side wake state. Timers live in WAKE_TIMERS_NAMESPACE (written by the
# wake.set_timer tool); the inbound-mail cursor lives here. Both ride the same
# knowledge table as everything else, so they survive worker restarts.
_EMAIL_WAKE_NAMESPACE = "__email_wake__"


def _pending_wake_timers(store: Any) -> dict[str, Any]:
    try:
        timers = store.load_knowledge(WAKE_TIMERS_NAMESPACE)
    except Exception:  # noqa: BLE001 - a broken timer table must not kill the watch
        _LOGGER.warning("Reading wake timers failed", exc_info=True)
        return {}
    return timers if isinstance(timers, dict) else {}


def _clear_wake_timer(store: Any, run_id: str) -> None:
    # Re-read before writing: the resumed run may have parked itself again with
    # a fresh timer, and that fresh timer must survive this clear.
    timers = _pending_wake_timers(store)
    if run_id in timers:
        timers.pop(run_id)
        store.save_knowledge(WAKE_TIMERS_NAMESPACE, timers)


def _earliest_wake_at(resources: Any) -> float | None:
    store = getattr(resources, "store", None)
    if store is None:
        return None
    values = [
        float(entry.get("wake_at"))
        for entry in _pending_wake_timers(store).values()
        if isinstance(entry, dict) and entry.get("wake_at") is not None
    ]
    return min(values) if values else None


def _new_inbound_email_marker(resources: Any) -> str | None:
    """Return a marker when mail arrived since the last cycle, else None.

    Poll-bound, not push: this piggybacks on the existing watch cadence (one
    check per cycle through the provider's thread listing, which itself rides
    the ETag-cached proposals call), so a new message is noticed at most one
    poll interval after it lands. The cursor is the newest last_inbound_at
    seen; the first observation only baselines it so history never wakes
    anything.
    """
    store = getattr(resources, "store", None)
    provider = getattr(getattr(resources, "runtime", None), "email_provider", None)
    if store is None or provider is None:
        return None
    try:
        threads = provider.list(limit=50)
    except Exception:  # noqa: BLE001 - a mail hiccup must not kill the watch
        _LOGGER.warning("Inbound email check failed", exc_info=True)
        return None
    latest = max(
        (
            str(thread.get("last_inbound_at") or "")
            for thread in threads
            if isinstance(thread, dict)
        ),
        default="",
    )
    state = store.load_knowledge(_EMAIL_WAKE_NAMESPACE)
    cursor = state.get("cursor") if isinstance(state, dict) else None
    if cursor is None:
        store.save_knowledge(_EMAIL_WAKE_NAMESPACE, {"cursor": latest})
        return None
    if latest and latest > str(cursor):
        store.save_knowledge(_EMAIL_WAKE_NAMESPACE, {"cursor": latest})
        return latest
    return None


def _process_wakes(resources: Any) -> list[dict[str, Any]]:
    """Resume parked runs: due wake.set_timer timers, or new inbound mail.

    New inbound mail wakes every parked run (cause inbound_email) because a
    parked run is one that chose to wait, and the mail may be the reply it is
    waiting for; a due timer wakes only its own run (cause timer). A timer is
    cleared before its run is resumed, so a run that parks itself again keeps
    its fresh timer.
    """
    store = getattr(resources, "store", None)
    runtime = getattr(resources, "runtime", None)
    if store is None or runtime is None:
        return []
    inbound_marker = _new_inbound_email_marker(resources)
    timers = _pending_wake_timers(store)
    now = time.time()
    woken: list[dict[str, Any]] = []
    for run_id, entry in timers.items():
        if not isinstance(entry, dict):
            _clear_wake_timer(store, run_id)
            continue
        if inbound_marker is not None:
            cause = "inbound_email"
        elif entry.get("wake_at") is not None and now >= float(entry["wake_at"]):
            cause = "timer"
        else:
            continue
        note = entry.get("note")
        _clear_wake_timer(store, run_id)
        wake: dict[str, Any] = {"run_id": run_id, "cause": cause, "note": note}
        try:
            result = runtime.resume(run_id, cause=cause, note=note)
            wake["run"] = _result_payload(result)
        except Exception:  # noqa: BLE001 - one unresumable run must not kill the watch
            _LOGGER.exception("Waking run %s failed", run_id)
            wake["error"] = "wake_failed"
        woken.append(wake)
    return woken


def command_market_watch(arguments: argparse.Namespace) -> int:
    resources = build_runtime(arguments.config)
    stall_threshold = _configured_stall_threshold(arguments.config)
    previous_failure = None
    previous_scan_failure = None
    next_market_scan = 0.0
    scan_interval = max(float(getattr(arguments, "scan_interval", 300.0)), 0.0)
    bidding_enabled = not bool(getattr(arguments, "no_bid", False))
    dry_run = bool(getattr(arguments, "dry_run", False))
    try:
        while True:
            woken: list[dict[str, Any]] = []
            try:
                # Wake parked runs first: due timers and new inbound mail are
                # obligations of this worker, checked on the same poll cadence.
                woken = _process_wakes(resources)
                result = _process_market_attention(
                    resources,
                    arguments.wait,
                    previous_failure=previous_failure,
                    stall_threshold=stall_threshold,
                )
                # Agents look for new work ALWAYS, debt or not (Steven,
                # 2026-08-26). Obligations are serviced first in every cycle,
                # and the board scan runs on its own cadence regardless of what
                # the obligation side did -- including when it is parked on a
                # human action (payout onboarding, an email approval) or just
                # finished a deal step. The only requirement is that the bench
                # answered this cycle (reachability present).
                if (
                    bidding_enabled
                    and result.get("reachability")
                    and time.monotonic() >= next_market_scan
                ):
                    scan_result = _process_market_opportunities(
                        resources,
                        result.get("reachability") or {},
                        previous_failure=previous_scan_failure,
                        dry_run=dry_run,
                    )
                    next_market_scan = time.monotonic() + scan_interval
                    previous_scan_failure = (
                        None
                        if scan_result.get("ok")
                        else (
                            (scan_result.get("run") or {}).get("result")
                            or {"error": scan_result.get("error")}
                        )
                    )
                    if result.get("ok") and result.get("run") is None:
                        # The obligation side had nothing model-worthy this
                        # cycle; the scan is the cycle's story.
                        result = scan_result
                    else:
                        # Obligation work (or an obligation blocker) plus a
                        # scan in one cycle: keep the obligation result as the
                        # cycle's verdict and carry the scan alongside it.
                        result = {**result, "scan": scan_result}
            except BookOfHousesApiError as error:
                result = {
                    "ok": False,
                    "error": "book_of_houses_api_error",
                    "status": error.status,
                    "code": error.code,
                    "message": error.message,
                }
            except Exception as error:  # noqa: BLE001 - a watcher must survive one bad cycle
                _LOGGER.exception("Market watch iteration failed")
                result = {
                    "ok": False,
                    "error": "market_watch_iteration_failed",
                    "error_type": type(error).__name__,
                }
            if woken:
                result = {**result, "wakes": woken}
            if result.get("ok"):
                if result.get("market_scan") or result.get("run") is not None:
                    previous_failure = None
            else:
                run = result.get("run") or {}
                previous_failure = run.get("result") or {
                    "error": result.get("error"),
                    "error_type": result.get("error_type"),
                }
            _print(result)
            if arguments.once:
                return 0 if result.get("ok") else 2
            delay = (
                max(
                    arguments.interval,
                    float(result.get("retry_after_seconds") or 0.0),
                )
                if result.get("ok")
                else max(
                    arguments.interval,
                    float(result.get("retry_after_seconds") or 30.0),
                )
            )
            # Never sleep past a pending wake timer: sleep until whichever
            # comes first, the next poll or the earliest wake_at (floor 1s so
            # a due timer cannot busy-spin the loop).
            earliest_wake = _earliest_wake_at(resources)
            if earliest_wake is not None:
                delay = max(1.0, min(delay, earliest_wake - time.time()))
            time.sleep(delay)
    finally:
        resources.close()


def _channel(config_path: str) -> tuple[OperatorChannel, Any]:
    resources = build_runtime(config_path)
    return OperatorChannel(resources.store, resources.store), resources


def command_operator_observe(arguments: argparse.Namespace) -> int:
    channel, resources = _channel(arguments.config)
    try:
        _print(channel.observe(arguments.run_id))
        return 0
    finally:
        resources.close()


def command_operator_message(arguments: argparse.Namespace) -> int:
    channel, resources = _channel(arguments.config)
    try:
        channel.message(arguments.run_id, arguments.message)
        _print({"accepted": True, "run_id": arguments.run_id})
        return 0
    finally:
        resources.close()


def command_human_message(arguments: argparse.Namespace) -> int:
    resources = build_runtime(arguments.config)
    try:
        resources.runtime.add_human_input(arguments.run_id, arguments.message)
        _print({"accepted": True, "run_id": arguments.run_id})
        return 0
    finally:
        resources.close()


def command_bedrock_probe(arguments: argparse.Namespace) -> int:
    probe = BedrockProbe(region=arguments.region, profile_name=arguments.profile)
    results = probe.run(
        max_providers=arguments.max_providers,
        attempts_per_provider=arguments.attempts_per_provider,
        preferred_intelligences=arguments.prefer,
    )
    payload = {
        "identity": probe.identity(),
        "matrix": [result.to_dict() for result in results],
    }
    if arguments.output:
        output_path = Path(arguments.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2) + "\n")
    if arguments.write_configs:
        destination = Path(arguments.write_configs)
        destination.mkdir(parents=True, exist_ok=True)
        template = yaml.safe_load(default_config())
        for result in results:
            if (
                not result.invocation_successful
                or not result.tool_use_supported
                or not result.identifier
            ):
                continue
            config = dict(template)
            config["model"] = dict(
                template["model"],
                model_id=result.identifier,
                profile=arguments.profile,
            )
            name = result.intelligence.lower().replace(" ", "-") + ".yaml"
            (destination / name).write_text(yaml.safe_dump(config, sort_keys=False))
        payload["configs_written_to"] = str(destination.resolve())
    _print(payload)
    return 0 if any(result.invocation_successful for result in results) else 2


def command_bedrock_canary(arguments: argparse.Namespace) -> int:
    probe = BedrockProbe(region=arguments.region, profile_name=arguments.profile)
    probe_results = probe.run(
        max_providers=arguments.max_models,
        attempts_per_provider=arguments.attempts_per_provider,
        preferred_intelligences=arguments.prefer,
    )
    candidates = [
        result
        for result in probe_results
        if result.invocation_successful and result.tool_use_supported and result.identifier
    ][: arguments.max_models]
    data_dir = Path(arguments.data_directory).resolve()
    store = SQLiteStore(data_dir / "harness.sqlite3")
    artifacts = FilesystemArtifactStore(data_dir / "artifacts")
    runs = []
    for candidate in candidates:
        runtime = HarnessRuntime(
            model=BedrockModelAdapter(
                candidate.identifier,
                region=arguments.region,
                profile_name=arguments.profile,
            ),
            state_store=store,
            event_store=store,
            artifact_store=artifacts,
            tools=build_standard_registry(),
            enabled_tools=["state.save", "result.complete", "result.fail"],
            max_iterations=6,
        )
        result = runtime.start(
            "Call state.save with a compact checkpoint containing status='canary-ready' "
            "and your intelligence family, then call result.complete with a short confirmation."
        )
        actions = [
            event.payload
            for event in store.list_events(result.run_id)
            if event.kind == "tool.called"
        ]
        runs.append(
            {
                "intelligence": candidate.intelligence,
                "provider": candidate.provider,
                "identifier": candidate.identifier,
                **_result_payload(result),
                "actions": actions,
            }
        )
    payload = {
        "harness_version": __version__,
        "region": arguments.region,
        "autonomy_mode": "autonomous",
        "probe_matrix": [result.to_dict() for result in probe_results],
        "runs": runs,
    }
    if arguments.output:
        output_path = Path(arguments.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2, default=str) + "\n")
    _print(payload)
    return 0 if runs and all(run["status"] == "completed" for run in runs) else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="toll-harness")
    parser.add_argument("--version", action="version", version=__version__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    initialize = subcommands.add_parser("init", help="Create a local agent configuration")
    initialize.add_argument("directory", nargs="?", default=".")
    initialize.add_argument("--force", action="store_true")
    initialize.add_argument("--resume", action="store_true")
    initialize.add_argument(
        "--yes", action="store_true", help="Approve registration non-interactively"
    )
    initialize.add_argument(
        "--no-worker",
        action="store_true",
        help="Do not install the persistent Toll Bench market worker",
    )
    initialize.set_defaults(handler=command_init)

    doctor = subcommands.add_parser("doctor", help="Check configuration and provider access")
    doctor.add_argument("config", nargs="?")
    doctor.add_argument("--region")
    doctor.add_argument("--profile")
    doctor.set_defaults(handler=command_doctor)

    run = subcommands.add_parser("run", help="Start or resume an intelligence run")
    run.add_argument("config")
    run.add_argument("--goal")
    run.add_argument("--resume", metavar="RUN_ID")
    run.set_defaults(handler=command_run)

    market = subcommands.add_parser("market", help="Connect to and service Toll Bench")
    market_subcommands = market.add_subparsers(dest="market_command", required=True)
    connect = market_subcommands.add_parser(
        "connect", help="Complete and verify the agent reachability handshake"
    )
    connect.add_argument("config")
    connect.set_defaults(handler=command_market_connect)
    watch = market_subcommands.add_parser(
        "watch", help="Process obligations and scan bounded sets of open Toll Bench wants"
    )
    watch.add_argument("config")
    watch.add_argument("--once", action="store_true")
    watch.add_argument("--wait", type=int, default=20, choices=range(0, 21), metavar="0..20")
    watch.add_argument("--interval", type=float, default=2.0)
    watch.add_argument(
        "--scan-interval",
        type=float,
        default=300.0,
        help="Minimum seconds between model-powered open-want scans",
    )
    watch.add_argument("--no-bid", action="store_true", help="Service obligations without bidding")
    watch.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan and validate at the bench's door, but file nothing",
    )
    watch.set_defaults(handler=command_market_watch)

    operator = subcommands.add_parser("operator", help="Use the operator channel")
    operator_subcommands = operator.add_subparsers(dest="operator_command", required=True)
    observe = operator_subcommands.add_parser("observe")
    observe.add_argument("config")
    observe.add_argument("run_id")
    observe.set_defaults(handler=command_operator_observe)
    message = operator_subcommands.add_parser("message")
    message.add_argument("config")
    message.add_argument("run_id")
    message.add_argument("message")
    message.set_defaults(handler=command_operator_message)

    human = subcommands.add_parser("human", help="Add requested end-user input")
    human_subcommands = human.add_subparsers(dest="human_command", required=True)
    human_message = human_subcommands.add_parser("message")
    human_message.add_argument("config")
    human_message.add_argument("run_id")
    human_message.add_argument("message")
    human_message.set_defaults(handler=command_human_message)

    bedrock = subcommands.add_parser("bedrock", help="Inspect the Bedrock reference lab")
    bedrock_subcommands = bedrock.add_subparsers(dest="bedrock_command", required=True)
    probe = bedrock_subcommands.add_parser("probe")
    probe.add_argument("--region", default="us-west-2")
    probe.add_argument("--profile")
    probe.add_argument("--max-providers", type=int, default=10)
    probe.add_argument("--attempts-per-provider", type=int, default=5)
    probe.add_argument("--prefer", action="append", default=[])
    probe.add_argument("--output")
    probe.add_argument("--write-configs")
    probe.set_defaults(handler=command_bedrock_probe)
    canary = bedrock_subcommands.add_parser(
        "canary", help="Probe and run the same state/completion task across intelligences"
    )
    canary.add_argument("--region", default="us-west-2")
    canary.add_argument("--profile")
    canary.add_argument("--max-models", type=int, default=10)
    canary.add_argument("--attempts-per-provider", type=int, default=5)
    canary.add_argument("--prefer", action="append", default=[])
    canary.add_argument("--data-directory", default=".toll-harness/bedrock-lab")
    canary.add_argument("--output")
    canary.set_defaults(handler=command_bedrock_canary)
    return parser


def main() -> None:
    parser = build_parser()
    arguments = parser.parse_args()
    try:
        raise SystemExit(arguments.handler(arguments))
    except (FileNotFoundError, KeyError, PermissionError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
