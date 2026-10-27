#!/usr/bin/env bash
# Install a toll-harness wheel or published version onto one lab agent, behind the freshness check.
#
# What forced it: on 2026-10-26 a harness build started from a stale local copy at 0.68.0 while
# 0.69.0-0.72.0 were already published, and named itself "0.69.0" (taken). A wheel built locally
# like that could have been installed onto a live agent with no check at all.
#
# Usage:
#   scripts/install_on_agent.sh [--dry-run] [--checkout DIR] [--log FILE] [--timeout SECONDS]
#                               [--python PY] <agent-dir> <unit> <wheel-or-version>
#
#   scripts/install_on_agent.sh ~/lab/DJ4Fun-prod toll-harness-dj-4-fun-prod.service 0.73.0
#   scripts/install_on_agent.sh ~/lab/DJ4Fun-prod toll-harness-dj-4-fun-prod.service \
#       ~/toll-harness/dist/toll_harness-0.74.0-py3-none-any.whl
#
# What it does, in order (any failure stops it; nothing past step 1 runs on a failed check):
#   1. the freshness check on what is being installed (python -m toll_harness.freshness wheel):
#      a published version, PyPI's own file, or a newer wheel from a checkout that is not
#      behind origin/main. A network failure fails closed.
#   2. <agent-dir>/venv-<version>, and pip installs into it. Extras follow what the agent's
#      current python can import (boto3 -> aws, playwright -> browser, anthropic, openai).
#   3. a systemd user drop-in, <unit>.d/zzz-harness-<NNNN>.conf, that copies the ExecStart of the
#      unit's current winning ExecStart but points it at the new venv's python. Its name sorts
#      after every other drop-in there.
#   4. daemon-reload, a check that the unit now runs the new python, then a restart.
#   5. waits for the first "attention -> 200" in the agent's market.log written after the restart.
#   6. prints the one-line rollback.
#
# --dry-run runs the check and reads the unit, prints what it would do, and changes nothing.
# SYSTEMCTL (default systemctl) and SYSTEMD_USER_DIR (default ~/.config/systemd/user) can point
# somewhere else for testing.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/.." && pwd)"
systemctl_bin="${SYSTEMCTL:-systemctl}"
user_dir="${SYSTEMD_USER_DIR:-$HOME/.config/systemd/user}"

die() { printf 'install_on_agent: %s\n' "$*" >&2; exit 1; }
usage() { sed -n '8,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }
user_systemctl() { "$systemctl_bin" --user "$@"; }

dry_run=0 checkout="" log="" timeout=300 base_python=""
positional=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) dry_run=1; shift ;;
    --checkout) checkout="${2:?--checkout needs a directory}"; shift 2 ;;
    --log) log="${2:?--log needs a file}"; shift 2 ;;
    --timeout) timeout="${2:?--timeout needs seconds}"; shift 2 ;;
    --python) base_python="${2:?--python needs an interpreter}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    -*) usage >&2; die "unknown option: $1" ;;
    *) positional+=("$1"); shift ;;
  esac
