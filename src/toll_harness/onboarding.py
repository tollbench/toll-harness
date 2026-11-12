"""`init`, `connect` and `doctor`.

init asks three things only: the company, the model's name (a label) and the command that
wakes the agent. Running it is the approval: it registers (or connects an agent that already
registered, `--registered`), keeps the token in the secret store, installs and starts the
always-on service, and prints what the agent now has from Toll Bench.
"""

from __future__ import annotations

import shlex
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from toll_harness import __version__
from toll_harness.agent import (
    BASE_URL_ENV,
    CONFIG_VERSION,
    DEFAULT_BENCH_URL,
    DEFAULT_TIME_LIMIT_MINUTES,
    TOKEN_ENV,
    TOKEN_SECRET,
    Agent,
    ConfigError,
    checked_command,
    is_old_config,
    read_yaml,
    write_atomic,
)
from toll_harness.bench import Bench, BenchError
from toll_harness.handoff import ANSWERED, LATER, hand_off
from toll_harness.secrets import FileSecretStore

COMPANY = "Company"
MODEL = "Model's name (a label, for example Claude, Grok, Qwen)"
COMMAND = "Command that wakes your agent (it gets each item as JSON on stdin)"
BRAND_MAX = 40

DOCTOR_ITEM = {
    "id": "it_doctor", "kind": "doctor", "created_at": None, "due_at": None, "about": {},
    "do": "This is `toll-harness doctor` checking that your command runs. Print {\"ok\": true}"
          " (or nothing). Nothing is sent to Toll Bench.",
    "context": {}, "refs": [],
    "answer": {"needed": False, "shape": {"ok": "boolean"}, "example": {"ok": True}},
}


class InitError(RuntimeError):
    """init stopped; the message says why in plain words and what to do."""


# -- what the bench says about the agent ------------------------------------------------
def describe_me(me: dict[str, Any]) -> list[str]:
    agent = me.get("agent") if isinstance(me.get("agent"), dict) else {}
    owner = me.get("owner") if isinstance(me.get("owner"), dict) else None
    terms = me.get("terms") if isinstance(me.get("terms"), dict) else {}
    settings = me.get("settings") if isinstance(me.get("settings"), dict) else {}
    ident = agent.get("id") or agent.get("registry_no") or agent.get("maker_id") or "?"
    lines = [
        f"Agent:    {agent.get('handle') or agent.get('name') or '?'} ({ident})",
        f"Owner:    {(owner or {}).get('name') or 'none yet'}",
        f"Email:    {me.get('email') or 'none'}",
    ]
    if terms:
        accepted = "accepted" if terms.get("accepted") else "NOT accepted yet"
        lines.append(f"Terms:    version {terms.get('version') or '?'}, {accepted}"
                     + (f" ({terms['how']})" if terms.get("how") and not terms.get("accepted")
                        else ""))
    if settings:
        lines.append("Settings: " + ", ".join(f"{key} {value}" for key, value in
                                              settings.items() if not isinstance(value, dict)))
    return lines


def connect(bench: Bench) -> dict[str, Any]:
    """The reachability handshake (kept from 0.76): ack the bench's ping until it says reachable."""
    me = bench.me()
    acks = 0
    for _ in range(2):
        test = me.get("reachability_test") or {}
        if not isinstance(test, dict) or test.get("reachable") or test.get("reachable_at"):
            break
        me = bench.ack_ping()
        acks += 1
    test = me.get("reachability_test") if isinstance(me.get("reachability_test"), dict) else {}
    reachable = not test or bool(test.get("reachable") or test.get("reachable_at"))
    return {"ok": reachable, "acknowledgements": acks}


# -- init ------------------------------------------------------------------------------
def _old_token(path: Path, config: dict[str, Any]) -> str | None:
    """The token a 0.76 agent.yaml keeps, read from its own secret folder."""
    folder = (config.get("secrets") or {}).get("directory")
    name = (config.get("toll_bench") or {}).get("token_secret") or TOKEN_SECRET
    if not folder:
        return None
    try:
        return FileSecretStore(path.parent / folder).get(name)
    except (OSError, PermissionError, ValueError):
        return None


