"""toll-harness: init, watch, answer, connect, doctor, update, logs.

A 0.x service line (`market watch <agent.yaml>`) still runs: it is the watch, and the
watch moves a 0.x agent by itself (move_in_place)."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time

from toll_harness import __version__
from toll_harness.agent import ConfigError, config_path, is_old_config, load, read_yaml
from toll_harness.bench import BenchError
from toll_harness.known import KNOWN


def _init(arguments: argparse.Namespace) -> int:
    from toll_harness.onboarding import InitError, init

    answers = {key: getattr(arguments, key) for key in (
        "company", "model", "agent", "command", "name", "contact_email", "location",
        "company_url", "connect_code")}
    try:
        init(arguments.directory, registered=arguments.registered, answers=answers)
    except (InitError, ConfigError) as error:
        print(str(error), file=sys.stderr)
        return 2
    except BenchError as error:
        print(f"Toll Bench: {error}", file=sys.stderr)
        return 2
    return 0


MOVE_RETRY_SECONDS = 600  # a move whose sample wake failed is tried again this much later


def move_in_place(config: str | None, *, say=print, sleep=time.sleep) -> int | None:
    """THE UPGRADE: the watch meets an agent.yaml written by toll-harness 0.x.

    Steven, Nov 11: "we just need to make it work... we upgrade them all." The old service
    keeps running `python -m toll_harness.cli market watch <agent.yaml>`; after the normal
    `pip install -U toll-harness` it starts this 1.0 watch, which moves the agent here, by
    itself: the same token (so the same agent, owner, terms and card on Toll Bench), the same
    agent woken the same way (known.wake_from_old), a sample wake first, the old file kept as
    agent.0x.yaml. None: moved (or nothing to move), go on watching. An int: the exit code,
    after plain words on the service's log saying the one thing the owner has to do."""
    from toll_harness.onboarding import InitError, init

    path = config_path(config)
    if not path.is_file() or not is_old_config(read_yaml(path)):
        return None
    asked: list[str] = []

    def no_questions(label: str, default: str | None) -> str:
        asked.append(label)
        raise InitError(f"it needs an answer: {label.split(' (')[0]}")

    try:
        init(path, ask=no_questions, say=say, answers={},
             install_service=lambda agent: {"service": "the one already running this watch",
                                            "running": True})
        say("Moved to toll-harness " + __version__ + "; watching.")
        return None
    except (InitError, ConfigError, BenchError) as error:
        say(f"toll-harness {__version__} could not move this agent by itself: {error}")
        say("The agent takes no items until this is done. On this computer run: "
            f"toll-harness init {path.parent}")
        say("(To go back instead: pip install toll-harness==0.76.2, then restart the service.)")
        if asked:
            return 0  # a question only the owner can answer: stop until they do
        sleep(MOVE_RETRY_SECONDS)  # the wake or the bench failed: try again later
        return 1


def _watch(arguments: argparse.Namespace) -> int:
    from toll_harness.watch import Watch

    def stop(*_):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    stopped = move_in_place(arguments.config)
    if stopped is not None:
        return stopped
    return Watch(load(arguments.config), wait=arguments.wait).run()


def _answer(arguments: argparse.Namespace) -> int:
    from toll_harness.logs import ActivityLog
    from toll_harness.watch import send_answer

    agent = load(arguments.config)
    try:
        answer = json.loads(sys.stdin.read())
    except ValueError:
        print("The answer on stdin is not JSON.", file=sys.stderr)
        return 2
    if not isinstance(answer, dict):
        print("The answer on stdin must be a JSON object.", file=sys.stderr)
        return 2
    outcome = send_answer(agent.bench(), arguments.item_id, answer,
                          ActivityLog(agent.data_dir, echo=False))
    print(outcome)
    return 0 if outcome == "accepted" else 1


def _connect(arguments: argparse.Namespace) -> int:
    from toll_harness.onboarding import connect

    result = connect(load(arguments.config).bench())
    print("reachable" if result["ok"] else "not reachable yet")
    return 0 if result["ok"] else 2


def _doctor(arguments: argparse.Namespace) -> int:
    from toll_harness.onboarding import doctor

    return 0 if doctor(load(arguments.config)) else 1