done
[[ ${#positional[@]} -eq 3 ]] || { usage >&2; exit 2; }
[[ -d "${positional[0]}" ]] || die "no such agent directory: ${positional[0]}"
agent_dir="$(cd "${positional[0]}" && pwd)"
unit="${positional[1]}"
target="${positional[2]}"
if [[ "$target" == *.whl ]]; then
  [[ -f "$target" ]] || die "no such wheel: $target"
  target="$(cd "$(dirname "$target")" && pwd)/$(basename "$target")"
fi

freshness() { PYTHONPATH="$repo/src${PYTHONPATH:+:$PYTHONPATH}" python3 -m toll_harness.freshness "$@"; }

# 1. The freshness check, before anything is touched.
check_args=(wheel "$target")
[[ -n "$checkout" ]] && check_args+=(--checkout "$checkout")
freshness "${check_args[@]}" || die "the freshness check failed; nothing was changed."

if [[ "$target" == *.whl ]]; then
  version="$(PYTHONPATH="$repo/src" python3 -c 'import sys
from pathlib import Path
from toll_harness.freshness import wheel_metadata
print(wheel_metadata(Path(sys.argv[1]))[1])' "$target")"
  source_note="local wheel $target"
else
  version="$target"
  source_note="PyPI"
fi

# The unit's winning ExecStart (the last non-empty one systemd applies) and its log file.
unit_text="$(user_systemctl cat "$unit" 2>&1)" || die "systemctl --user cat $unit failed: $unit_text"
parsed_text="$(printf '%s\n' "$unit_text" | python3 -c '
import re, sys
exec_line = out = None
for raw in sys.stdin.read().splitlines():
    line = raw.strip()
    if line.startswith("ExecStart="):
        exec_line = line[len("ExecStart="):].strip() or None
    elif line.startswith(("StandardOutput=append:", "StandardOutput=file:")):
        out = line.split(":", 1)[1]
if not exec_line:
    sys.exit("the unit has no ExecStart")
match = re.match(r"^([-@:+!]*)(?:\"([^\"]*)\"|(\S+))(.*)$", exec_line)
if not match:
    sys.exit("cannot read the ExecStart: " + exec_line)
prefix, exe, rest = match.group(1), match.group(2) or match.group(3), match.group(4)
name = exe.rsplit("/", 1)[-1]
if name.startswith("python"):
    if "toll_harness" not in rest:
        sys.exit("the ExecStart runs " + exe + " without toll_harness; is this the right unit?")
    kind = "python"
elif name == "toll-harness":
    kind = "toll-harness"
else:
    sys.exit("the ExecStart runs " + exe + ", neither a python nor toll-harness; edit it by hand")
print(exe); print(kind); print(prefix); print(rest); print(out or ""); print("end")
')" || die "cannot use the unit's ExecStart (see above)."
mapfile -t parsed <<< "$parsed_text"
current_exe="${parsed[0]}" exe_kind="${parsed[1]}" exec_prefix="${parsed[2]}" exec_rest="${parsed[3]}"
[[ -n "$log" ]] || log="${parsed[4]}"
if [[ -z "$log" ]]; then
  log="$(ls -t "$agent_dir"/.toll-harness/*/market.log 2>/dev/null | head -n 1 || true)"
fi
[[ -n "$log" ]] || die "cannot find the agent's market.log; pass --log FILE."

current_python="$current_exe"
[[ "$exe_kind" == "toll-harness" ]] && current_python="$(dirname "$current_exe")/python"
if [[ -z "$base_python" ]]; then
  base_python="$("$current_python" -c 'import sys; print(getattr(sys, "_base_executable", "") or sys.executable)' 2>/dev/null || true)"
  [[ -n "$base_python" ]] || base_python="$(command -v python3)"
fi
extras="$("$current_python" -c 'import importlib.util as u
pairs = (("aws", "boto3"), ("browser", "playwright"), ("anthropic", "anthropic"), ("openai", "openai"))
print(",".join(extra for extra, module in pairs if u.find_spec(module)))' 2>/dev/null || true)"
extras_spec="${extras:+[$extras]}"

venv="$agent_dir/venv-$version"
if [[ "$target" == *.whl ]]; then
  pip_args=(install --quiet --force-reinstall "$target$extras_spec")
else
  pip_args=(install --quiet "toll-harness$extras_spec==$version")
fi
new_exe="$venv/bin/python"
[[ "$exe_kind" == "toll-harness" ]] && new_exe="$venv/bin/toll-harness"
new_exec="$exec_prefix\"$new_exe\"$exec_rest"

# The drop-in's name: zzz-harness-0730.conf for 0.73.0 (the lab's naming), with more z's in
# front if anything already in the folder would sort after it, because the last one wins.
dropin_dir="$user_dir/$unit.d"
compact="$(python3 -c 'import re, sys
m = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?(.*)$", sys.argv[1])
print("%d%02d%d%s" % (int(m[1]), int(m[2]), int(m[3] or 0), re.sub(r"[^A-Za-z0-9]", "", m[4])) if m
      else re.sub(r"[^A-Za-z0-9]", "", sys.argv[1]))' "$version")"
dropin_name="zzz-harness-$compact.conf"
if [[ -d "$dropin_dir" ]]; then
  for _ in 1 2 3 4 5 6 7 8; do
    last="$(cd "$dropin_dir" && ls -1 -- *.conf 2>/dev/null | grep -vxF -- "$dropin_name" | LC_ALL=C sort | tail -n 1 || true)"
    if [[ -z "$last" ]] || [[ "$(printf '%s\n%s\n' "$last" "$dropin_name" | LC_ALL=C sort | tail -n 1)" == "$dropin_name" ]]; then
      break
    fi
    dropin_name="z$dropin_name"
  done
fi
dropin="$dropin_dir/$dropin_name"
rollback="rm '$dropin' && systemctl --user daemon-reload && systemctl --user restart '$unit'"
dropin_text="# toll-harness $version ($source_note), written $(date -u +%Y-%m-%dT%H:%MZ) by scripts/install_on_agent.sh
# after the freshness check passed. It sorts after every other drop-in here, so its ExecStart wins.
# Roll back: $rollback
[Service]
ExecStart=
ExecStart=$new_exec"

if [[ "$dry_run" -eq 1 ]]; then
  cat <<EOF
DRY RUN: the freshness check passed; nothing below was done.
  agent:    $agent_dir
  unit:     $unit (now runs $current_exe)
  venv:     $venv (from $base_python)
  install:  $venv/bin/pip ${pip_args[*]}
  drop-in:  $dropin
-----
$dropin_text
-----
  then:     systemctl --user daemon-reload, check the unit runs $new_exe, systemctl --user restart $unit
  wait:     the first "attention -> 200" in $log after the restart (up to ${timeout}s)
  rollback: $rollback
EOF
  exit 0
fi

# 2. The venv.
if [[ ! -x "$venv/bin/python" ]]; then
  "$base_python" -m venv "$venv" || die "could not make $venv"
fi
"$venv/bin/pip" "${pip_args[@]}" || die "pip install into $venv failed; the agent was not touched."
installed="$("$venv/bin/python" -c 'import importlib.metadata as m; print(m.version("toll-harness"))')"
[[ "$installed" == "$version" ]] || die "$venv holds toll-harness $installed, not $version; the agent was not touched."

# 3. The drop-in, and 4. reload, confirm, restart.
mkdir -p "$dropin_dir"
printf '%s\n' "$dropin_text" > "$dropin"
user_systemctl daemon-reload
running="$(user_systemctl show -p ExecStart --value "$unit")"
if [[ "$running" != *"$new_exe"* ]]; then
  rm -f "$dropin"
  user_systemctl daemon-reload
  die "after daemon-reload $unit still does not run $new_exe (another drop-in wins?); removed $dropin, the agent was not restarted."
fi
offset=0
[[ -f "$log" ]] && offset="$(stat -c %s "$log")"
user_systemctl restart "$unit"

# 5. The first clean attention after the restart.
deadline=$(( $(date +%s) + timeout ))
while :; do
  if [[ -f "$log" ]]; then
    size="$(stat -c %s "$log")"
    (( size < offset )) && offset=0
    hit="$(tail -c +"$((offset + 1))" "$log" | grep -m 1 -E 'attention -> 200' || true)"
    if [[ -n "$hit" ]]; then
      echo "$unit runs toll-harness $version from $venv."
      echo "  first clean attention: $hit"
      echo "Roll back: $rollback"
      exit 0
    fi
  fi
  state="$(user_systemctl is-active "$unit" || true)"
  if [[ "$state" == "failed" ]] || (( $(date +%s) >= deadline )); then
    echo "install_on_agent: $unit did not answer attention -> 200 (state: $state, waited up to ${timeout}s)." >&2
    [[ -f "$log" ]] && tail -c +"$((offset + 1))" "$log" | tail -n 20 >&2
    echo "Roll back: $rollback" >&2
    exit 1
  fi
  sleep 2
done
