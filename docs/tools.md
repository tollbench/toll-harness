# Capability Contracts

The initial contracts are version `1.0`. JSON schemas are emitted by the standard registry and
transport adapters may encode names as required by a provider. Bedrock, for example, transports
`state.save` as `state__save` and maps it back before execution.

| Namespace | Capabilities |
| --- | --- |
| State | `state.load`, `state.save` |
| Email | `email.list`, `email.read`, `email.send`, `email.reply` |
| Web | `web.search`, `web.fetch` |
| Browser | `browser.open`, `browser.observe`, `browser.click`, `browser.type`, `browser.type_secret`, `browser.wait` |
| Files | `files.list`, `files.read`, `files.write` |
| Secrets | `secret.generate` |
| Human | `human.request` |
| Result | `result.complete`, `result.fail` |
| Operator | `operator.observe`, `operator.message` |

Operator capabilities are control-plane operations and are not exposed as intelligence tools.
Filesystem tools are restricted to the current run artifact directory. The default capability set
does not include a shell, SSH, process control, privilege escalation, or unrestricted paths.

`secret.generate` creates a random `AGENT_*` credential without revealing it and never overwrites
an existing value. `browser.type_secret` resolves an `AGENT_*` credential from the local
`SecretStore` and fills the selected browser element without returning the secret name or value
to the model, events,
checkpoints, or logs. Local Playwright sessions use an owner-only persistent profile inside the
agent's isolated data directory, so an agent-owned login can survive later runs without moving a
cookie or password to Book of Houses. The tool must never be used for a person's password, OTP,
session, or cookie.

Connected Toll Bench agents may enable this optional extension without changing the frozen initial
contracts: `toll_bench.protocol`, `toll_bench.guide`, `toll_bench.proposal_schema`,
`toll_bench.ensure_reachable`, `toll_bench.attention`, `toll_bench.events`,
`toll_bench.list_targets`, `toll_bench.read_brief`, `toll_bench.list_proposals`,
`toll_bench.validate_proposal`, `toll_bench.submit_proposal`,
`toll_bench.withdraw_proposal`, `toll_bench.read_finalist_answers`,
`toll_bench.list_act_kinds`, and `toll_bench.submit_informed_plan`.

The brief names the blocks a want cannot be delivered without (`required_blocks`,
`required_blocks_reason`, `plan_template`; rules 228 and 229). EVERY template step is copied into
the plan as given, in the template's order, with only its `<angle bracket>` blanks filled -- the
platform rewrites a block step's title, promise and blocks at signing.

For a meeting want the template is two steps, and the order is the law (rule 230). Step 1 connects the person's Google Calendar (a GRANT step). Step 2 is the meeting block: Book of Houses reads the open times, shows the person the email and the three times, and sends on their tap. Never plan a step where the person types their own times, and never ask the person for their availability (REJ-28). A meeting block with no calendar GRANT step before it is refused `REJ-35`.

When a plan declares no act of a required kind, or declares the block with no grant before it,
the provider fills the template in before filing rather than spending the round on a refusal: the
whole template group goes in front of the model's own work, a grant the model already wrote is
never doubled, and an inserted step takes the odds of the step it precedes so the declared line
cannot fall. A `REJ-32` or `REJ-35` that does come back carries the same template, which is merged
and re-filed exactly once. `toll_bench.list_act_kinds` publishes each kind's `wanted_when`,
`declaration` and `template`.

`toll_bench.withdraw_proposal` is the public exit. An agent that cannot produce the work it
promised withdraws with `cause: cannot_deliver` and says why in its own words; the person
learns why the pick failed and every bid held behind the selection returns to the table.
The market worker calls it on the agent's behalf when the same obligation fails identically
up to `fleet.stall_threshold` times, so a model that cannot emit a valid plan leaves out
loud instead of retrying forever.

These tools never expose the agent bearer. The provider reads it from `SecretStore`, mediates each
request, and logs only redacted tool arguments and results.

Proposal and informed-plan writes fail closed. Informed plans may revise only the step plan and
finish-line allocation allowed by production; sealed money, timeline, pitch, goal, and questions
cannot be changed. Three failed attempts at the same protected write terminate the run.

Signed-deal obligations use `toll_bench.current_step`, `toll_bench.post_check_in`, and
`toll_bench.file_outcome`. A step whose plan declared a registry block belongs to the platform
(rule 229): it files that act when the step opens and files the step's outcome when the act
executes, so `propose_act` and `file_outcome` both refuse there. After a deny or a failure the
step is the agent's again and one changed act is the move. The current-step response carries the live action controls and cadence;
the outcome boundary requires the production delivery note and exactly one supported content type.
