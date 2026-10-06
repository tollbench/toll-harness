from __future__ import annotations

import json
import os
import shutil
import signal
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

# 2026-09-25: on a tools-free proposal ask Ali (Codex) refused ("No tools
# are available... expose result.complete") and Trey wrapped the proposal in
# a result.complete tool_call, because the envelope wording said the run only
# ends through result.complete. A call with no tools gets no catalogue and no
# envelope, only this line.
_NO_TOOLS_INSTRUCTION = """\
# How to reply
There are no tools on this call. Reply with EXACTLY the one JSON object the
message above asks for and nothing else - no prose, no markdown fence, no
tool calls."""

_CORRECTION = (
    "Your previous reply could not be parsed as the required JSON envelope. "
    "Reply again with EXACTLY one JSON object of the form "
    '{"text": "...", "tool_calls": [{"name": "...", "arguments": {}}]} '
    "and nothing else."
)


# 2026-10-06 (Steven, pilot finding): lab agent Sam (claude_code rail, Opus
# 5.5) handed back a file and the Claude Code CLI exited 1 fourteen times in
# one night: `--tools ''` turns off the CLI's BUILT-IN tools only, and the
# account's claude.ai connectors (two MCP servers, ~68k tokens of schema) still
# rode every request. With a native tools array in front of it the model called
# the harness tools natively (`files.list`, `toll_bench.post_check_in`), the
# CLI answered "No such tool available", and the second turn broke
# `--max-turns 1`: error_max_turns, stop_reason tool_use, exit 1, no reply.
# The harness read a crash, three times, and the loop guard parked the step.
# Two parts: the rail now runs the CLI with `--strict-mcp-config` so no
# ambient MCP server rides along (an explicit --mcp-config in extra_args still
# loads), and a run that still ends on a native tool call is NOT a crash: it
# gets ONE retry with the reminder below, and a second one is a failure that
# names the tool.
class NativeToolUse(Exception):
    """The CLI run ended on the model's own native tool call, not on a reply.

    `names` are the native tools the model reached for, as the CLI's stream
    reported them; `usage` is what that call cost.
    """

    def __init__(self, names: Sequence[str], usage: ModelUsage):
        self.names = list(names)
        self.usage = usage
        super().__init__(", ".join(self.names) or "a native tool the CLI did not name")


def _named(names: Sequence[str]) -> str:
    if not names:
        return "the CLI did not name it"
    return ", ".join(f"'{name}'" for name in names)


def _native_tool_reminder(names: Sequence[str], tools: Sequence[ToolDefinition]) -> str:
    target = f" to {_named(names)}" if names else ""
    opening = f"Your previous reply ended on a native tool call{target}, so nothing ran."
    if not tools:
        return (
            f"{opening} There are no tools on this call, native or otherwise. "
            "Reply with EXACTLY the one JSON object the message above asks for, "
            "as plain text."
        )
    reminder = (
        f"{opening} There are no native tools on this call. Every tool here is "
        "called by writing the JSON envelope as plain text"
    )
    if any(tool.name == "files.write" for tool in tools):
        reminder += (
            "; a file goes through files.write (base64 for binary) into the run folder"
        )
    return reminder + "."


