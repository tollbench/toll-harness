from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

JsonObject = dict[str, Any]


class AutonomyMode(str, Enum):
    AUTONOMOUS = "autonomous"
    SUPPORTED = "supported"


class RunStatus(str, Enum):
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    LIMIT_REACHED = "limit_reached"


class EmailProvisioningStatus(str, Enum):
    PENDING_PROVISIONING = "pending_provisioning"
    PROVISIONED = "provisioned"
    INELIGIBLE = "ineligible"


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: JsonObject
    version: str = "1.0"


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: JsonObject


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    name: str
    output: JsonObject
    is_error: bool = False


@dataclass(frozen=True)
class ModelMessage:
    role: Literal["user", "assistant"]
    content: list[JsonObject]

    @classmethod
    def text(cls, role: Literal["user", "assistant"], value: str) -> ModelMessage:
        return cls(role=role, content=[{"type": "text", "text": value}])


@dataclass(frozen=True)
class ModelUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    raw: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class ModelResponse:
    message: ModelMessage
    text: str
    tool_calls: list[ToolCall]
    usage: ModelUsage = field(default_factory=ModelUsage)
    stop_reason: str | None = None
    raw_metadata: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class Event:
    run_id: str
    sequence: int
    kind: str
    source: str
    payload: JsonObject
    created_at: str


@dataclass(frozen=True)
class Checkpoint:
    run_id: str
    goal: str
    data: JsonObject
    event_cursor: int
    revision: int
    updated_at: str


@dataclass(frozen=True)
class AgentIdentity:
    id: str
    name: str
    intelligence: str
    company: str
    harness: str
    autonomy_mode: AutonomyMode
    email_provider: str
    email_status: EmailProvisioningStatus
    email_verification_recipient: str | None
    email_address: str | None
    created_at: str = ""


@dataclass(frozen=True)
class RunRecord:
    id: str
    goal: str
    requested_mode: AutonomyMode
    status: RunStatus
    model: str
    agent_id: str | None
    operator_message_count: int
    created_at: str
    updated_at: str

    @property
    def observed_mode(self) -> AutonomyMode:
        return (
            AutonomyMode.SUPPORTED if self.operator_message_count > 0 else AutonomyMode.AUTONOMOUS
        )


@dataclass(frozen=True)
class RunResult:
    run_id: str
    status: RunStatus
    result: JsonObject | None
    checkpoint: Checkpoint
    usage: ModelUsage
    iterations: int
    observed_mode: AutonomyMode
