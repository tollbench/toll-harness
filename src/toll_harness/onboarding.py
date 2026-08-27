from __future__ import annotations

import json
import os
import secrets
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from toll_harness import __version__
from toll_harness.email.book_of_houses import BookOfHousesApiClient
from toll_harness.fleet import default_fleet_database
from toll_harness.storage.secrets import FileSecretStore

HARNESS_LABEL = f"Toll Harness {__version__}"

WAITING_FOR_COMPANY_VERIFICATION = "WAITING_FOR_COMPANY_VERIFICATION"
READY = "READY"
VALIDATED = "VALIDATED"
LOCAL_CONFIGURED = "LOCAL_CONFIGURED"
TOKEN_SECRET_NAME = "book_of_houses_agent_token"

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
    "browser.wait",
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
    "toll_bench.status",
    "toll_bench.ensure_reachable",
    "toll_bench.attention",
    "toll_bench.events",
    "toll_bench.list_targets",
    "toll_bench.read_brief",
    "toll_bench.list_proposals",
    "toll_bench.validate_proposal",
    "toll_bench.submit_proposal",
    "toll_bench.read_finalist_answers",
    "toll_bench.submit_informed_plan",
    "toll_bench.current_step",
    "toll_bench.reply_step_message",
    "toll_bench.post_check_in",
    "toll_bench.file_outcome",
]


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
    # bedrock | anthropic | openai | claude_code | codex
    model_adapter: str = "bedrock"
    # Pasted at init for the anthropic/openai adapters; written straight into
    # the agent's isolated SecretStore, never into agent.yaml or onboarding
    # state. The subscription rails (claude_code, codex) never carry a key.
    model_api_key: str | None = None


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
        return {
            "adapter": adapter,
            "model_id": answers.model_id,
            "max_tokens": 2048,
            "api_key_secret": _API_KEY_SECRET_NAMES[adapter],
        }
    if adapter in ("claude_code", "codex"):
        # Subscription OAuth rails: the vendor CLI owns the login, so the
        # configuration carries no credential reference at all.
        return {
            "adapter": adapter,
            "model_id": answers.model_id or None,
            "timeout_seconds": 600,
        }
    raise ValueError(
        "model_adapter must be one of: bedrock, anthropic, openai, claude_code, codex"
    )


def create_configuration(destination: Path, answers: InitAnswers) -> Path:
    if answers.mode not in {"Autonomous", "Supported"}:
        raise ValueError("Operating mode must be exactly Autonomous or Supported")
    if answers.model_api_key and answers.model_adapter not in _API_KEY_SECRET_NAMES:
        raise ValueError("Only the anthropic and openai adapters take a pasted API key")
    if answers.connect_toll_bench and not all(
        (
            answers.company_url,
            answers.responsible_legal_name,
            answers.responsible_jurisdiction,
            answers.verification_recipient,
        )
    ):
        raise ValueError("Connected onboarding requires company and responsible-party details")
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
            "proposal_limit_per_target": 4,
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
            "base_url": "https://bookofhouses.com",
            "status": LOCAL_CONFIGURED if answers.connect_toll_bench else "DISCONNECTED",
            "token_secret": TOKEN_SECRET_NAME if answers.connect_toll_bench else None,
            "company_url": answers.company_url,
            "responsible_party": (
                {
                    "legal_name": answers.responsible_legal_name,
                    "jurisdiction": answers.responsible_jurisdiction,
                    "contact_ref": answers.verification_recipient,
                }
                if answers.connect_toll_bench
                else None
            ),
            "idempotency_key": f"toll-harness-register-{agent_id}",
            "maker_id": None,
            "registry_no": None,
        },
    }
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
    return {
        "handle": agent["name"],
        "skills": (
            "Autonomous web research, browser interaction, email, file work, stateful task "
            "execution, and completion reporting through Toll Harness standard capabilities."
        ),
        "disclosure": {
            "is_ai": True,
            "model": model["model_id"],
            "operator_label": agent["company"],
            "operator_type": "company",
            "company_url": toll_bench["company_url"],
        },
        "system_record": {
            "base_models": [
                {
                    "provider": agent["intelligence"],
                    "model": model["model_id"],
                    "version": agent["intelligence"],
                }
            ],
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
    mailbox_response = authenticated.mailbox()
    mailbox = mailbox_response.get("mailbox") or state.get("mailbox") or {}
    contact = me.get("responsible_party_contact") or {}
    state["mailbox"] = mailbox
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
        }

    address = str(mailbox.get("address") or "").strip()
    if not address:
        raise RuntimeError("Company is confirmed but production returned no canonical mailbox")
    email_enabled = config.get("providers", {}).get("email") == "book_of_houses"
    if email_enabled:
        config["email"]["status"] = "provisioned"
        config["email"]["address"] = address
    config["toll_bench"]["status"] = READY
    state["status"] = READY
    state["mailbox"] = mailbox
    save_config(path, config)
    save_onboarding(path, config, state)
    return {
        "status": READY,
        "mailbox": address,
        "outbound_enabled": bool(mailbox.get("outbound_enabled")),
        "maker_id": maker_id,
        "registry_no": state.get("registry_no"),
    }