def _known(path: Path) -> dict[str, Any]:
    """What an agent.yaml already in this folder says (0.76 or 1.0), for defaults."""
    if not path.is_file():
        return {}
    config = read_yaml(path)
    if is_old_config(config):
        agent = config.get("agent") or {}
        model = config.get("model") or {}
        return {
            "old": True, "name": agent.get("name"), "company": agent.get("company"),
            "model": agent.get("intelligence_brand") or agent.get("intelligence"),
            "command": model.get("command") if model.get("adapter") == "external" else None,
            "url": (config.get("toll_bench") or {}).get("base_url"),
            "token": _old_token(path, config),
        }
    old = Agent(path, config)
    section = config.get("agent") or {}
    return {"name": section.get("name"), "company": section.get("company"),
            "model": section.get("model"), "command": section.get("command"),
            "url": old.bench_url, "token": old.token(), "config": config}


def registration(name: str, company: str, model: str, protocol: dict[str, Any], *,
                 contact_email: str | None = None, jurisdiction: str | None = None,
                 company_url: str | None = None) -> dict[str, Any]:
    brand = model[:BRAND_MAX].strip()
    disclosure: dict[str, Any] = {"is_ai": True, "model": model, "operator_label": company,
                                  "operator_type": "company" if company_url else "person"}
    if company_url:
        disclosure["company_url"] = company_url
    responsible = {"legal_name": company}
    if jurisdiction:
        responsible["jurisdiction"] = jurisdiction
    if contact_email:
        responsible["contact_ref"] = contact_email
    return {
        "handle": name,
        "skills": "Works in its own app; connected to Toll Bench by toll-harness.",
        "disclosure": disclosure,
        "intelligence": {"brand": brand},
        "system_record": {
            "base_models": [{"provider": model, "model": model, "version": model,
                             "brand": brand}],
            "autonomy": "fully_autonomous", "harness": "Toll Harness",
            "harness_version": __version__, "label": f"{name} toll-harness {__version__}",
        },
        "responsible_party": responsible,
        "rules": {"accepted": True, "version_hash": protocol.get("rules_version_hash")},
    }


def _register(bench: Bench, payload: dict[str, Any], connect_code: str | None) -> str:
    try:
        check = bench.validate_registration(payload)
    except BenchError as error:
        if error.unreachable:
            raise
        check = {"ok": False, "problems": error.body.get("problems")
                 or [{"message": error.message}]}
    if check.get("ok") is False or check.get("problems"):
        words = [str(problem.get("message") or problem.get("detail") or problem)
                 for problem in check.get("problems") or [] if problem]
        raise InitError("The bench did not take this registration:\n  - "
                        + "\n  - ".join(words or ["(no reason given)"])
                        + "\nOr register the agent on the website and run "
                          "`toll-harness init --registered` with its token in "
                        + TOKEN_ENV + ".")
    if connect_code:
        payload = {**payload, "connect_code": connect_code}
    answer = bench.register(payload, f"toll-harness-register-{uuid.uuid4()}")
    token = answer.get("rest_token")
    if not token:
        raise InitError("The bench registered the agent but sent no token back.")
    return str(token)


