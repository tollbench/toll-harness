# Privacy and Data Ownership

Toll Harness has no telemetry and no phone-home behavior. It runs without Toll Bench.

The operator retains model conversations, events, checkpoints, browser state/history, files, email
content, credentials, cookies, and raw tool results. Any future Toll Bench integration must be an
explicit adapter that selects only the marketplace and measurement records the operator intends to
share.

THE THINKING STREAM (0.75.0) IS THAT KIND OF ADAPTER, AND IT IS ON BY DEFAULT for a connected
agent. What it shares: the model turns the agent spends on a want (its words, the tools it calls
and what they hand back, the model, tokens, cost and timing), sent to that want's own log on the
bench and nowhere else. Nothing goes for an agent that is not connected or for work that names no
want. Before an entry leaves the machine, bearer and basic credentials, `sk-`/`rk-`/`pk-` keys
(and AWS, GitHub, Slack and JWT tokens), the agent's own bench token and anything under a key named
like key / secret / token / password / credential / cookie are masked, `http.request` arguments go
as the same audit view the event log keeps (method, domain, header names, body size), and every
text, result and argument list is cut at 20,000 characters. `TOLL_HARNESS_THINKING=0` or
`toll_bench.thinking: false` in `agent.yaml` turns it off; off wins.

Secrets are resolved within provider implementations. They are not capability results, checkpoint
fields, onboarding state, or ordinary logs. Local file secrets use owner-only directories and files;
the secret-store interface is non-enumerable. Checkpoint keys that look like secrets or credentials
are rejected.

Agent-owned browser sessions persist only in that agent's owner-only local profile. Book of Houses
never receives the profile, cookie jar, or credential. Person-owned passwords, OTPs, sessions, and
cookies are outside the harness authority boundary; person-owned access arrives only as a signed
target-bound `GRANT`.
