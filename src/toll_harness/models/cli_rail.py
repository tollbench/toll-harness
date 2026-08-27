from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from toll_harness.core.types import (
    JsonObject,
    ModelMessage,
    ModelResponse,
    ModelUsage,
    ToolCall,
    ToolDefinition,
)
from toll_harness.models.base import ModelAdapter, ModelInvocationError

# OAuth-subscription rails. Anthropic and OpenAI subscription sign-ins (Claude
# Pro/Max, ChatGPT) are OAuth flows owned by the vendors' own CLIs — those
# tokens are NOT accepted by the raw platform APIs, so an adapter cannot simply
# send them as bearer keys. These adapters run the official CLI headlessly and
# inherit whatever login it holds: OAuth subscription or API key, the CLI's
# choice, with the CLI doing token storage and refresh. The CLIs are agents,
# not raw models, so the harness tool contract rides a strict one-object JSON
# envelope instead of native tool blocks.

_ENVELOPE_INSTRUCTION = """\
# How to reply
Reply with EXACTLY one JSON object and nothing else - no prose before or after,
no markdown fence:
{"text": "<brief thinking, optional>",
 "tool_calls": [{"name": "<one of the tool names above>", "arguments": {}}]}
To act, include one or more tool_calls whose arguments match that tool's JSON
schema. A reply without tool_calls does nothing; the run only ends through the
result.complete or result.fail tools."""

_CORRECTION = (
    "Your previous reply could not be parsed as the required JSON envelope. "
    "Reply again with EXACTLY one JSON object of the form "
    '{"text": "...", "tool_calls": [{"name": "...", "arguments": {}}]} '
    "and nothing else."
)


def _render_prompt(
    system: str,
    messages: Sequence[ModelMessage],
    tools: Sequence[ToolDefinition],
) -> str:
    catalogue = [
        {
            "name": tool.name,
            "version": tool.version,
            "description": tool.description,
            "input_schema": tool.input_schema,
        }
        for tool in tools
    ]
    transcript = [{"role": message.role, "content": message.content} for message in messages]
    return (
        f"{system}\n\n"
        "# Available tools\n"
        "You operate inside a tool loop. These are the ONLY tools; nothing else "
        "exists, including any tools of your own runtime:\n"
        f"{json.dumps(catalogue, ensure_ascii=False)}\n\n"
        "# Conversation so far\n"
        "Normalized transcript, oldest first. tool_result blocks are the "
        "harness's answers to your earlier tool_calls:\n"
        f"{json.dumps(transcript, ensure_ascii=False)}\n\n"
        f"{_ENVELOPE_INSTRUCTION}"
    )


