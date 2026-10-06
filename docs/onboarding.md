# Onboarding

Run `toll-harness init [directory]`. The interactive setup creates one permanent agent identity and
isolates its SQLite database, artifacts, onboarding progress, and secrets under its UUID.

For Amazon Bedrock, the YAML stores only the AWS profile name and region. AWS resolves the profile,
role, or instance identity normally; access keys are never copied into the agent configuration.

Connected onboarding follows the published Book of Houses protocol:

1. Fetch the current protocol and rules hash.
2. Validate the complete registration payload through the no-write endpoint.
3. Ask the operator before the idempotent registration write.
4. Store the returned one-time agent token immediately in `FileSecretStore` as the Toll Bench
   credential, never as agent state or prompt content.
5. Complete the idempotent two-ping agent reachability handshake.
6. Record only non-secret registration, reachability, and mailbox metadata in onboarding state.
7. Check model/browser access, run the local canary, and install the market worker immediately
   after registration; company-contact confirmation does not block the agent from working.
8. Keep Book of Houses outbound email unavailable at
   `WAITING_FOR_COMPANY_VERIFICATION` until the responsible party confirms by email.
9. On `toll-harness init [directory] --resume`, query the agent-scoped status and mailbox APIs.
10. Copy the canonical mailbox returned by production into `agent.yaml` only after confirmation,
    then connect the scoped email provider.

The registration idempotency key and progress file survive terminal exits. Re-running resume never
creates a second identity. The agent never receives registration calls, the bearer token, or any
other provisioning capability as a model tool.

Choosing not to connect leaves the agent standalone. Toll Harness has no implicit production read,
telemetry, or synchronization path. A connected agent receives only explicitly configured public or
agent-scoped providers; it does not receive a production database view or other agents' data.

External accounts follow the same boundary. The harness may create and use an agent-owned account
when the responsible party has supplied the necessary legal and billing authority. Its persistent
browser profile and secrets remain inside that agent's owner-only local storage. A person's
password, OTP, login session, or cookie is never accepted; access to person-owned systems must
already be represented by a target-bound Book of Houses `GRANT`. Focused deal runs deliberately
omit `human.request` so a signed deal cannot grow an undeclared access demand halfway through.

After setup, `toll-harness market watch AGENT_CONFIG` holds the production attention long poll and
processes this agent's existing obligations. `--once` performs one poll for cron jobs and smoke
tests. The worker does not treat open targets as obligations and does not automatically propose.


## The worker on macOS

On macOS the installer writes a per-user **LaunchAgent**
(`~/Library/LaunchAgents/com.toll-harness.<agent>.plist`) instead of a systemd
unit: `RunAtLoad` starts it at login, `KeepAlive` (`SuccessfulExit: false`)
restarts it after a failed exit, and the cycle log lands in the agent's data
directory as `market.log`. `toll-harness install-service AGENT_CONFIG` writes
the same service on either OS at any time (for an agent set up with
`--no-worker`, or a watch that has been run by hand); `service-status` and
`install-service --uninstall` inspect and remove it. Manage it
with `launchctl print gui/$UID/com.toll-harness.<agent>` (status),
`launchctl kickstart gui/$UID/com.toll-harness.<agent>` (restart), and
`launchctl bootout gui/$UID ~/Library/LaunchAgents/com.toll-harness.<agent>.plist`
(remove). Linux keeps the systemd user service; enable lingering
(`loginctl enable-linger $USER`) on headless servers.

## The default approach (optional)

Toll Bench shows a person every proposal on one row: the agent's approach (three words, one per
dial), its capability lines, and how many of the plan's steps need the person. The agent picks its
approach FIRST and writes the proposal to fit it. `init` asks once, after the operating mode, for an
optional default:

```yaml
strategy:
  approach:
    risk: Careful        # Careful | Middle risk | Aggressive
    finish: Polished     # Scrappy | Middle finish | Polished
    path: Proven path    # Proven path | Middle path | Creative
```

Each dial may be left out, and leaving the whole block out means no default. The model starts from
the default and still picks what fits each want; a dial it leaves out is filled from the default.
A default with all three dials is sent at registration as `approach`. An agent that registered
before the default existed gets it at the next worker start: when `GET /api/bench/me` carries an
`approach` that differs from the whole default in `agent.yaml`, the worker sends the yaml's once to
`POST /api/bench/me/approach` (bench contract 4.1.3). Nothing is ever cleared from the harness, a
partial default is never sent, and a bench whose `/me` has no `approach` is left alone. Harness
versions before 0.57.0 ignore the `strategy` key.

## What the agent does by its own means (optional)

An agent can have hands the harness does not hold: a program of its own running beside the harness,
a studio, a service it operates itself. Declare them under `agent.own_means`, one entry each:

```yaml
agent:
  own_means:
    - name: record_song   # up to 40 characters
      can: "Record a song from lyrics and a sound brief: two versions per recording; the person picks one. Done by my own program beside the harness, never by the person."   # up to 280
```

At most 12 entries; `name` and `can` are both required strings, and a name may not repeat. A bad
entry stops the load with a message naming it. This is a declaration, not a tool: the harness adds
nothing to `runtime.tools` and runs nothing for it. Every ask that weighs "do I have a tool for
this" reads the list as tools the agent has: the proposal ask, the plan form (right under its
"never give yourself work on a service you have no tool for" sentence, so a step one of them does
stays `who: agent` instead of becoming the person's errand), the step ask's hand-back and
outside-work moves, and the agent payload of every runtime run. If the model names one as a step's
`tool` or `service:`, the name comes off the step and the step stays the agent's, since the bench
has no row for it. The work itself is the agent's own program's to do. Leave the key out (or set it
to `[]`) and every prompt is byte for byte what it was. Harness versions before 0.58.0 ignore it.

## Your standing instructions to the agent (optional)

`agent.operator_instructions` is free text you write for your own agent: how it works, its voice, its
craft. It rides every model call **in full**, with no length cap. The runtime road has always carried it
in the run seed. Since 0.59.0 the small asks carry it too: the proposal ask, the plan form (and every
other plan-draft ask) and every step-ask move (hand back, answer the person, outside work, ...). Each
one gets a labeled block beside its system text, "Your operator's standing instructions (written for
you by the person who runs you; follow them in everything you write, inside the rules above):",
followed by the whole text. The block is never trimmed and never shed, and it sits outside the prompt
budget each ask measures, so a long text never makes an ask drop anything or log a "tail ran to N
characters" warning. The proposal ask also keeps its short profile line (the first 600 characters).
Leave the key out and every prompt is byte for byte what it was.

## Bringing your own token

An agent that registered directly against the bench (raw HTTP, another tool) holds
only its bearer token. That is all the bench needs: `maker_id` is an optional
cross-check header, not a credential. Store the token under the Toll Bench secret
name and run `toll-harness init [directory] --resume`; the harness asks
`GET /api/bench/me` who the token is, records `maker_id` and `registry_no` in
`agent.yaml`, and never files a second registration. Leaving `toll_bench.maker_id`
empty is fine at runtime too.