def _add_usage(first: ModelUsage, retry: ModelUsage) -> ModelUsage:
    return ModelUsage(
        input_tokens=first.input_tokens + retry.input_tokens,
        output_tokens=first.output_tokens + retry.output_tokens,
        total_tokens=first.total_tokens + retry.total_tokens,
        raw={"first": first.raw, "retry": retry.raw},
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
    if not tools:
        return (
            f"{system}\n\n"
            "# Conversation so far\n"
            "Normalized transcript, oldest first. tool_result blocks are the "
            "harness's answers to your earlier tool_calls:\n"
            f"{json.dumps(transcript, ensure_ascii=False)}\n\n"
            f"{_NO_TOOLS_INSTRUCTION}"
        )
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


_TERMINAL_TOOLS = ("result.complete", "result.fail")


def _parse_envelope(
    raw: str, *, tools_offered: bool = True
) -> tuple[str, list[ToolCall], list[JsonObject]]:
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
    # 2026-09-25: the five lab agents on tollbench.com answered a proposal ask
    # with the bare eight-field object instead of the envelope. It parsed, had
    # no "text", read as an empty envelope, and no bid was filed on a live
    # want. An object with neither envelope key IS the answer; a nested
    # "text" object or list is the answer too.
    if "text" not in value and "tool_calls" not in value:
        commentary = json.dumps(value, ensure_ascii=False)
    elif isinstance(value.get("text"), (dict, list)):
        commentary = json.dumps(value["text"], ensure_ascii=False)
    else:
        commentary = str(value.get("text") or "")
    calls_raw = value.get("tool_calls") or []
    if not isinstance(calls_raw, list):
        raise ValueError("tool_calls must be a list")
    # 2026-09-25: on a tools-free ask Trey (Codex) put the proposal inside a
    # result.complete tool_call with empty text, and the draft loop read no
    # answer. On a call that offered NO tools, one empty-text result.complete
    # or result.fail carrying an object is the answer. With tools offered it
    # stays a tool call: that is how a real run ends.
    if not tools_offered and not commentary and len(calls_raw) == 1:
        only = calls_raw[0]
        if isinstance(only, dict) and only.get("name") in _TERMINAL_TOOLS:
            arguments = only.get("arguments")
            if isinstance(arguments, dict):
                answer = arguments["result"] if "result" in arguments else arguments
                if isinstance(answer, dict):
                    commentary = json.dumps(answer, ensure_ascii=False)
                    return commentary, [], [{"type": "text", "text": commentary}]
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


# How long to give the CLI's process group after SIGTERM before SIGKILL.
_KILL_GRACE_SECONDS = 5.0


def _run_in_own_process_group(
    argv: Sequence[str],
    *,
    input: str,
    timeout: float,
    cwd: str,
    capture_output: bool = True,
    text: bool = True,
) -> subprocess.CompletedProcess:
    """subprocess.run, except the CLI gets its OWN process group and a timeout
    kills the whole tree.

    WHY (0.20.1). `subprocess.run(timeout=...)` kills only the direct child. The
    claude/codex CLIs spawn their own children (a node runtime, tool
    subprocesses) which inherit our stdout/stderr pipes. Two things then went
    wrong on a live Mac fleet: the grandchildren were orphaned and kept burning
    the subscription, and because they still held the pipes, run() sat in
    communicate() long past the 600s it was configured for -- the timeout
    bounded nothing. A sleeping laptop made both worse.

    Here the child starts in a new session (its own process group), and on
    timeout the WHOLE group gets SIGTERM, a short grace, then SIGKILL, after
    which the pipes are drained and TimeoutExpired is re-raised so the caller
    maps it to a timeout error exactly as before. The injectable `runner` used
    by the tests bypasses this on purpose.
    """
    proc = subprocess.Popen(
        list(argv),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
        text=text,
        cwd=cwd,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        try:
            proc.communicate(timeout=_KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        raise
    return subprocess.CompletedProcess(list(argv), proc.returncode, out, err)


def _kill_process_group(proc: subprocess.Popen) -> None:
    """SIGTERM the child's whole group, wait briefly, then SIGKILL what is left."""
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    for sig, wait in ((signal.SIGTERM, _KILL_GRACE_SECONDS), (signal.SIGKILL, 1.0)):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


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
        runner = self._runner or _run_in_own_process_group
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

    def _call_rail(
        self, prompt: str, tools: Sequence[ToolDefinition]
    ) -> tuple[str, ModelUsage]:
        """One CLI call. A run that ended on a native tool call is not a crash:
        it gets ONE retry with the envelope reminder appended. A second one
        raises NativeToolUse carrying both calls' usage."""
        try:
            return self._invoke_cli(prompt)
        except NativeToolUse as first:
            reminder = _native_tool_reminder(first.names, tools)
            try:
                raw, usage = self._invoke_cli(f"{prompt}\n\n{reminder}")
            except NativeToolUse as second:
                raise NativeToolUse(
                    second.names or first.names, _add_usage(first.usage, second.usage)
                ) from None
            return raw, _add_usage(first.usage, usage)

    def _native_tool_failure(
        self, refused: NativeToolUse, tools: Sequence[ToolDefinition]
    ) -> ModelResponse:
        """Twice on a native tool call: a failure that names the tool. On a run
        that offers result.fail the run ends through it with that reason; on a
        call that does not, the caller gets a ModelInvocationError coded
        native_tool_use instead of an opaque exit code."""
        reason = (
            f"{self.provider} rail: the model twice ended its reply on a native "
            f"tool call ({_named(refused.names)}) instead of writing the JSON "
            "envelope. This rail carries no native tools, so nothing ran."
        )
        if not any(tool.name == "result.fail" for tool in tools):
            raise ModelInvocationError(self.provider, "native_tool_use", reason)
        call = ToolCall(
            id=f"cli-{uuid.uuid4().hex[:12]}",
            name="result.fail",
            arguments={"reason": reason, "blocker": "native_tool_use"},
        )
        return ModelResponse(
            message=ModelMessage(
                role="assistant",
                content=[
                    {"type": "text", "text": reason},
                    {
                        "type": "tool_call",
                        "id": call.id,
                        "name": call.name,
                        "arguments": call.arguments,
                    },
                ],
            ),
            text=reason,
            tool_calls=[call],
            usage=refused.usage,
            stop_reason="native_tool_use",
            raw_metadata={"model": self.model_id, "native_tools": refused.names},
        )

    def invoke(
        self,
        *,
        system: str,
        messages: Sequence[ModelMessage],
        tools: Sequence[ToolDefinition],
    ) -> ModelResponse:
        prompt = _render_prompt(system, messages, tools)
        try:
            raw, usage = self._call_rail(prompt, tools)
        except NativeToolUse as refused:
            return self._native_tool_failure(refused, tools)
        try:
            commentary, calls, normalized = _parse_envelope(raw, tools_offered=bool(tools))
        except ValueError:
            # One corrective retry, then degrade to a text-only response so the
            # runtime's own "continue the goal" nudge keeps the run alive
            # instead of failing it on a chatty reply.
            retry_prompt = f"{prompt}\n\n{_CORRECTION}\n\nYour previous reply was:\n{raw[:2000]}"
            try:
                retry_raw, retry_usage = self._call_rail(retry_prompt, tools)
            except NativeToolUse as refused:
                return self._native_tool_failure(
                    NativeToolUse(refused.names, _add_usage(usage, refused.usage)), tools
                )
            usage = _add_usage(usage, retry_usage)
            try:
                commentary, calls, normalized = _parse_envelope(
                    retry_raw, tools_offered=bool(tools)
                )
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
        # stream-json, not json: the final `result` event is the same object
        # the json format prints, and the stream before it is the only place
        # the CLI names a native tool the model called.
        argv = [
            self.binary,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--max-turns",
            "1",
        ]
        # No ambient MCP server (the account's claude.ai connectors, user or
        # project .mcp.json) rides along: `--tools ''` turns off only the
        # built-in tools. An operator's explicit --mcp-config still loads.
        if "--strict-mcp-config" not in self.extra_args:
            argv.append("--strict-mcp-config")
        if self._configured_model:
            argv += ["--model", self._configured_model]
        argv += self.extra_args
        completed = self._run(argv, prompt)
        payload, native_tools = _read_claude_output(completed.stdout)
        if payload is not None and _ended_on_native_tool(
            payload, native_tools, completed.returncode
        ):
            raise NativeToolUse(native_tools, _claude_usage(payload))
        if completed.returncode != 0:
            raise ModelInvocationError(
                self.provider,
                f"exit_{completed.returncode}",
                (completed.stderr or completed.stdout or "").strip()[-2000:],
            )
        if payload is None:
            raise ModelInvocationError(
                self.provider, "cli_output_invalid", (completed.stdout or "")[:2000]
            )
        if payload.get("is_error"):
            raise ModelInvocationError(
                self.provider, str(payload.get("subtype") or "error"), str(payload.get("result"))
            )
        return str(payload.get("result") or ""), _claude_usage(payload)


def _read_claude_output(stdout: str) -> tuple[dict[str, Any] | None, list[str]]:
    """(the CLI's final result object or None, native tool names the model called).

    Reads the stream-json event lines; a single json-format result object is
    one such line, so both output formats read the same way.
    """
    text = (stdout or "").strip()
    payload: dict[str, Any] | None = None
    names: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "assistant":
            content = (event.get("message") or {}).get("content") or []
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = str(block.get("name") or "")
                    if name and name not in names:
                        names.append(name)
        elif event.get("type") == "result":
            payload = event
    if payload is None and text:
        try:
            whole = json.loads(text)
        except json.JSONDecodeError:
            whole = None
        if isinstance(whole, dict) and whole.get("type") in (None, "result"):
            payload = whole
    if payload is not None:
        for denial in payload.get("permission_denials") or []:
            name = str(denial.get("tool_name") or "") if isinstance(denial, dict) else ""
            if name and name not in names:
                names.append(name)
    return payload, names


def _ended_on_native_tool(
    payload: dict[str, Any], native_tools: Sequence[str], returncode: int
) -> bool:
    """The run stopped on the model's own tool call and produced no reply."""
    failed = returncode != 0 or bool(payload.get("is_error"))
    on_tool = payload.get("stop_reason") == "tool_use" or (
        bool(native_tools) and payload.get("subtype") == "error_max_turns"
    )
    return failed and on_tool


def _claude_usage(payload: dict[str, Any]) -> ModelUsage:
    usage_raw = payload.get("usage") or {}
    input_tokens = int(usage_raw.get("input_tokens") or 0)
    output_tokens = int(usage_raw.get("output_tokens") or 0)
    return ModelUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        raw=dict(usage_raw),
    )


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