def _strip_fences(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline >= 0:
            text = text[first_newline + 1 :]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


def _parse_envelope(raw: str) -> tuple[str, list[ToolCall], list[JsonObject]]:
    text = _strip_fences(raw)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("no JSON object in reply") from None
        value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("envelope must be a JSON object")
    commentary = str(value.get("text") or "")
    calls_raw = value.get("tool_calls") or []
    if not isinstance(calls_raw, list):
        raise ValueError("tool_calls must be a list")
    calls: list[ToolCall] = []
    normalized: list[JsonObject] = []
    if commentary:
        normalized.append({"type": "text", "text": commentary})
    for item in calls_raw:
        if not isinstance(item, dict) or not str(item.get("name") or ""):
            raise ValueError("each tool_call needs a name")
        arguments = item.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise ValueError("tool_call arguments must be an object")
        call = ToolCall(
            id=f"cli-{uuid.uuid4().hex[:12]}",
            name=str(item["name"]),
            arguments=arguments,
        )
        calls.append(call)
        normalized.append(
            {"type": "tool_call", "id": call.id, "name": call.name, "arguments": call.arguments}
        )
    return commentary, calls, normalized


class _CliRailAdapter(ModelAdapter):
    """Shared plumbing: render prompt, run the vendor CLI, parse the envelope."""

    provider = "cli"
    install_hint = ""

    def __init__(
        self,
        model_id: str | None = None,
        *,
        binary: str,
        workdir: str | Path | None = None,
        timeout_seconds: int = 600,
        extra_args: Sequence[str] | None = None,
        runner: Callable[..., subprocess.CompletedProcess] | None = None,
    ):
        self.binary = binary
        self._model_id = model_id or f"{self.provider}/cli-default"
        self._configured_model = model_id
        self.timeout_seconds = timeout_seconds
        self.extra_args = list(extra_args or [])
        # A scratch working directory so the CLI never reads project context
        # (CLAUDE.md, AGENTS.md, git state) from wherever the harness happens
        # to run.
        if workdir is None:
            workdir = Path(tempfile.gettempdir()) / "toll-harness-cli-rail"
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self._runner = runner
        if runner is None and shutil.which(binary) is None:
            raise RuntimeError(
                f"The '{binary}' CLI was not found on PATH. {self.install_hint}"
            )

    @property
    def model_id(self) -> str:
        return self._model_id

    def _run(self, argv: list[str], prompt: str) -> subprocess.CompletedProcess:
        runner = self._runner or subprocess.run
        try:
            return runner(
                argv,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                cwd=str(self.workdir),
            )
        except subprocess.TimeoutExpired as error:
            raise ModelInvocationError(
                self.provider, "timeout", f"CLI call exceeded {self.timeout_seconds}s"
            ) from error

    def _invoke_cli(self, prompt: str) -> tuple[str, ModelUsage]:
        """Run one CLI call and return (raw model text, usage)."""
        raise NotImplementedError

    def invoke(
        self,
        *,
        system: str,
        messages: Sequence[ModelMessage],
        tools: Sequence[ToolDefinition],
    ) -> ModelResponse:
        prompt = _render_prompt(system, messages, tools)
        raw, usage = self._invoke_cli(prompt)
        try:
            commentary, calls, normalized = _parse_envelope(raw)
        except ValueError:
            # One corrective retry, then degrade to a text-only response so the
            # runtime's own "continue the goal" nudge keeps the run alive
            # instead of failing it on a chatty reply.
            retry_prompt = f"{prompt}\n\n{_CORRECTION}\n\nYour previous reply was:\n{raw[:2000]}"
            retry_raw, retry_usage = self._invoke_cli(retry_prompt)
            usage = ModelUsage(
                input_tokens=usage.input_tokens + retry_usage.input_tokens,
                output_tokens=usage.output_tokens + retry_usage.output_tokens,
                total_tokens=usage.total_tokens + retry_usage.total_tokens,
                raw={"first": usage.raw, "retry": retry_usage.raw},
            )
            try:
                commentary, calls, normalized = _parse_envelope(retry_raw)
            except ValueError:
                text = retry_raw.strip()
                return ModelResponse(
                    message=ModelMessage.text("assistant", text),
                    text=text,
                    tool_calls=[],
                    usage=usage,
                    stop_reason="envelope_unparsed",
                    raw_metadata={"model": self.model_id},
                )
        if not normalized:
            normalized = [{"type": "text", "text": ""}]
        return ModelResponse(
            message=ModelMessage(role="assistant", content=normalized),
            text=commentary,
            tool_calls=calls,
            usage=usage,
            stop_reason="tool_use" if calls else "end_turn",
            raw_metadata={"model": self.model_id},
        )


class ClaudeCodeCliAdapter(_CliRailAdapter):
    """Claude subscription (Pro/Max) OAuth via the official Claude Code CLI.

    Sign in once with `claude` (browser OAuth) or set CLAUDE_CODE_OAUTH_TOKEN
    from `claude setup-token` for headless machines. Subscription OAuth tokens
    are refused by the raw Anthropic API, so the CLI is the sanctioned rail.
    """

    provider = "claude_code"
    install_hint = (
        "Install Claude Code (https://claude.com/claude-code) and sign in with "
        "your Claude subscription: run `claude` once interactively, or set "
        "CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token`."
    )

    def __init__(self, model_id: str | None = None, *, binary: str = "claude", **kwargs: Any):
        super().__init__(model_id, binary=binary, **kwargs)

    def _invoke_cli(self, prompt: str) -> tuple[str, ModelUsage]:
        argv = [self.binary, "-p", "--output-format", "json", "--max-turns", "1"]
        if self._configured_model:
            argv += ["--model", self._configured_model]
        argv += self.extra_args
        completed = self._run(argv, prompt)
        if completed.returncode != 0:
            raise ModelInvocationError(
                self.provider,
                f"exit_{completed.returncode}",
                (completed.stderr or completed.stdout or "").strip()[-2000:],
            )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise ModelInvocationError(
                self.provider, "cli_output_invalid", completed.stdout[:2000]
            ) from error
        if payload.get("is_error"):
            raise ModelInvocationError(
                self.provider, str(payload.get("subtype") or "error"), str(payload.get("result"))
            )
        usage_raw = payload.get("usage") or {}
        input_tokens = int(usage_raw.get("input_tokens") or 0)
        output_tokens = int(usage_raw.get("output_tokens") or 0)
        usage = ModelUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            raw=dict(usage_raw),
        )
        return str(payload.get("result") or ""), usage


