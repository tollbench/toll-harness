from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml

from toll_harness.browser.playwright import PlaywrightBrowserProvider
from toll_harness.core.runtime import (
    BASE_SYSTEM_INSTRUCTION,
    TOLL_BENCH_SYSTEM_INSTRUCTION,
    HarnessRuntime,
)
from toll_harness.core.types import (
    AgentIdentity,
    AutonomyMode,
    EmailProvisioningStatus,
)
from toll_harness.email.book_of_houses import (
    BookOfHousesApiClient,
    BookOfHousesEmailProvider,
    BookOfHousesRestMailClient,
)
from toll_harness.fleet import FleetStore, default_fleet_database
from toll_harness.models.anthropic import AnthropicModelAdapter
from toll_harness.models.base import ModelAdapter
from toll_harness.models.bedrock import BedrockModelAdapter
from toll_harness.models.openai import OpenAIModelAdapter
from toll_harness.storage.filesystem import FilesystemArtifactStore
from toll_harness.storage.local import SQLiteStore
from toll_harness.storage.secrets import FileSecretStore
from toll_harness.toll_bench.book_of_houses import BookOfHousesTollBenchProvider
from toll_harness.tools.registry import add_toll_bench_tools, build_standard_registry
from toll_harness.tools.web import BasicWebProvider

DEFAULT_TOOLS = [
    "state.load",
    "state.save",
    "result.complete",
    "result.fail",
    "human.request",
    "files.list",
    "files.read",
    "files.write",
]


@dataclass
class RuntimeResources:
    runtime: HarnessRuntime
    store: SQLiteStore
    browser: Any | None = None
    agent_identity: AgentIdentity | None = None
    toll_bench: Any | None = None

    def close(self) -> None:
        if self.browser is not None:
            self.browser.close()


def load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    value = yaml.safe_load(config_path.read_text())
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("Configuration must be an object with version: 1")
    return value


def load_agent_identity(config: dict[str, Any]) -> AgentIdentity | None:
    agent = config.get("agent")
    if agent is None:
        return None
    if not isinstance(agent, dict) or agent.get("permanent") is not True:
        raise ValueError("agent.permanent must be true for a permanent agent configuration")
    required = ("id", "name", "intelligence", "company", "mode", "harness")
    missing = [key for key in required if not agent.get(key)]
    if missing:
        raise ValueError(f"Missing permanent agent fields: {', '.join(missing)}")
    UUID(agent["id"])
    mode_labels = {"Autonomous": AutonomyMode.AUTONOMOUS, "Supported": AutonomyMode.SUPPORTED}
    if agent["mode"] not in mode_labels:
        raise ValueError("agent.mode must be exactly Autonomous or Supported")
    email = config.get("email", {})
    identity = AgentIdentity(
        id=agent["id"],
        name=agent["name"],
        intelligence=agent["intelligence"],
        company=agent["company"],
        harness=agent["harness"],
        autonomy_mode=mode_labels[agent["mode"]],
        email_provider=email.get("provider", "disabled"),
        email_status=EmailProvisioningStatus(email.get("status", "pending_provisioning")),
        email_verification_recipient=email.get("verification_recipient"),
        email_address=email.get("address"),
    )
    benchmark = config.get("benchmark", {})
    expected_benchmark = {
        "intelligence": identity.intelligence,
        "harness": identity.harness,
        "company": identity.company,
        "autonomy": identity.autonomy_mode.value.upper(),
    }
    if benchmark != expected_benchmark:
        raise ValueError(f"benchmark must exactly match the agent identity: {expected_benchmark}")
    return identity


def _resolve_model_api_key(config: dict, *, root: Path, data_dir: Path) -> str | None:
    secret_name = config.get("model", {}).get("api_key_secret")
    if not secret_name:
        return None  # the provider SDK falls back to its standard environment variable
    secret_config = config.get("secrets", {})
    if secret_config.get("provider") != "file":
        raise ValueError("model.api_key_secret requires a configured file SecretStore")
    secret_directory = (root / secret_config.get("directory", "secrets")).resolve()
    if secret_directory != data_dir and data_dir not in secret_directory.parents:
        raise ValueError("Model API keys must remain in isolated storage")
    return FileSecretStore(secret_directory).get(secret_name)


def _build_model(config: dict, *, root: Path, data_dir: Path) -> ModelAdapter:
    model_config = config.get("model", {})
    adapter_name = model_config.get("adapter")
    model_id = model_config.get("model_id")
    max_tokens = model_config.get("max_tokens", 2048)
    if adapter_name == "bedrock":
        if not model_id:
            raise ValueError("model.model_id is required; run `toll-harness bedrock probe` first")
        return BedrockModelAdapter(
            model_id,
            region=model_config.get("region", "us-west-2"),
            profile_name=model_config.get("profile"),
            max_tokens=max_tokens,
            temperature=model_config.get("temperature", 0),
        )
    if adapter_name == "anthropic":
        return AnthropicModelAdapter(
            model_id or "claude-opus-4-8",
            api_key=_resolve_model_api_key(config, root=root, data_dir=data_dir),
            max_tokens=max_tokens,
        )
    if adapter_name == "openai":
        if not model_id:
            raise ValueError("model.model_id is required for the openai adapter")
        return OpenAIModelAdapter(
            model_id,
            api_key=_resolve_model_api_key(config, root=root, data_dir=data_dir),
            max_tokens=max_tokens,
        )
    raise ValueError("model.adapter must be one of: bedrock, anthropic, openai")


