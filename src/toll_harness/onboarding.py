from __future__ import annotations

import json
import os
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from toll_harness import __version__
from toll_harness.config import (
    BENCH_DOOR_TOOLS,
    GOAL_CHECK_TOOLS,
    PACK_INPUT_TOOLS,
    PAID_PACK_TOOLS,
    STEP_DOOR_TOOLS,
    approach_from_config,
)
from toll_harness.fleet import default_fleet_database
from toll_harness.storage.secrets import FileSecretStore
from toll_harness.toll_bench.api import BookOfHousesApiClient, BookOfHousesApiError

HARNESS_LABEL = f"Toll Harness {__version__}"

WAITING_FOR_COMPANY_VERIFICATION = "WAITING_FOR_COMPANY_VERIFICATION"
READY = "READY"
VALIDATED = "VALIDATED"
LOCAL_CONFIGURED = "LOCAL_CONFIGURED"
TOKEN_SECRET_NAME = "book_of_houses_agent_token"
# `init --registered` reads the agent's own bearer token from this variable in
# its own process environment. No human holds, pastes or is prompted for it.
AGENT_TOKEN_ENV = "TOLL_HARNESS_AGENT_TOKEN"
MODEL_ADAPTERS = ("bedrock", "anthropic", "openai", "claude_code", "codex", "external")
DEFAULT_BASE_URL = "https://bookofhouses.com"
# WHICH BENCH INIT TALKS TO (0.76.1). TOLL_HARNESS_BASE_URL in the process
# environment, else DEFAULT_BASE_URL. Init reads it once and writes it to
# `toll_bench.base_url` in agent.yaml, the key the worker, `--resume` and the
# update check already read, so one name carries it from init to every run.
# A checker pointing the CLI at staging had to monkeypatch the constant.
BASE_URL_ENV = "TOLL_HARNESS_BASE_URL"


def bench_base_url() -> str:
    """The bench init talks to: TOLL_HARNESS_BASE_URL, else DEFAULT_BASE_URL."""
    return (os.environ.get(BASE_URL_ENV) or "").strip().rstrip("/") or DEFAULT_BASE_URL


# The intelligence BRAND is the name its maker publishes (Fable, Astra, Muse...).
# The bench ranks the company fielding an agent together with that brand; the
# exact model version and the harness are recorded on the system record and
# never ranked. The brand is free text, 1 to 40 characters.
INTELLIGENCE_BRAND_MAX = 40
def stated_intelligence(config: dict[str, Any]) -> tuple[str, str | None] | None:
    """The agent's (brand, maker) as agent.yaml states them, or None.

    An agent.yaml written before the brand question has no
    `agent.intelligence_brand`; it still loads and runs, and registers with
    no brand for the bench's validation to name (0.74.0: no guessing from
    the model id).
    """
    agent = config.get("agent") or {}
    brand = str(agent.get("intelligence_brand") or "").strip()
    maker = str(agent.get("intelligence_maker") or "").strip() or None
    return (brand, maker) if brand else None

STANDARD_TOOLS = [
    "state.load",
    "state.save",
    "email.list",
    "email.read",
    "email.send",
    "email.reply",
    "web.search",
    "web.fetch",
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
    "human.request",
    "result.complete",
    "result.fail",
]

TOLL_BENCH_TOOLS = [
    "toll_bench.protocol",
    "toll_bench.guide",
    "toll_bench.proposal_schema",
    "toll_bench.capability_taxonomy",
    "toll_bench.status",
    "toll_bench.ensure_reachable",
    "toll_bench.attention",
    "toll_bench.events",
    "toll_bench.list_targets",
    "toll_bench.read_brief",
    "toll_bench.list_proposals",
    "toll_bench.validate_proposal",
    "toll_bench.submit_proposal",
    "toll_bench.withdraw_proposal",
    "toll_bench.read_finalist_answers",
    "toll_bench.submit_informed_plan",
    "toll_bench.current_step",
    "toll_bench.reply_step_message",
    "toll_bench.post_check_in",
    "toll_bench.file_outcome",
    # RULE 230: the two doors that hand back bytes.
    "toll_bench.deliver_file",
    "toll_bench.deliver_hosted_file",
] + STEP_DOOR_TOOLS + GOAL_CHECK_TOOLS + PACK_INPUT_TOOLS + PAID_PACK_TOOLS + BENCH_DOOR_TOOLS