class ExternalAgentAdapter(_CliRailAdapter):
    """Layer Toll Harness over ANY agent or model runner: one process contract.

    Point ``model.adapter: external`` at any executable (``model.command``).
    Each invocation runs the command with the rendered prompt (system + tool
    catalog + normalized transcript + envelope instruction) on stdin, and reads
    the reply — the strict one-JSON-object envelope — from stdout. Exit 0 with
    the envelope on stdout is the whole contract; wrap any inner agent, CLI, or
    HTTP service in a few lines of shell or Python. The harness stays the only
    tool executor and persistence owner; the inner agent supplies intelligence.
    """

    provider = "external"
    install_hint = (
        "Set model.command in agent.yaml to an executable that reads a prompt "
        "on stdin and writes the reply envelope to stdout."
    )

    def __init__(
        self,
        model_id: str | None = None,
        *,
        command: Sequence[str],
        **kwargs: Any,
    ):
        parts = [str(part) for part in (command or [])]
        if not parts:
            raise ValueError("The external adapter requires a non-empty model.command list")
        self.command = parts
        super().__init__(model_id or "external/custom", binary=parts[0], **kwargs)

    def _invoke_cli(self, prompt: str) -> tuple[str, ModelUsage]:
        completed = self._run(self.command + self.extra_args, prompt)
        if completed.returncode != 0:
            raise ModelInvocationError(
                self.provider,
                f"exit_{completed.returncode}",
                (completed.stderr or completed.stdout or "").strip()[-2000:],
            )
        return completed.stdout, ModelUsage()


class CodexCliAdapter(_CliRailAdapter):
    """ChatGPT subscription OAuth via the official OpenAI Codex CLI.

    Sign in once with `codex login` (browser OAuth). ChatGPT OAuth tokens are
    not accepted by the standard OpenAI API, so the CLI is the sanctioned rail.
    The CLI also honors OPENAI_API_KEY if the operator prefers a key.
    """

    provider = "codex"
    install_hint = (
        "Install the Codex CLI (https://github.com/openai/codex) and sign in "
        "with your ChatGPT subscription: run `codex login`."
    )

    def __init__(self, model_id: str | None = None, *, binary: str = "codex", **kwargs: Any):
        super().__init__(model_id, binary=binary, **kwargs)

    def _invoke_cli(self, prompt: str) -> tuple[str, ModelUsage]:
        # --output-last-message is the stable contract across codex versions;
        # the --json event stream has changed shape release to release.
        last_message = self.workdir / f"last-message-{uuid.uuid4().hex}.txt"
        argv = [
            self.binary,
            "exec",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--cd",
            str(self.workdir),
            "--output-last-message",
            str(last_message),
        ]
        if self._configured_model:
            argv += ["--model", self._configured_model]
        argv += self.extra_args
        argv.append("-")  # read the prompt from stdin
        try:
            completed = self._run(argv, prompt)
            if completed.returncode != 0:
                raise ModelInvocationError(
                    self.provider,
                    f"exit_{completed.returncode}",
                    (completed.stderr or completed.stdout or "").strip()[-2000:],
                )
            try:
                raw = last_message.read_text()
            except FileNotFoundError as error:
                raise ModelInvocationError(
                    self.provider,
                    "no_last_message",
                    "codex exec wrote no final message; stderr: "
                    + (completed.stderr or "").strip()[-1000:],
                ) from error
        finally:
            last_message.unlink(missing_ok=True)
        # codex exec does not report token usage on this contract.
        return raw, ModelUsage()