def build_runtime(path: str | Path) -> RuntimeResources:
    config_path = Path(path).resolve()
    config = load_config(config_path)
    identity = load_agent_identity(config)
    root = config_path.parent
    storage = config.get("storage", {})
    data_dir = (root / storage.get("directory", ".toll-harness")).resolve()
    if identity and identity.id not in data_dir.parts:
        raise ValueError("Permanent agent storage must be isolated under its unique agent ID")
    store = SQLiteStore(data_dir / "harness.sqlite3")
    if identity:
        identity = store.register_agent(identity)
    artifacts = FilesystemArtifactStore(data_dir / "artifacts")
    model = _build_model(config, root=root, data_dir=data_dir)
    model_config = config.get("model", {})

    providers = config.get("providers", {})
    web = BasicWebProvider() if providers.get("web") == "basic" else None
    browser_name = providers.get("browser")
    if browser_name == "playwright":
        browser = PlaywrightBrowserProvider(headless=providers.get("browser_headless", True))
    elif browser_name == "agentcore":
        from toll_harness.browser.agentcore import AgentCoreBrowserProvider

        browser = AgentCoreBrowserProvider(
            region=model_config.get("region", "us-west-2"),
            profile_name=model_config.get("profile"),
            browser_identifier=providers.get("browser_identifier", "aws.browser.v1"),
        )
    else:
        browser = None
    email_provider = None
    toll_bench_provider = None
    fleet_store = None
    fleet_config = config.get("fleet") or {}
    if identity and fleet_config.get("enabled", True):
        fleet_store = FleetStore(fleet_config.get("database") or default_fleet_database())
        fleet_store.register_agent(
            agent_id=identity.id,
            name=identity.name,
            config_path=config_path,
        )
    toll_bench_config = config.get("toll_bench", {})
    connected_api = None
    if toll_bench_config.get("connected"):
        token_name = toll_bench_config.get("token_secret")
        maker_id = toll_bench_config.get("maker_id")
        secret_config = config.get("secrets", {})
        if secret_config.get("provider") != "file":
            raise ValueError("Connected Toll Bench agents require a configured SecretStore")
        secret_directory = (root / secret_config.get("directory", "secrets")).resolve()
        if secret_directory != data_dir and data_dir not in secret_directory.parents:
            raise ValueError("Permanent agent secrets must remain in isolated storage")
        token = FileSecretStore(secret_directory).get(token_name) if token_name else None
        if not token or not maker_id:
            raise ValueError("Connected Toll Bench agent token or maker ID is missing")
        connected_api = BookOfHousesApiClient(
            base_url=toll_bench_config.get("base_url", "https://bookofhouses.com"),
            token=token,
            maker_id=maker_id,
        )
        toll_bench_provider = BookOfHousesTollBenchProvider(
            connected_api,
            fleet=fleet_store,
            fleet_agent_id=identity.id if identity else None,
            fleet_proposal_limit=int(fleet_config.get("proposal_limit_per_target", 4)),
        )
    if providers.get("email") == "book_of_houses":
        email_config = config.get("email", {})
        if email_config.get("status") == "provisioned":
            mailbox = email_config.get("address")
            if not mailbox or connected_api is None:
                raise ValueError("Provisioned Book of Houses email requires a connected agent")
            email_client = BookOfHousesRestMailClient(
                connected_api,
                expected_mailbox=mailbox,
                send_context=email_config.get("send_context"),
                pending_store=data_dir / "pending-email-send.json",
            )
            email_provider = BookOfHousesEmailProvider(mailbox=mailbox, client=email_client)
    runtime_config = config.get("runtime", {})
    runtime_mode = AutonomyMode(runtime_config.get("autonomy", "autonomous"))
    if identity and runtime_mode is not identity.autonomy_mode:
        raise ValueError("runtime.autonomy must match agent.mode")
    knowledge_namespace = runtime_config.get("knowledge_namespace")
    if identity:
        knowledge_namespace = knowledge_namespace or identity.id
        if knowledge_namespace != identity.id:
            raise ValueError("Permanent agent knowledge must use its unique agent ID namespace")
    tool_registry = build_standard_registry()
    if toll_bench_provider:
        tool_registry = add_toll_bench_tools(tool_registry)
    runtime = HarnessRuntime(
        model=model,
        state_store=store,
        event_store=store,
        artifact_store=artifacts,
        tools=tool_registry,
        enabled_tools=runtime_config.get("tools", DEFAULT_TOOLS),
        web_provider=web,
        email_provider=email_provider,
        browser_provider=browser,
        toll_bench_provider=toll_bench_provider,
        agent_identity=identity,
        knowledge_namespace=knowledge_namespace,
        max_iterations=runtime_config.get("max_iterations", 20),
        system_instruction=(
            BASE_SYSTEM_INSTRUCTION + TOLL_BENCH_SYSTEM_INSTRUCTION
            if toll_bench_provider
            else BASE_SYSTEM_INSTRUCTION
        ),
    )
    return RuntimeResources(
        runtime=runtime,
        store=store,
        browser=browser,
        agent_identity=identity,
        toll_bench=toll_bench_provider,
    )


def default_config() -> str:
    return """# Toll Harness configuration v1
version: 1
model:
  adapter: bedrock
  region: us-west-2
  profile: null
  model_id: null  # Set from `toll-harness bedrock probe` output.
  max_tokens: 2048
  temperature: 0
runtime:
  autonomy: autonomous
  knowledge_namespace: null  # Set to an agent name to opt into cross-run learning.
  max_iterations: 20
  tools:
    - state.load
    - state.save
    - files.list
    - files.read
    - files.write
    - result.complete
    - result.fail
    - human.request
storage:
  directory: .toll-harness
providers:
  web: disabled
  browser: disabled
  email: disabled
"""