@dataclass(frozen=True)
class InitAnswers:
    agent_name: str
    intelligence: str
    model_id: str
    company: str
    mode: str
    aws_profile: str | None
    aws_region: str
    connect_toll_bench: bool
    use_book_of_houses_email: bool
    company_url: str | None = None
    responsible_legal_name: str | None = None
    responsible_jurisdiction: str | None = None
    verification_recipient: str | None = None
    # bedrock | anthropic | openai | claude_code | codex | external
    model_adapter: str = "bedrock"
    # Pasted at init for the anthropic/openai adapters; written straight into
    # the agent's isolated SecretStore, never into agent.yaml or onboarding
    # state. The subscription rails (claude_code, codex) never carry a key.
    model_api_key: str | None = None
    # The external adapter's command line, already split into argv parts.
    model_command: tuple[str, ...] | None = None
    # An OpenAI-compatible endpoint for the openai adapter (model.base_url,
    # read since 0.68.0); None is the SDK's own default.
    model_base_url: str | None = None
    # True for `init --registered`: the agent already entered at the bench's
    # door and holds its token, so no registration details are collected.
    registered: bool = False
    # The intelligence brand its maker publishes (Fable, Astra, Muse...) and,
    # optionally, the maker. None when the operator stated none.
    intelligence_brand: str | None = None
    intelligence_maker: str | None = None
    # THE AGENT'S DEFAULT APPROACH (0.57.0, optional): one stop per dial
    # ({risk, finish, path}, any of them may be left out), in the bench's own
    # words. None means no default: the model picks per want.
    approach: dict[str, str] | None = None


def _atomic_text(path: Path, value: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, mode)
    finally:
        if temporary.exists():
            temporary.unlink()


def save_config(path: Path, config: dict[str, Any]) -> None:
    _atomic_text(path, yaml.safe_dump(config, sort_keys=False), 0o600)


