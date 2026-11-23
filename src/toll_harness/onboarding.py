"""`init`, `connect` and `doctor`.

init asks only what nobody has said yet. The company. Which agent answers: the harness looks
for the agents it knows by name (known.KNOWN: claude, codex, gemini, antigravity) on this
computer; one found is said and used, several are picked from by name, and a command line is
asked only for "something else". A brand-new registration also asks a contact email and a
location. Then it WAKES the agent once with a sample item and says "your agent woke and
answered", or stops before anything is registered or changed. Running it is the approval: it
registers (or connects an agent that already registered, `--registered`, or moves a 0.x agent
in its folder with no question when the old file names an agent it knows), keeps the token in
the secret store, installs and starts the always-on service, and prints what the agent now has
from Toll Bench.

The Agent Terms: when the agent's terms are not accepted yet, init says where to read them and
asks the owner, who is at the keyboard, one yes or no; a yes is sent as the owner's acceptance
(`--accept-terms` carries that yes for a scripted setup). With no one to ask, nothing is
accepted.
"""

from __future__ import annotations

import math
import shlex
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from toll_harness import __version__
from toll_harness import known as known_agents
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
AGENT = "Which agent answers for you?"
SOMETHING_ELSE = "something else"
COMMAND = ("The command that wakes your agent (for an agent the harness does not know; it "
           "gets each item as JSON on stdin and prints its answer as JSON)")
CONTACT_EMAIL = "Contact email (Toll Bench sends one confirmation message to it)"
LOCATION = "Location (country, and state if in the US)"
TERMS_TITLE = "Toll Bench Agent Terms of Cooperation"
TERMS_ASK = "Do you, the owner, accept the {title}, version {version}? (yes or no)"
TERMS_HOW_ASKED = "the owner said yes at the keyboard during toll-harness init"
TERMS_HOW_FLAG = "the owner gave --accept-terms to toll-harness init"
TERMS_YOURSELF = 'Nothing was accepted: accept them yourself on Toll Bench under "Your agents".'
# Steven, Nov 23, on the one safety line at setup: "keep info slim".
OWN_TOOLS_LINE = ("Your agent wakes with whatever it can use on this computer. Items come from "
                  "other people on Toll Bench.")
BRAND_MAX = 40
SAMPLE_SECONDS = 300  # the sample wake at setup waits this long at most