def _update(arguments: argparse.Namespace) -> int:
    from toll_harness import service
    from toll_harness.logs import ActivityLog
    from toll_harness.updates import check_and_install, decide

    agent = load(arguments.config)
    bench = agent.bench()
    if arguments.check:
        print(json.dumps(decide(bench.version()), indent=1))
        return 0
    result = check_and_install(agent, bench, ActivityLog(agent.data_dir), force=True)
    if result.get("installed_now"):
        try:
            service.restart(agent)
            print("The service restarted on the new version.")
        except service.ServiceError as error:
            print(f"Installed; restart the watch yourself: {error}")
        return 0
    if result.get("action") == "none":
        print(f"toll-harness {__version__} is current.")
        return 0
    return 1


def _logs(arguments: argparse.Namespace) -> int:
    from toll_harness.logs import tail

    for line in tail(load(arguments.config).data_dir, arguments.lines):
        print(line)
    return 0


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="toll-harness", description=(
        "The Toll Bench harness: it hands each item to your agent's own command and sends "
        "the answer back. Your agent's settings live on its card on Toll Bench."))
    top.add_argument("--version", action="version", version=f"toll-harness {__version__}")
    commands = top.add_subparsers(dest="name", required=True)

    init = commands.add_parser("init", help="set up and connect this agent")
    init.add_argument("directory", nargs="?", default=".")
    init.add_argument("--registered", action="store_true",
                      help="the agent registered already; its token is in "
                           "TOLL_HARNESS_AGENT_TOKEN")
    for flag, text in (("--company", "the company"), ("--model", "the model's name"),
                       ("--agent", "the agent the harness wakes, by name: "
                                   + ", ".join(k.name for k in KNOWN)),
                       ("--command", "only for an agent the harness does not know: the "
                                     "command that wakes it"),
                       ("--name", "the agent's handle (default: the folder's name)"),
                       ("--contact-email", "contact email, for a new registration"),
                       ("--location", "location: country, and state if in the US (e.g. US-OR)"),
                       ("--company-url", "the company's public URL"),
                       ("--connect-code", "a connect code from your Toll Bench account")):
        init.add_argument(flag, help=text)
    init.add_argument("--jurisdiction", dest="location", help=argparse.SUPPRESS)  # old name
    init.set_defaults(run=_init)

    watch = commands.add_parser("watch", help="the always-on loop (what the service runs)")
    watch.add_argument("config", nargs="?")
    watch.add_argument("--wait", type=int, default=55, help=argparse.SUPPRESS)
    watch.set_defaults(run=_watch)

    answer = commands.add_parser("answer", help="send an answer later (JSON on stdin)")
    answer.add_argument("item_id")
    answer.add_argument("--config")
    answer.set_defaults(run=_answer)

    for name, run, text in (("connect", _connect, "the reachability handshake"),
                            ("doctor", _doctor, "check the bench, the token and the command"),
                            ("logs", _logs, "the harness's recent activity lines"),
                            ("update", _update, "install a newer harness now and restart")):
        command = commands.add_parser(name, help=text)
        command.add_argument("config", nargs="?")
        command.set_defaults(run=run)
        if name == "logs":
            command.add_argument("-n", "--lines", type=int, default=40)
        if name == "update":
            command.add_argument("--check", action="store_true", help="only say what is out")
    return top


_OLD_VALUE_FLAGS = ("--wait", "--interval", "--scan-interval", "--config")


def from_0x(argv: list[str]) -> list[str]:
    """The line a 0.x service runs, `market watch <agent.yaml> [--scan-interval N]`, is the
    1.0 `watch <agent.yaml>`: an upgraded agent's own service keeps working unchanged."""
    if argv[:2] != ["market", "watch"]:
        return argv
    rest, config, index = argv[2:], None, 0
    while index < len(rest):
        part = rest[index]
        if part == "--config" and index + 1 < len(rest):
            config = rest[index + 1]
        if part in _OLD_VALUE_FLAGS:
            index += 2
            continue
        if not part.startswith("-") and config is None:
            config = part
        index += 1
    return ["watch"] + ([config] if config else [])


def main(argv: list[str] | None = None) -> None:
    argv = from_0x(list(sys.argv[1:] if argv is None else argv))
    arguments = parser().parse_args(argv)
    try:
        code = arguments.run(arguments)
    except ConfigError as error:
        print(str(error), file=sys.stderr)
        code = 2
    except KeyboardInterrupt:
        code = 130
    sys.exit(code)


if __name__ == "__main__":
    main()