def init(directory: str | Path, *, registered: bool = False,
         ask: Callable[[str, str | None], str] | None = None,
         say: Callable[[str], None] = print, environ: dict[str, str] | None = None,
         answers: dict[str, str | None] | None = None,
         bench_factory: Callable[[str], Bench] = Bench,
         install_service: Callable[[Agent], dict[str, Any]] | None = None) -> Agent:
    import os

    environ = dict(os.environ if environ is None else environ)
    answers = dict(answers or {})
    path = Path(directory).expanduser().resolve()
    path = path if path.suffix in (".yaml", ".yml") else path / "agent.yaml"
    known = _known(path)
    url = str(environ.get(BASE_URL_ENV) or known.get("url") or DEFAULT_BENCH_URL)
    url = url.strip().rstrip("/")
    bench = bench_factory(url)
    token = known.get("token")
    if registered:
        token = (environ.get(TOKEN_ENV) or "").strip() or None
        if not token:
            raise InitError(f"--registered reads the agent's token from {TOKEN_ENV}; it is "
                            "empty here.")
    me: dict[str, Any] = {}
    if token:
        try:
            me = bench.with_token(token).me()
        except BenchError as error:
            raise InitError(f"The bench did not accept this agent's token ({error}).") from None
    on_record = me.get("agent") if isinstance(me.get("agent"), dict) else {}
    owner = me.get("owner") if isinstance(me.get("owner"), dict) else {}

    def question(key: str, label: str, default: Any) -> str:
        value = answers.get(key)
        if value is None:
            value = (ask or _ask)(label, str(default) if default else None)
        value = str(value or default or "").strip()
        if not value:
            raise InitError(f"{label}: an answer is needed.")
        return value

    if known.get("old"):
        say(f"Moving {path.parent.name} from toll-harness 0.x: it keeps its identity and token.")
    company = question("company", COMPANY, known.get("company") or owner.get("name"))
    model = question("model", MODEL, known.get("model"))
    default_command = known.get("command")
    if isinstance(default_command, list):
        default_command = shlex.join(default_command)
    command = checked_command(shlex.split(question("command", COMMAND, default_command)))
    if shutil.which(command[0]) is None and not Path(command[0]).exists():
        say(f"Note: `{command[0]}` is not on this PATH; the service uses the PATH it is "
            "installed with.")
    name = str(answers.get("name") or on_record.get("handle") or known.get("name")
               or path.parent.name or "agent")

    if not token:
        token = _register(bench, registration(
            name, company, model, bench.protocol(), contact_email=answers.get("contact_email"),
            jurisdiction=answers.get("jurisdiction"), company_url=answers.get("company_url"),
        ), answers.get("connect_code"))
        me = bench.with_token(token).me()

    if known.get("old"):
        backup = path.with_name("agent.0x.yaml")
        write_atomic(backup, path.read_text(encoding="utf-8"))
        say(f"The 0.x agent.yaml is kept as {backup.name}.")
    previous = known.get("config") or {}
    config = {
        "version": CONFIG_VERSION,
        "agent": {
            "name": name, "company": company, "model": model, "command": command,
            "time_limit_minutes": (previous.get("agent") or {}).get(
                "time_limit_minutes", DEFAULT_TIME_LIMIT_MINUTES),
        },
        "bench": {"url": url},
        "storage": previous.get("storage") or ".toll-harness",
    }
    if previous.get("update"):
        config["update"] = previous["update"]
    path.parent.mkdir(parents=True, exist_ok=True)
    agent = Agent(path, config)
    agent.save()
    agent.secrets.set(TOKEN_SECRET, token)
    agent_bench = agent.bench()
    handshake = connect(agent_bench)
    say("Connected to Toll Bench." if handshake["ok"]
        else "Connected, but the bench has not seen a reachability answer yet.")
    if install_service is None:
        from toll_harness import service

        install_service = service.install
    try:
        report = install_service(agent)
        say(f"Always-on service: {report.get('service')} "
            f"({'running' if report.get('running') else 'installed, not running yet'}).")
    except Exception as error:  # noqa: BLE001 - the agent is connected; say what is left
        say(f"The always-on service was not installed: {error}")
    try:
        me = agent_bench.me()
    except BenchError:
        pass
    say("\nWhat this agent has from Toll Bench:")
    for line in describe_me(me):
        say("  " + line)
    return agent


def _ask(label: str, default: str | None) -> str:
    suffix = f" [{default}]" if default else ""
    return input(f"{label}{suffix}: ").strip() or (default or "")


# -- doctor ----------------------------------------------------------------------------
def doctor(agent: Agent, *, say: Callable[[str], None] = print,
           service_status: Callable[[Agent], str] | None = None) -> bool:
    good = True

    def report(ok: bool, line: str) -> None:
        nonlocal good
        good = good and ok
        say(f"{'ok  ' if ok else 'FAIL'} {line}")

    bench = agent.bench(timeout=15)
    try:
        version = bench.version()
        report(True, f"bench reachable at {agent.bench_url} (harness current "
                     f"{version.get('current') or '?'}, you run {__version__})")
    except BenchError as error:
        try:
            bench.protocol()
            report(True, f"bench reachable at {agent.bench_url} (no version door yet: "
                         f"{error.status})")
        except BenchError as again:
            report(False, f"bench not reachable at {agent.bench_url}: {again.message}")
    try:
        me = bench.me()
        agent_part = me.get("agent") if isinstance(me.get("agent"), dict) else {}
        report(True, f"token good: the bench knows this agent as "
                     f"{agent_part.get('handle') or agent.name}")
    except BenchError as error:
        report(False, f"token: {error}")
    try:
        result = hand_off(agent.command, dict(DOCTOR_ITEM),
                          time_limit=min(agent.time_limit_seconds, 120),
                          config_path=str(agent.path), cwd=str(agent.path.parent))
        report(result.status in (ANSWERED, LATER),
               f"the agent's command {'answered' if result.status == ANSWERED else 'ran'} "
               f"a sample item in {result.seconds:.1f} s" if result.status != "failed"
               else f"the agent's command: {result.why}")
    except ConfigError as error:
        report(False, str(error))
    if service_status is None:
        from toll_harness import service

        service_status = service.status
    state = service_status(agent)
    report(state == "running", f"always-on service: {state}")
    return good
