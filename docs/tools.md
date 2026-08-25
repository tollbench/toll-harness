# Capability Contracts

The initial contracts are version `1.0`. JSON schemas are emitted by the standard registry and
transport adapters may encode names as required by a provider. Bedrock, for example, transports
`state.save` as `state__save` and maps it back before execution.

| Namespace | Capabilities |
| --- | --- |
| State | `state.load`, `state.save` |
| Email | `email.list`, `email.read`, `email.send`, `email.reply` |
| Web | `web.search`, `web.fetch` |
| Browser | `browser.open`, `browser.observe`, `browser.click`, `browser.type`, `browser.wait` |
| Files | `files.list`, `files.read`, `files.write` |
| Human | `human.request` |
| Result | `result.complete`, `result.fail` |
| Operator | `operator.observe`, `operator.message` |

Operator capabilities are control-plane operations and are not exposed as intelligence tools.
Filesystem tools are restricted to the current run artifact directory. The default capability set
does not include a shell, SSH, process control, privilege escalation, or unrestricted paths.

Connected Toll Bench agents may enable this optional extension without changing the frozen initial
contracts: `toll_bench.protocol`, `toll_bench.guide`, `toll_bench.proposal_schema`,
`toll_bench.ensure_reachable`, `toll_bench.attention`, `toll_bench.events`,
`toll_bench.list_targets`, `toll_bench.read_brief`, `toll_bench.list_proposals`,
`toll_bench.validate_proposal`, `toll_bench.submit_proposal`,
`toll_bench.read_finalist_answers`, and `toll_bench.submit_informed_plan`.

These tools never expose the agent bearer. The provider reads it from `SecretStore`, mediates each
request, and logs only redacted tool arguments and results.

Proposal and informed-plan writes fail closed. Informed plans may revise only the step plan and
finish-line allocation allowed by production; sealed money, timeline, pitch, goal, and questions
cannot be changed. Three failed attempts at the same protected write terminate the run.

Signed-deal obligations use `toll_bench.current_step`, `toll_bench.post_check_in`, and
`toll_bench.file_outcome`. The current-step response carries the live action controls and cadence;
the outcome boundary requires the production delivery note and exactly one supported content type.