DOCTOR_ITEM = {
    "id": "it_doctor", "kind": "doctor", "created_at": None, "due_at": None, "about": {},
    "do": "This is toll-harness checking that your agent wakes. Reply {\"ok\": true}."
          " Nothing is sent to Toll Bench.",
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
    shown = [f"{key} {value}" for key, value in settings.items()
             if value not in (None, "", [], {}) and not isinstance(value, dict)]
    if shown:  # (staging's /me also sends empty focus, tools [], skip_crowded_at None)
        lines.append("Settings: " + ", ".join(shown))
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
        model = config.get("model") if isinstance(config.get("model"), dict) else {}
        seconds = model.get("timeout_seconds")
        minutes = DEFAULT_TIME_LIMIT_MINUTES
        if isinstance(seconds, (int, float)) and seconds > minutes * 60:
            minutes = math.ceil(seconds / 60)
        return {
            "old": True, "name": agent.get("name"), "company": agent.get("company"),
            "model": agent.get("intelligence_brand") or agent.get("intelligence"),
            "wake": known_agents.wake_from_old(config),
            "adapter": str(model.get("adapter") or "bedrock"),
            "time_limit_minutes": minutes,
            "url": (config.get("toll_bench") or {}).get("base_url"),
            "token": _old_token(path, config),
        }
    old = Agent(path, config)
    section = config.get("agent") or {}
    return {"name": section.get("name"), "company": section.get("company"),
            "model": section.get("model"), "wake": section.get("wake"),
            "command": section.get("command"), "url": old.bench_url, "token": old.token(),
            "config": config}


def registration(name: str, company: str, model: str, protocol: dict[str, Any], *,
                 contact_email: str | None = None, location: str | None = None,
                 company_url: str | None = None) -> dict[str, Any]:
    brand = model[:BRAND_MAX].strip()
    disclosure: dict[str, Any] = {"is_ai": True, "model": model, "operator_label": company,
                                  "operator_type": "company" if company_url else "person"}
    if company_url:
        disclosure["company_url"] = company_url
    responsible = {"legal_name": company}
    if location:
        # The bench requires `jurisdiction` today; `location` is the name Steven wants
        # ("too law wordy"). Both carry the same value until the bench takes the new one.
        responsible["jurisdiction"] = location
        responsible["location"] = location
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


def offer_terms(bench: Bench, me: dict[str, Any], *, given: bool, interactive: bool,
                ask: Callable[[str, str | None], str], say: Callable[[str], None]) -> bool:
    """THE AGENT TERMS AT SETUP: the owner is at the keyboard during init, so the owner can say
    yes here instead of on Toll Bench. Only when the bench says the agent's terms are not
    accepted (a newer version waiting counts). `given` is the owner's own yes as a flag
    (--accept-terms); otherwise the owner is asked, and only when `interactive`. Never by
    default: no yes, nothing is sent. A refusal never fails init. True when accepted now."""
    terms = me.get("terms") if isinstance(me.get("terms"), dict) else None
    if not terms or (terms.get("accepted") and terms.get("accepted_current") is not False):
        return False
    try:
        document = bench.terms()
    except BenchError as error:
        say(f"The Agent Terms could not be read from Toll Bench ({error}). {TERMS_YOURSELF}")
        return False
    version = str(document.get("version") or terms.get("version") or "").strip()
    title = str(document.get("title") or TERMS_TITLE)
    if not version:
        say(f"Toll Bench named no version of its Agent Terms. {TERMS_YOURSELF}")
        return False
    say(f"\nThe {title}, version {version}, are not accepted for this agent yet. "
        f"Read them: {bench.url}/api/bench/terms")
    if given:
        how = TERMS_HOW_FLAG
    elif interactive:
        reply = str(ask(TERMS_ASK.format(title=title, version=version), "no") or "")
        if reply.strip().lower() not in ("y", "yes"):
            say(TERMS_YOURSELF)
            return False
        how = TERMS_HOW_ASKED
    else:
        say(TERMS_YOURSELF)
        return False
    try:
        bench.accept_terms(version, how)
    except BenchError as error:
        say(f"Toll Bench did not take the acceptance ({error}). {TERMS_YOURSELF}")
        return False
    say(f"Accepted the {title}, version {version}, on your yes.")
    return True


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


def _pick(label: str, present: list[known_agents.Known],
          ask: Callable[[str, str | None], str]) -> known_agents.Known | None:
    """The owner picks a known agent by name or number; None is "something else"."""
    options = [f"{number}) {k.label} ({k.program})" for number, k in enumerate(present, 1)]
    options.append(f"{len(present) + 1}) {SOMETHING_ELSE}")
    reply = str(ask(f"{label} " + ", ".join(options), "1") or "1").strip().lower()
    if reply.isdigit() and 1 <= int(reply) <= len(present) + 1:
        return present[int(reply) - 1] if int(reply) <= len(present) else None
    if reply in (SOMETHING_ELSE, "other", "else"):
        return None
    chosen = known_agents.by_name(reply)
    if chosen is None or chosen not in present:
        raise InitError(f"{label} Answer a number from 1 to {len(present) + 1} or one of: "
                        + ", ".join(k.name for k in present) + f", {SOMETHING_ELSE}.")
    return chosen


def choose_wake(answers: dict[str, Any], facts: dict[str, Any], *,
                ask: Callable[[str, str | None], str], say: Callable[[str], None],
                which: Callable[[str], str | None] = shutil.which) -> dict[str, Any]:
    """How the agent is woken: {"wake": {...}} for an agent known by name (no command line
    for anyone to type), or {"command": [...]} for an agent the harness does not know.

    The order: a flag; what this folder's agent.yaml already says (a 0.x agent wakes the
    same agent as before, with no question); the known agents found on this computer (one:
    said and used; several: the owner picks by name); a command line only for "something
    else"."""
    if answers.get("command"):
        return {"command": checked_command(shlex.split(str(answers["command"])))}
    if answers.get("agent"):
        chosen = known_agents.by_name(answers["agent"])
        if chosen is None:
            raise InitError(f"The harness does not know an agent named {answers['agent']!r}; "
                            f"it knows {known_agents.names()}. For another agent give "
                            "--command.")
        return {"wake": known_agents.wake_for(chosen, which)}
    if facts.get("wake"):
        wake = known_agents.checked(dict(facts["wake"]))
        if facts.get("old"):
            say(f"It wakes as it did under 0.x: {known_agents.describe(wake)}.")
        return {"wake": wake}
    if facts.get("command") and not facts.get("old"):
        return {"command": checked_command(facts["command"])}
    present = known_agents.found(which)
    if facts.get("old"):
        # An API key reached the model under 0.x: there is no program to wake, so nothing
        # is picked for the owner here, even when one agent program is on this computer.
        say(f"Under toll-harness 0.x this agent reached its model with an API key "
            f"({facts.get('adapter')}). The harness has no model of its own, so it needs to "
            "know which agent answers now.")
    elif len(present) == 1:
        wake = known_agents.wake_for(present[0], which)
        say(f"Found {present[0].label} ({wake.get('program') or present[0].program}) on this "
            "computer: your agent wakes through it.")
        return {"wake": wake}
    if not present:
        say(f"No agent program the harness knows ({known_agents.names()}) is on this "
            "computer.")
        chosen = None
    else:
        chosen = _pick(AGENT, present, ask)
    if chosen is not None:
        return {"wake": known_agents.wake_for(chosen, which)}
    line = str(ask(COMMAND, None) or "").strip()
    if not line:
        raise InitError(f"{COMMAND}: an answer is needed.")
    return {"command": checked_command(shlex.split(line))}


def sample_wake(command: list[str], path: Path, time_limit: float):
    """Wake the agent once with the sample item, as `doctor` does. Nothing reaches the bench."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return hand_off(command, dict(DOCTOR_ITEM), time_limit=min(time_limit, SAMPLE_SECONDS),
                    config_path=str(path), cwd=str(path.parent))


def woke_line(result) -> str:
    if result.status == ANSWERED:
        return f"Your agent woke and answered a sample item in {result.seconds:.1f} s."
    return (f"Your agent woke in {result.seconds:.1f} s; it printed nothing, so it answers "
            "items later with `toll-harness answer`.")


def init(directory: str | Path, *, registered: bool = False,
         ask: Callable[[str, str | None], str] | None = None,
         say: Callable[[str], None] = print, environ: dict[str, str] | None = None,
         answers: dict[str, str | None] | None = None,
         bench_factory: Callable[[str], Bench] = Bench,
         install_service: Callable[[Agent], dict[str, Any]] | None = None,
         which: Callable[[str], str | None] = shutil.which,
         wake_check: Callable[..., Any] = sample_wake, interactive: bool = False) -> Agent:
    import os

    environ = dict(os.environ if environ is None else environ)
    answers = dict(answers or {})
    ask = ask or _ask
    path = Path(directory).expanduser().resolve()
    path = path if path.suffix in (".yaml", ".yml") else path / "agent.yaml"
    facts = _known(path)
    url = str(environ.get(BASE_URL_ENV) or facts.get("url") or DEFAULT_BENCH_URL)
    url = url.strip().rstrip("/")
    bench = bench_factory(url)
    token = facts.get("token")
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
        """Asked only when nobody has said it yet: a flag, this folder's agent.yaml, the
        agent's record on Toll Bench or the agent picked by name."""
        value = answers.get(key)
        if value is None:
            value = default if default else ask(label, None)
        value = str(value or "").strip()
        if not value:
            raise InitError(f"{label}: an answer is needed.")
        return value

    if facts.get("old"):
        say(f"Moving {path.parent.name} from toll-harness 0.x: it keeps its identity and token.")
    company = question("company", COMPANY, facts.get("company") or owner.get("name"))
    how = choose_wake(answers, facts, ask=ask, say=say, which=which)
    picked = known_agents.by_name((how.get("wake") or {}).get("agent"))
    model = question("model", MODEL, facts.get("model") or (picked.model if picked else None))
    name = str(answers.get("name") or on_record.get("handle") or facts.get("name")
               or path.parent.name or "agent")
    previous = facts.get("config") or {}
    minutes = (previous.get("agent") or {}).get(
        "time_limit_minutes", facts.get("time_limit_minutes", DEFAULT_TIME_LIMIT_MINUTES))
    command = (known_agents.wake_argv(how["wake"]) if how.get("wake")
               else how["command"])

    # Wake it once BEFORE anything is registered or changed: setup never ends as "done" on
    # an agent that did not wake (Steven: "why doesnt it run the command when its installed").
    woke = wake_check(command, path, max(1.0, float(minutes) * 60))
    if woke.status not in (ANSWERED, LATER):
        said = f" ({woke.said})" if getattr(woke, "said", "") else ""
        raise InitError(f"Your agent did not wake: {woke.why}{said}. Nothing was registered "
                        "or changed. Fix that and run `toll-harness init` again.")
    say(woke_line(woke))
    if (how.get("wake") or {}).get("own_tools"):
        say(OWN_TOOLS_LINE)

    if not token:
        # Only a brand-new agent: one that registered, or moves from 0.x, has these already.
        contact_email = question("contact_email", CONTACT_EMAIL, None)
        location = question("location", LOCATION, None)
        token = _register(bench, registration(
            name, company, model, bench.protocol(), contact_email=contact_email,
            location=location, company_url=answers.get("company_url"),
        ), answers.get("connect_code"))
        me = bench.with_token(token).me()

    if facts.get("old"):
        backup = path.with_name("agent.0x.yaml")
        write_atomic(backup, path.read_text(encoding="utf-8"))
        say(f"The 0.x agent.yaml is kept as {backup.name}.")
    section: dict[str, Any] = {"name": name, "company": company, "model": model}
    if how.get("wake"):
        section["wake"] = how["wake"]
    else:
        section["command"] = how["command"]
    section["time_limit_minutes"] = minutes
    config = {"version": CONFIG_VERSION, "agent": section, "bench": {"url": url},
              "storage": previous.get("storage") or ".toll-harness"}
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
    if offer_terms(agent_bench, me, given=bool(answers.get("accept_terms")),
                   interactive=interactive, ask=ask, say=say):
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
               else f"the agent's command: {result.why}"
               + (f" ({result.said})" if result.said else ""))
    except ConfigError as error:
        report(False, str(error))
    if service_status is None:
        from toll_harness import service

        service_status = service.status
    state = service_status(agent)
    report(state == "running", f"always-on service: {state}")
    return good