def load_config(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("Agent configuration must be a YAML object")
    return value


def data_directory(config_path: Path, config: dict[str, Any]) -> Path:
    directory = config.get("storage", {}).get("directory", ".toll-harness")
    return (config_path.parent / directory).resolve()


def onboarding_path(config_path: Path, config: dict[str, Any]) -> Path:
    return data_directory(config_path, config) / "onboarding.json"


def secret_store(config_path: Path, config: dict[str, Any]) -> FileSecretStore:
    configured = config.get("secrets", {}).get("directory", "secrets")
    directory = (config_path.parent / configured).resolve()
    expected_root = data_directory(config_path, config)
    if directory != expected_root and expected_root not in directory.parents:
        raise ValueError("Permanent agent secrets must remain inside its isolated storage")
    return FileSecretStore(directory)


def load_onboarding(config_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    path = onboarding_path(config_path, config)
    if not path.exists():
        return {"version": 1, "status": LOCAL_CONFIGURED}
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("Unsupported onboarding state")
    return value


def save_onboarding(config_path: Path, config: dict[str, Any], state: dict[str, Any]) -> None:
    safe = dict(state)
    for forbidden in ("rest_token", "token", "credential", "secret"):
        if forbidden in safe:
            raise ValueError("Secrets cannot be stored in onboarding state")
    encoded = json.dumps(safe, indent=2, sort_keys=True) + "\n"
    if "bookofhouses_agent_" in encoded:
        raise ValueError("Agent credentials cannot be stored in onboarding state")
    _atomic_text(
        onboarding_path(config_path, config),
        encoded,
        0o600,
    )


_API_KEY_SECRET_NAMES = {"anthropic": "anthropic_api_key", "openai": "openai_api_key"}


def _model_block(answers: InitAnswers) -> dict[str, Any]:
    adapter = answers.model_adapter
    if adapter == "bedrock":
        return {
            "adapter": "bedrock",
            "profile": answers.aws_profile,
            "region": answers.aws_region,
            "model_id": answers.model_id,
            "max_tokens": 2048,
            "temperature": 0,
        }
    if adapter in _API_KEY_SECRET_NAMES:
        block: dict[str, Any] = {
            "adapter": adapter,
            "model_id": answers.model_id,
            "max_tokens": 2048,
            "api_key_secret": _API_KEY_SECRET_NAMES[adapter],
        }
        if answers.model_base_url:
            block["base_url"] = answers.model_base_url
        return block
    if adapter in ("claude_code", "codex"):
        # Subscription OAuth rails: the vendor CLI owns the login, so the
        # configuration carries no credential reference at all.
        return {
            "adapter": adapter,
            "model_id": answers.model_id or None,
            "timeout_seconds": 600,
        }
    if adapter == "external":
        command = [str(part) for part in (answers.model_command or ()) if str(part)]
        if not command:
            raise ValueError("The external adapter needs model.command (a command line)")
        return {
            "adapter": "external",
            "command": command,
            "model_id": answers.model_id or None,
            "timeout_seconds": 600,
        }
    raise ValueError("model_adapter must be one of: " + ", ".join(MODEL_ADAPTERS))


def create_configuration(destination: Path, answers: InitAnswers) -> Path:
    if answers.mode not in {"Autonomous", "Supported"}:
        raise ValueError("Operating mode must be exactly Autonomous or Supported")
    if answers.model_api_key and answers.model_adapter not in _API_KEY_SECRET_NAMES:
        raise ValueError("Only the anthropic and openai adapters take a pasted API key")
    if answers.model_command and answers.model_adapter != "external":
        raise ValueError("Only the external adapter takes a model command")
    if answers.model_base_url and answers.model_adapter != "openai":
        raise ValueError("Only the openai adapter takes a base URL")
    if answers.registered and not answers.connect_toll_bench:
        raise ValueError("A registered agent is a connected agent")
    if answers.connect_toll_bench and not answers.registered and not all(
        (
            answers.company_url,
            answers.responsible_legal_name,
            answers.responsible_jurisdiction,
            answers.verification_recipient,
        )
    ):
        raise ValueError("Connected onboarding requires company and responsible-party details")
    brand = (answers.intelligence_brand or "").strip() or None
    if brand and len(brand) > INTELLIGENCE_BRAND_MAX:
        raise ValueError(
            f"The intelligence brand is {INTELLIGENCE_BRAND_MAX} characters or fewer"
        )
    maker = (answers.intelligence_maker or "").strip() or None
    # The default approach is written as given; the bench judges its words.
    approach = approach_from_config({"strategy": {"approach": answers.approach}})
    agent_id = str(uuid.uuid4())
    destination.mkdir(parents=True, exist_ok=True)
    config_path = destination / "agent.yaml"
    local_root = f".toll-harness/{agent_id}"
    email_enabled = answers.connect_toll_bench and answers.use_book_of_houses_email
    config: dict[str, Any] = {
        "version": 1,
        "agent": {
            "permanent": True,
            "id": agent_id,
            "name": answers.agent_name,
            "intelligence": answers.intelligence,
            "intelligence_brand": brand,
            "intelligence_maker": maker,
            "company": answers.company,
            "mode": answers.mode,
            "harness": HARNESS_LABEL,
        },
        "benchmark": {
            "intelligence": answers.intelligence,
            "harness": HARNESS_LABEL,
            "company": answers.company,
            "autonomy": answers.mode.upper(),
        },
        "model": _model_block(answers),
        "runtime": {
            "autonomy": answers.mode.lower(),
            "knowledge_namespace": agent_id,
            "max_iterations": 20,
            "tools": list(STANDARD_TOOLS)
            + (list(TOLL_BENCH_TOOLS) if answers.connect_toll_bench else []),
        },
        "storage": {"directory": local_root},
        "secrets": {
            "provider": "file",
            "directory": f"{local_root}/secrets",
        },
        "providers": {
            "web": "basic",
            # The AgentCore browser assumes AWS credentials; every other rail
            # gets no browser by default (operators can flip to playwright).
            "browser": "agentcore" if answers.model_adapter == "bedrock" else "disabled",
            "browser_headless": True,
            "email": "book_of_houses" if email_enabled else "disabled",
        },
        "worker": {"enabled": answers.connect_toll_bench},
        "fleet": {
            "enabled": True,
            "database": str(default_fleet_database()),
        },
        "email": {
            "provider": "book_of_houses" if email_enabled else "disabled",
            "status": "pending_provisioning" if email_enabled else "ineligible",
            "verification_recipient": answers.verification_recipient,
            "address": None,
            "token_secret": TOKEN_SECRET_NAME if email_enabled else None,
        },
        "toll_bench": {
            "connected": answers.connect_toll_bench,
            "base_url": bench_base_url(),
            "status": LOCAL_CONFIGURED if answers.connect_toll_bench else "DISCONNECTED",
            "token_secret": TOKEN_SECRET_NAME if answers.connect_toll_bench else None,
            "company_url": answers.company_url,
            "responsible_party": (
                {
                    "legal_name": answers.responsible_legal_name,
                    "jurisdiction": answers.responsible_jurisdiction,
                    "contact_ref": answers.verification_recipient,
                }
                if answers.connect_toll_bench and not answers.registered
                else None
            ),
            "idempotency_key": f"toll-harness-register-{agent_id}",
            "maker_id": None,
            "registry_no": None,
        },
    }
    if approach:
        # Written only when the operator set a dial: an agent.yaml without
        # `strategy` has no default, and 0.56 and older ignore the key.
        config["strategy"] = {"approach": approach}
    save_config(config_path, config)
    data_directory(config_path, config).mkdir(parents=True, exist_ok=True)
    secrets_store = secret_store(config_path, config)
    if answers.model_api_key:
        # The pasted key lands only in the owner-only SecretStore; agent.yaml
        # carries just the secret NAME (model.api_key_secret).
        secrets_store.set(_API_KEY_SECRET_NAMES[answers.model_adapter], answers.model_api_key)
    save_onboarding(config_path, config, {"version": 1, "status": LOCAL_CONFIGURED})
    return config_path


def registration_payload(config: dict[str, Any], protocol: dict[str, Any]) -> dict[str, Any]:
    agent = config["agent"]
    model = config["model"]
    toll_bench = config["toll_bench"]
    responsible = toll_bench.get("responsible_party") or {}
    # An agent reached by its own command stores nothing about a model (0.76.0),
    # and the bench needs a model named at registration: the agent's own name
    # for its intelligence is that answer (0.76.2), never a guess of ours.
    model_name = model.get("model_id") or agent["intelligence"]
    base_model = {
        "provider": agent["intelligence"],
        "model": model_name,
        "version": agent["intelligence"],
    }
    # The board ranks (company, intelligence brand); the model version and the
    # harness ride the system record, recorded and never ranked.
    intelligence: dict[str, str] | None = None
    stated = stated_intelligence(config)
    if stated is not None:
        brand, maker = stated
        intelligence = {"brand": brand}
        base_model["brand"] = brand
        if maker:
            intelligence["maker"] = maker
            base_model["maker"] = maker
    payload: dict[str, Any] = {
        "handle": agent["name"],
        "skills": (
            "Autonomous web research, browser interaction, email, file work, stateful task "
            "execution, and completion reporting through Toll Harness standard capabilities."
        ),
        "disclosure": {
            "is_ai": True,
            "model": model_name,
            "operator_label": agent["company"],
            "operator_type": "company",
            "company_url": toll_bench["company_url"],
        },
    }
    if intelligence is not None:
        payload["intelligence"] = intelligence
    # THE DEFAULT APPROACH (contract 4.1.3, optional): the agent's standing
    # style, the fallback for a proposal that carries none, sent as written.
    # The register door judges it (0.74.0).
    approach = approach_from_config(config)
    if approach:
        payload["approach"] = approach
    return {
        **payload,
        "system_record": {
            "base_models": [base_model],
            "autonomy": ("fully_autonomous" if agent["mode"] == "Autonomous" else "human_assisted"),
            "harness": "Toll Harness",
            "harness_version": __version__,
            "label": f"{agent['name']} {HARNESS_LABEL}",
        },
        "responsible_party": responsible,
        "rules": {
            "accepted": True,
            "version_hash": protocol["rules_version_hash"],
        },
    }


def _save_connected_metadata(
    config_path: Path,
    config: dict[str, Any],
    *,
    status: str,
    maker_id: str | None = None,
    registry_no: str | None = None,
) -> None:
    toll_bench = config["toll_bench"]
    toll_bench["status"] = status
    if maker_id:
        toll_bench["maker_id"] = maker_id
    if registry_no:
        toll_bench["registry_no"] = registry_no
    save_config(config_path, config)


def intelligence_on_record(me: dict[str, Any] | None) -> dict[str, str | None]:
    """The intelligence the agent's own record names, read off GET /api/bench/me.

    The agent named it when it registered (`intelligence.brand`, and the
    models on `system_record.base_models`); the bench keeps the brand and
    maker on the record's base_models entries, so /me carries them there. A
    top-level `intelligence` on /me, when the bench sends one, is read first.

    Returns {name, brand, maker, provider, model}. `name` is the brand, else
    the model, else the provider, else None: the name the record gives, taken
    as written. Nothing here maps, corrects or second-guesses it; an agent
    that is the intelligence itself (its own program, under its own name) is
    read exactly like any other.
    """
    found: dict[str, str | None] = {
        "brand": None,
        "maker": None,
        "provider": None,
        "model": None,
    }

    def take(key: str, value: Any) -> None:
        text = " ".join(str(value or "").split())
        if text and not found[key]:
            found[key] = text

    me = me if isinstance(me, dict) else {}
    top = me.get("intelligence")
    if isinstance(top, str):
        take("brand", top)
    elif isinstance(top, dict):
        take("brand", top.get("brand"))
        take("maker", top.get("maker"))
    record = me.get("system_record")
    base_models = record.get("base_models") if isinstance(record, dict) else None
    for entry in base_models if isinstance(base_models, list) else []:
        if isinstance(entry, dict):
            take("brand", entry.get("brand"))
            take("maker", entry.get("maker"))
            take("provider", entry.get("provider"))
            take("model", entry.get("model"))
        elif isinstance(entry, str):
            take("model", entry)
    return {
        "name": found["brand"] or found["model"] or found["provider"],
        **found,
    }


def read_own_record(token: str, *, api: BookOfHousesApiClient | None = None) -> dict[str, Any]:
    """GET /api/bench/me with the agent's own token, before anything is written.

    `init --registered` reads it first so it can say back the intelligence the
    agent named at registration instead of asking for one. A refusal or a
    bench that does not answer raises BookOfHousesApiError.
    """
    token = (token or "").strip()
    if not token:
        raise ValueError(f"{AGENT_TOKEN_ENV} is empty")
    public_api = api or BookOfHousesApiClient(base_url=bench_base_url())
    return public_api.authenticated(token).me()


COMPANY_QUESTION = "Which company fields this agent?"
COMPANY_MISSING = (
    "The bench's attribution names no company for this agent and none was given; the "
    "agent's configuration needs the company that fields it."
)


def company_from_attribution(api: Any) -> str | None:
    """The company the bench records for the agent, or None.

    GET /api/bench/me/attribution carries `attribution.operator_name`, the
    company the agent registered as fielding it (disclosure.operator_label).
    A failed call or an empty field is None; the caller then asks.
    """
    try:
        body = api.attribution()
    except BookOfHousesApiError:
        return None
    attribution = (body or {}).get("attribution") or {}
    return str(attribution.get("operator_name") or "").strip() or None


def connect_registered_agent(
    config_path: str | Path,
    token: str,
    *,
    api: BookOfHousesApiClient | None = None,
    ask_company: Callable[[], str] | None = None,
) -> dict[str, Any]:
    """Connect a configuration to an agent that already registered at the door.

    The token goes straight into the owner-only secret store (never into
    agent.yaml or onboarding state), /me says who it is, and the maker_id and
    registry number are recorded with the same status a fresh registration
    leaves. The company comes from the bench's attribution
    (`attribution.operator_name`), else from `ask_company` (init asks
    COMPANY_QUESTION once, only when that call fails or the field is empty);
    the agent identity needs it, so a configuration is never left with an
    empty company. The rest
    (reachability, mailbox, company contact) is the ordinary connected
    onboarding, which never files a second registration once a maker_id is
    known.
    """
    token = (token or "").strip()
    if not token:
        raise ValueError(f"{AGENT_TOKEN_ENV} is empty")
    path = Path(config_path).resolve()
    config = load_config(path)
    toll_bench = config.get("toll_bench") or {}
    if not toll_bench.get("connected"):
        raise ValueError("This configuration is not connected to Toll Bench")
    token_name = toll_bench.get("token_secret") or TOKEN_SECRET_NAME
    secret_store(path, config).set(token_name, token)
    public_api = api or BookOfHousesApiClient(base_url=toll_bench["base_url"])
    agent_api = public_api.authenticated(token)
    me = agent_api.me()
    who = me.get("agent") or {}
    maker_id = who.get("maker_id")
    if not maker_id:
        raise RuntimeError("The agent token authenticated but the bench returned no maker_id")
    registry_no = who.get("registry_no")
    handle = str(who.get("handle") or "").strip()
    if handle:
        config["agent"]["name"] = handle
    company = (
        company_from_attribution(agent_api)
        or str(config["agent"].get("company") or "").strip()
    )
    if not company and ask_company is not None:
        company = str(ask_company() or "").strip()
    if not company:
        raise ValueError(COMPANY_MISSING)
    # The benchmark block must match the agent identity exactly.
    config["agent"]["company"] = company
    config.setdefault("benchmark", {})["company"] = company
    state = load_onboarding(path, config)
    state.update(
        {
            "status": WAITING_FOR_COMPANY_VERIFICATION,
            "maker_id": maker_id,
            "registry_no": registry_no,
            "token_secret": token_name,
            "connected_by": "registered",
        }
    )
    save_onboarding(path, config, state)
    _save_connected_metadata(
        path,
        config,
        status=WAITING_FOR_COMPANY_VERIFICATION,
        maker_id=maker_id,
        registry_no=registry_no,
    )
    return advance_connected_onboarding(path, approve_registration=False, api=public_api)


# NO COMPANY MAILBOX (0.76.1). The bench gives a mailbox only to an agent a
# company with a URL fields; for any other agent (operator_type person, which
# the quickstart names as valid) GET /api/agent-email/mailbox answers 403
# COMPANY_URL_REQUIRED. Init let that escape as a traceback after the pings
# were already acked (a checker's live `init --registered` for a person-run
# agent, 2026-11-10). A 4xx on that probe now means "no company mailbox":
# the email lane is switched off in agent.yaml and init goes on.
NO_COMPANY_MAILBOX = (
    "No company mailbox for this agent; deal email still rides the bench's per-deal thread."
)


def _company_mailbox(api: Any) -> dict[str, Any] | None:
    """The agent's company mailbox, or None when the bench says it has none.

    Any 4xx on the mailbox probe (403 COMPANY_URL_REQUIRED for an agent no
    company fields) is None. A 5xx or no answer still raises: that is the
    bench being down, not an answer about the agent.
    """
    try:
        response = api.mailbox()
    except BookOfHousesApiError as error:
        if 400 <= int(error.status or 0) < 500:
            return None
        raise
    return (response or {}).get("mailbox") or {}


def _record_no_company_mailbox(config: dict[str, Any], state: dict[str, Any]) -> None:
    """Switch the email lane off: the keys build_runtime reads to open it."""
    config.setdefault("providers", {})["email"] = "disabled"
    config.setdefault("email", {}).update(
        {"provider": "disabled", "status": "ineligible", "address": None, "token_secret": None}
    )
    state["mailbox"] = None
    state["company_mailbox"] = False


def advance_connected_onboarding(
    config_path: str | Path,
    *,
    approve_registration: bool,
    api: BookOfHousesApiClient | None = None,
) -> dict[str, Any]:
    path = Path(config_path).resolve()
    config = load_config(path)
    toll_bench = config.get("toll_bench") or {}
    if not toll_bench.get("connected"):
        return {"status": "DISCONNECTED", "config": str(path)}
    store = secret_store(path, config)
    state = load_onboarding(path, config)
    public_api = api or BookOfHousesApiClient(base_url=toll_bench["base_url"])
    protocol = public_api.protocol()
    state["protocol"] = {
        "protocol_version": protocol.get("protocol_version"),
        "contract_version": protocol.get("contract_version"),
        "rules_version_hash": protocol.get("rules_version_hash"),
    }

    token_name = toll_bench.get("token_secret") or TOKEN_SECRET_NAME
    token = store.get(token_name)
    maker_id = state.get("maker_id") or toll_bench.get("maker_id")
    if token and not maker_id:
        # Bring-your-own-token: the agent registered outside this harness (raw
        # HTTP, another tool) and holds only the bearer token. The bench needs
        # nothing else; ask it who the token is and record the answer instead
        # of registering a second identity.
        me = public_api.authenticated(token).me()
        who = me.get("agent") or {}
        maker_id = who.get("maker_id")
        if not maker_id:
            raise RuntimeError(
                "The stored agent token authenticated but production returned no maker_id"
            )
        state.update({"maker_id": maker_id, "registry_no": who.get("registry_no")})
        save_onboarding(path, config, state)
        _save_connected_metadata(
            path, config, status=toll_bench.get("status") or LOCAL_CONFIGURED,
            maker_id=maker_id, registry_no=who.get("registry_no"),
        )
    if not maker_id:
        payload = registration_payload(config, protocol)
        validation = public_api.validate_registration(payload)
        if not validation.get("ok"):
            state["status"] = "VALIDATION_FAILED"
            state["validation"] = {
                "problem_count": validation.get("problem_count"),
                "problems": validation.get("problems") or [],
            }
            save_onboarding(path, config, state)
            return {"status": state["status"], **state["validation"]}
        state["status"] = VALIDATED
        state["validation"] = {"problem_count": 0, "problems": []}
        save_onboarding(path, config, state)
        if not approve_registration:
            _save_connected_metadata(path, config, status=VALIDATED)
            return {"status": VALIDATED, "validation": state["validation"]}

        registration = public_api.register(payload, toll_bench["idempotency_key"])
        if "approach" in payload:
            state["approach"] = registration.get("approach") or payload["approach"]
        returned_token = registration.pop("rest_token", None)
        if returned_token:
            store.set(token_name, returned_token)
            token = returned_token
        if not token:
            raise RuntimeError(
                "Registration completed but its one-time agent token is unavailable; use the "
                "production recovery flow before resuming"
            )
        mailbox = registration.get("email_mailbox") or {}
        maker_id = registration.get("maker_id")
        if not maker_id or not mailbox.get("address"):
            raise RuntimeError("Production registration did not return an agent mailbox")
        state.update(
            {
                "status": WAITING_FOR_COMPANY_VERIFICATION,
                "maker_id": maker_id,
                "registry_no": registration.get("registry_no"),
                "mailbox": mailbox,
                "verification_recipient": config["email"]["verification_recipient"],
                "token_secret": token_name,
            }
        )
        save_onboarding(path, config, state)
        _save_connected_metadata(
            path,
            config,
            status=WAITING_FOR_COMPANY_VERIFICATION,
            maker_id=maker_id,
            registry_no=registration.get("registry_no"),
        )

    authenticated = public_api.authenticated(token, maker_id)
    from toll_harness.toll_bench.book_of_houses import BookOfHousesTollBenchProvider

    reachability = BookOfHousesTollBenchProvider(authenticated).ensure_reachable()
    state["reachability"] = reachability
    me = authenticated.me()
    company_mailbox = _company_mailbox(authenticated)
    no_mailbox = company_mailbox is None
    if no_mailbox:
        _record_no_company_mailbox(config, state)
        mailbox: dict[str, Any] = {}
    else:
        mailbox = company_mailbox or state.get("mailbox") or {}
        state["mailbox"] = mailbox
    # Said on every answer below when there is none, so init can print it.
    said = {"company_mailbox": False} if no_mailbox else {}
    contact = me.get("responsible_party_contact") or {}
    state["confirmation_sent_at"] = contact.get("sent_at")
    if not contact.get("confirmed"):
        state["status"] = WAITING_FOR_COMPANY_VERIFICATION
        save_onboarding(path, config, state)
        _save_connected_metadata(
            path,
            config,
            status=WAITING_FOR_COMPANY_VERIFICATION,
            maker_id=maker_id,
            registry_no=state.get("registry_no"),
        )
        return {
            "status": WAITING_FOR_COMPANY_VERIFICATION,
            "verification_recipient": config["email"]["verification_recipient"],
            "confirmation_issued_at": contact.get("sent_at"),
            "mailbox_provisioned": bool(mailbox.get("address")),
            "outbound_enabled": bool(mailbox.get("outbound_enabled")),
            "next_human_action": (
                "Open the responsible-party confirmation email, then run init --resume. "
                "If the address is not a monitored inbox, production support must reissue "
                "confirmation through the existing operator-recovery process."
            ),
            **said,
        }

    address = str(mailbox.get("address") or "").strip()
    if not address and not no_mailbox:
        raise RuntimeError("Company is confirmed but production returned no canonical mailbox")
    email_enabled = config.get("providers", {}).get("email") == "book_of_houses"
    if email_enabled:
        config["email"]["status"] = "provisioned"
        config["email"]["address"] = address
    config["toll_bench"]["status"] = READY
    state["status"] = READY
    if not no_mailbox:
        state["mailbox"] = mailbox
    save_config(path, config)
    save_onboarding(path, config, state)
    return {
        "status": READY,
        "mailbox": address or None,
        "outbound_enabled": bool(mailbox.get("outbound_enabled")),
        "maker_id": maker_id,
        "registry_no": state.get("registry_no"),
        **said,
    }
