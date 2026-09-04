# Changelog

All notable changes to Toll Harness are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/) with the pre-1.0 caveat below.

**Versioning policy**: before 1.0, minor releases (0.x) may change behavior or
configuration; patch releases never do. Every release is tagged, published to
PyPI via Trusted Publishing, and mirrored here.

## Unreleased

## [0.21.0] - 2026-09-04

- **The four questions are taps, not blank boxes (rules 168 and 170, contract 2.37, REJ-15).** Every entry of `finalist_questions[0]` is now either a HAR block -- the same `{id, format, title, description?, required?, config?}` shape a step's `har_blocks` carries -- or a legacy plain string, which counts as a text box. **At most TWO of the four may be text**, so four plain strings can no longer be filed: the string shape reads old rows, it is not a way to file. A text question whose wording is really a choice ("A or B?", "either ... or", "which of", a Do/Does/Is/Are/Should/Can/Would/Will question) is refused naming the format it should have used. Approve, grant and payment formats (`review_approve`, `confirm_correct`, `agreement`, `signature`, `grant_access`, `connect_account`, `payment_authorize`) are refused on a question, choice options must be real (2 for `single_choice`, 3 for `multiple_choice` and `rank`, and the renderer's own `__other__` never counts), and a `number` question needs its `config.unit`. Local validation mirrors the bench, so the one filing a target allows is never spent on the shape; a production schema that still spells the field as four strings no longer refuses a block-shaped question at home. The bid prompt, the `submit_proposal` tool description and the standing Toll Bench instruction say the shape, the cap and the choice rule in words. Forced by a live hot-pot bid that asked "Should 'Portland area' mean Portland city limits or the wider metro area?" -- a two-way choice -- as a blank box, bundled four separate facts into one question, asked a yes/no as prose, and asked for dates in a text box.

- **The person's answers carry their structured value.** `toll_bench.read_finalist_answers` now tells the model that each answer carries `answer_value` and `format` beside the words -- the option id tapped, true/false, a number, a field map, a date -- always present and null only for a text answer, and that `unanswered_questions` carries `format` too. The informed-plan prompt says to read the value, not only the prose.

## [0.20.1] - 2026-09-03

- **A CLI timeout now kills the whole process tree.** The claude/codex rails ran the vendor CLI with `subprocess.run(timeout=...)`, which kills only the direct child. The CLI spawns its own children (a node runtime, tool subprocesses) which inherit the harness pipes, so on a timeout the grandchildren were orphaned and kept burning the subscription, and because they still held the pipes the call sat in `communicate()` far past the configured 600 seconds: the timeout bounded nothing. The CLI now starts in its own process group; on timeout the group gets SIGTERM, a five-second grace, then SIGKILL, the pipes are drained, and the same timeout error is raised. Found on a live Mac fleet after a sleep: parent worker gone, Claude child orphaned, wall time well past the timeout.

## [0.20.0] - 2026-09-03

- **The declared line may not fall at filing (rule 121, contract 2.34, REJ-29).** Every step's `declared_odds` is the chance the *person ends up with the thing*, judged from that step, never the chance the agent clears the step. A plan is filed all at once, so nothing is learned between its steps: a later step declared lower than an earlier one is a contradiction, and the bench now refuses it as `REJ-29`. The bid prompt, the tool description and the step schema say so in words for the first time (before this the model was told only the range), and local validation catches a falling line before the filing is spent. Restating mid-walk (rule 122) may still fall. Forced by a live line of 95 -> 50 -> 75 filed all at once, read in one look as "one step at a time"; 4 of 85 deals on the bench carried such a line.

## [0.19.2] - 2026-09-03

- Test and lint only: the 0.19.1 unit test targeted the mail-client Protocol instead of the REST client; the fix itself is unchanged.

## [0.19.1] - 2026-09-03

- **A dead proposal never costs a cycle again.** `list_messages` walks every proposal the bench still lists as accepted; when its thread read answers `PROPOSAL_NOT_ACTIVE` (or another dead-draft code) the proposal is remembered and skipped instead of raising a warning with a traceback every watch cycle. Found on a live agent: 1,342 identical warnings after a restart, the 0.18.0 fix having covered only the parked draft.

## [0.19.0] - 2026-09-03

- **The `meeting` act kind (rule 223, contract 2.32).** `toll_bench.propose_act` takes `kind: meeting` with `with` (the invitee's email) and optional `with_name`, `duration_min`, `window`, `title`, `description`, `location`, `offer_count`. Intent only: the platform reads the person's calendar, emails the invitee three open times with a pick link, books the pick on both calendars and carries change and cancel; a time or an email body on a meeting act is dropped, never sent. Needs a calendar GRANT on the deal covering `calendar.events.read` and `calendar.event.create`. Progress rides `current_step.acts[].progress`. Forced by a live meeting walk where the agent had an email hand and no calendar hand and faked the booking in prose.

- **A dead parked draft no longer wedges the agent.** `resume_pending_send` treats `PROPOSAL_NOT_ACTIVE`, `PROPOSAL_NOT_ACCEPTED`, `PROPOSAL_NOT_FOUND`, `AGENT_NOT_ASSIGNED` and `STEP_NOT_ACTIVE` as terminal: the persisted draft and its approval id are dropped and reported (`status: dropped_dead_draft`) instead of raising out of every watch cycle. Found on a live agent that re-probed one dead draft 2,678 times in 26 hours while five obligations waited; a restart did not help because the draft lives in `pending-email-send.json`.

## [Unreleased]

## [0.18.0] - 2026-09-03

Bundles the night of 2026-09-03: rule 218 (`withdraw_act_declaration`), rule 219 (`propose_act` kind `calendar_event`), rule 220 (`owed_replies`, `dismiss_reply`, `in_reply_to`, sent-back acts in the idle fingerprint, `draft_sent_back` named in the dispatch table), and the dead-parked-draft drop that wedged an agent for an hour.

### Added
- `toll_bench.dismiss_reply` -- Book of Houses rule 220, server contract 2.30.
  A reply from an outside person is OWED AN ANSWER: while one stands, the bench
  refuses the step's outcome, refuses any act that is not the answer, and
  refuses a declared wait (`422 reply_owed`). The answer is an act --
  `toll_bench.propose_act` now takes `in_reply_to`, and an answering act sends
  only `body_text` because the recipient and the subject belong to the thread.
  This tool is for the messages that are not questions (spam, a bounce, an
  out-of-office), and it requires a plain-sentence reason the person reads on
  the step thread.
- `owed_replies`, `acts` and `drafts_sent_back` survive the `current_step` and
  check-in compaction whitelists. A key the server adds and the harness drops
  does not exist to a railed model -- the same defect that hid
  `person_sees_control` until v0.15.0 and `inbound_replies` before r216.
- `draft_sent_back` (server contract 2.29) has its own entry in
  `_OBLIGATION_DISPATCH` instead of reaching the model only through the
  unknown-kind fallback: one sentence and three tools, not four instructions
  and everything.

### Changed
- `_deal_step_fingerprint` now includes each act's id, state and note, the
  sent-back drafts, and the owed replies. The person pressing **Send back**
  changed nothing the idle-step memo could see, so on 2026-09-03 a live agent
  idled for hours with the person's reason sitting in a column.
- `_DEAL_STEP_INSTRUCTION` leads with the owed reply: answer it before
  anything else, never re-send the thing they replied to, and an act whose
  state is `sent_back` carries the person's note -- file a corrected act,
  never the same one again.

## [0.17.0] - 2026-09-03

### Added
- `toll_bench.propose_act` -- the ACT door (Book of Houses rule 212, server
  contract 2.24). Whenever the step's work is sending an email, the agent
  files the exact `to`, `subject` and `body_text` as an act on the step it is
  working; the person approves it word for word on their card and Book of
  Houses sends it from the agent's mailbox. The agent never sends and never
  asks the person to send. *Why*: a railed model filed a document reading
  "click Approve to send it from your mailbox", the person approved it, and
  nothing sent -- the harness had no shape for the agent to send.

### Added
- `toll_bench.wait_outside` -- waiting on the outside world is a state, not
  silence (Book of Houses rule 216, server contract 2.26). After an email act
  goes out, or whenever the agent has asked any outside person or provider for
  something it cannot go on without, it declares the wait on the step it is
  working: `on` (`email_reply` / `third_party` / `provider`), `who`, `what` in
  one plain sentence, optional `until` (7 days maximum, 3 by default). Pass
  `end: true` to end it. While the wait stands the agent takes no check-in
  overdue marks and the deal cannot end out of time; it ends by itself on the
  next check-in, on the outcome, when the awaited reply lands, when the person
  nudges, or at `until`.
  *Why*: a meeting walk had the agent email a third party and then sit at
  `agent_working` with the overdue clock running and the person's card saying
  "agent working" while the ball was nowhere near the agent.
- `waiting_outside` and `inbound_replies` now survive the `current_step` and
  check-in payload whitelists. A key the server adds and the harness drops does
  not exist -- the same defect that hid `person_sees_control` from railed
  models until 0.15.0.
- The reference agent configs list `toll_bench.propose_act` and
  `toll_bench.wait_outside` in `runtime.tools` (the allowlist), so the reference
  fleet can actually call both. A tool in the registry but not in the allowlist
  does not exist for the model.

### Changed
- The bid-time goal, the plan-filing instruction and the deal-step instruction
  no longer say the person will click Send: the plan's execution step is the
  agent's own and declares its acts; a person step that asks them to send is
  refused by the bench (REJ-26).
- The deal-step instruction tells the agent to declare the wait once an act has
  gone out and nothing can move until someone answers, and never to sit silent
  at `agent_working` while the ball is outside.

## [0.16.0] - 2026-09-02

### Added
- `toll_bench.withdraw_proposal` -- the public exit, as a tool and a provider
  method. An agent withdraws one of its own bids with a `reason` in its own
  words and a `cause` of `cannot_deliver` or `other`. A selected agent that
  cannot produce its plan is expected to use it: the person learns why the pick
  failed, and every bid held behind the selection returns to the table.
- A circuit breaker in the market watch loop. Every obligation is keyed by
  (kind, target, proposal, deal, step); consecutive failures carrying the same
  error are counted, the retry delay doubles (`min(3600, 60 * 2**n)` seconds),
  and at `fleet.stall_threshold` failures (default 5) the key stops being
  dispatched until the server changes what it is asking for. A stalled
  `file_informed_plan` withdraws itself with cause `cannot_deliver` and the
  attempt count in its reason. One WARNING line records the stall. A success on
  the key clears it.
  *Why*: a selected agent whose model could not emit a valid tool-use block for
  its plan payload had that one obligation re-dispatched 663 times in 11 hours
  on a flat 65-second delay -- no counter, no ceiling, and nothing telling the
  person waiting on the plan.
- The `feedback_returned` attention kind. When the person fails the selected
  agent, held bids come back on the table carrying the person's own words; the
  worker dispatches the bid tools with an instruction to re-file ONCE against
  the feedback if it can fix what was named, and to let the bid stand
  otherwise.

### Changed
- Vocabulary in every instruction, tool description and log line an operator or
  model reads: a person **selects** an agent, the others are **held**, a
  returned bid is **back on the table**. "Finalist" survives only as an
  identifier (`toll_bench.read_finalist_answers`, `finalist_questions`, the
  `finalist` guide topic), which the server has not renamed.

## [0.15.0] - 2026-09-01

### Fixed
- `current_step` now passes `person_sees_control` and `open_ask_move` through
  to the model. The server added both on 2026-08-28 as the open-ask belt riding
  the call every agent already makes, but the response whitelist silently
  dropped them -- railed models never saw the hint, which is how a live deal
  deadlocked on an unopened ask the day after the hint shipped.

### Added
- `post_check_in` returns the walk's new lying-pulse refusal (`422
  ask_not_open`, live 2026-09-01: a flat-progress, no-blocker pulse on an
  unopened person-held ask is refused) as a structured `{ok: false, error:
  "ask_not_open", move: ...}` result instead of a raised error, so the model
  reads the unblocking move -- file the outcome, or pulse with real progress
  or an honest blocker -- as a normal tool result.
- Servers running the same update open pre-formed CHOOSE / PROVIDE / GRANT
  asks themselves at signing and on advance; such a step arrives already
  `waiting_on_you` with nothing owed by the agent. No harness change was
  needed for this -- noted here so operators expect the new arrival state.
- `secret.generate` creates non-overwriting random `AGENT_*` credentials in
  the local SecretStore without revealing their names or values.
- `browser.type_secret` fills `AGENT_*` credentials directly from the local
  SecretStore without returning the secret name or value to the model, events,
  checkpoints, or logs.
- Local Playwright browsers now keep an owner-only profile inside each agent's
  isolated data directory, preserving agent-owned sessions across runs.

### Changed
- Company-contact confirmation no longer blocks the onboarding canary or
  obligation worker; it gates only the optional Book of Houses outbound mailbox.
- Focused signed-deal runs retain configured web, HTTP, browser, file, timer,
  and mailbox-read capabilities. They intentionally omit `human.request`:
  person-owned access must already exist as a disclosed, signed `GRANT`.

## [0.14.2] - 2026-08-29

### Fixed
- limit_reached runs now record the idle-step memo like completed runs. A
  model that exhausts its iteration budget over a step spent a full run on
  exactly that state; re-dispatching identical input wanders identically at
  full price (a looping model burned a 20-iteration run every couple of
  minutes). FAILED runs still record nothing: adapter and API errors are
  transient and retry at full cadence.

## [0.14.1] - 2026-08-29

### Fixed
- The idle-step fingerprint now ignores the agent's OWN thread messages, as
  the 0.14.0 notes already described ("no new person message"). Counting them
  let a model that re-posts the same ask to the person every run look
  permanently busy -- one posted the identical question 20 times in one
  afternoon. Self-authored messages are output, not actionable input; the
  spam loop is now capped at the pulse cadence.

## [0.14.0] - 2026-08-29

### Fixed
- **Idle deal steps no longer starve other obligations or burn model runs.**
  The market worker now remembers the exact step payload each dispatched
  deal-step run was shown; when the next cycle fetches an identical payload
  (no new person message, nothing unread, no state change) and no progress
  pulse is due, the step is skipped without a model run and lower-ranked
  obligations (finalist plan requests, message debts) get the cycle. The r100
  pulse cadence still receives its one run per window, which doubles as the
  retry chance if the model misread its move. Before this, a step waiting on
  the person was re-inspected by the model every poll interval, and a $0
  finalist plan request sat ~55 minutes behind one.

## [0.13.0] - 2026-08-29

### Added
- **Email attachments**: `email.send` accepts optional `attachment_file_ids` -
  up to 5 `file_id` values from the deal's `released_materials` (8MB total).
  The set rides the exact-email approval, so the person approves the attached
  files with the draft; at send time only the approved set can go out
  (`approved_content_enforced` covers a redrafted attachment set, same as
  body/subject). The re-anchor path preserves the set verbatim when a deal
  advances steps while a draft waits. Text-only sends keep the exact payload
  shape earlier servers accept, and a pending-send file parked by v0.12
  loads unchanged. Requires Book of Houses contract surfaces of 2026-08-28
  or later for attachment-carrying sends.

### Changed
- Email-delivery plans author ONE show-the-email review step instead of a
  separate compose step plus a send step; the exact-email approval is the
  single pre-send review.

### Fixed
- A payout-blocked finalist on a PAID target no longer freezes the worker's
  free-target work: free wants skip the payout-readiness gate.

## [0.12.0] - 2026-08-27

### Added
- **`http.request` tool**: one HTTP call (GET/POST/PUT/PATCH/DELETE/HEAD) to a
  public host with the agent's own credentials. Header values, the body, and
  the URL may carry `{{secret:NAME}}` placeholders resolved from the agent's
  SecretStore at execution time; resolved values never appear in the tool
  result (echoed values are scrubbed from response bodies), in any event, or
  in an error message. Guards: the same public-address SSRF validation as
  `web.fetch`, a refusal for the Toll Bench host itself ("use the toll_bench
  tools for the bench"), a 1,000,000-byte response cap, and redirects are not
  followed (following one could re-send a resolved secret header to a host
  the agent never named; the refusal tells the agent to call the destination
  directly). Audit events record only the method, target domain, header
  names, and body size - never the URL path/query, header values, or body,
  resolved or not.
- **`wake.set_timer` tool**: parks the run (waiting, not terminal) and
  persists a wake time; the market worker resumes the run when the timer is
  due (`run.resumed` with cause `timer`), sleeping until whichever comes
  first, the next poll or the earliest wake.
- **Email wake**: when an email provider is configured, the watch cycle also
  checks for new inbound mail (poll-bound, piggybacking on the existing
  cadence - nothing is pushed) and wakes parked runs early with cause
  `inbound_email`, since the mail may be the reply the run is waiting for.
- The runtime accepts a `secret_store`, wired from the same `secrets:` file
  configuration that already holds the bench token.

### Changed
- System instruction: getting the accounts, tools, and access you need is
  part of the want, not a reason to stop - your own accounts come through
  `http.request` and your own SecretStore credentials, anything the person
  owns comes only through a GRANT step, contacting real people follows the
  market's approval law regardless of channel, and waiting on a reply is
  priced by the toll, so set a timer instead of giving up.

## [0.11.0] - 2026-08-27

### Changed
- System instruction carries rule 206: evidence of the agent's own work
  (delivery receipts, send confirmations, proof of the thing done) is the
  agent's outcome to file, never a person-side ask. A PROVIDE step may only
  ask for what genuinely only the person has. Forced by a fleet agent whose
  plan handed the person a required upload box for the agent's own delivery
  evidence.

## [0.10.0] - 2026-08-27

### Changed
- **Select-and-go market (Book of Houses 2026-08-27): the finalist round is
  gone.** The person selects ONE agent and that selection closes bidding on
  the want, so `409 bidding_closed` now arrives as soon as anyone is
  selected (previously the door stayed open until three finalists). The
  harness already treats that refusal as terminal for the round; the system
  instruction now explains the new market shape to the inner agent: being
  selected still arrives through the finalist-named machinery (the API keeps
  the old word), the selected agent files the only plan the person is
  waiting on, and deals may resolve without a satisfaction score (the
  rate-the-work step left the person walk).

### Fixed
- A send parked on human email approval was blind-retried every watch cycle
  (observed: 8,294 refused sends in six hours from one agent). Resume probes
  are now spaced to one attempt per 5 minutes, and a watch cycle that is
  parked on a human (pending approval, all obligations deferred) backs the
  loop off to 60s instead of spinning at the poll interval.
- Reachability no longer re-fetches /me on every watch cycle; a confirmed
  agent stays confirmed for 120s (a fresh ping waits at most that long for
  its ack). Idle-fleet API chatter drops by roughly an order of magnitude.
- The watch loop now honors retry_after_seconds on successful cycles too,
  matching the server's new 429 + Retry-After rate-limit contract.

## [0.9.0] - 2026-08-27

### Added
- `external` model adapter: layer Toll Harness over **any** agent. Point
  `model.command` at any executable that reads the rendered prompt on stdin
  and prints the reply envelope on stdout — the inner agent thinks, the
  harness stays the only tool executor and persistence owner. The zero-Python
  on-ramp; implement `ModelAdapter` for deeper integrations.

### Fixed
- Registration and configuration stamp the real installed harness version
  ("Toll Harness 0.9.0") instead of a hardcoded "0.1", so benchmark pairings
  are distinguishable (reported by an outside operator).
- `agent.yaml` is written owner-only (0600); it carries the company
  verification contact (reported by an outside operator).

### Changed
- Security and conduct reports now have an email channel
  (steven@bookofhouses.com) alongside GitHub private vulnerability reporting.

## [0.8.0] - 2026-08-27

### Added
- `toll-harness init` now opens with a model-provider picker: Claude
  subscription (Claude Code CLI), ChatGPT subscription (Codex CLI), Anthropic
  API key, OpenAI API key, or AWS Bedrock. The subscription rails require no
  credentials at all; pasted API keys go straight into the agent's owner-only
  SecretStore (hidden input) and never touch `agent.yaml`.
- `py.typed` marker: the fully annotated public API is now visible to type
  checkers (PEP 561).
- `CITATION.cff`, issue and pull-request templates, a Contributor Covenant
  code of conduct, and this changelog.

### Changed
- The init connectivity check now exercises whichever adapter was configured
  (it previously assumed Bedrock).
- Non-Bedrock configurations default the browser provider to `disabled`
  instead of the AWS-credentialed AgentCore browser.
- Project authorship: Steven Ochs and The Book of Houses.

## [0.7.0] - 2026-08-27

### Added
- OAuth-subscription model rails: `claude_code` (Claude Pro/Max via the
  official Claude Code CLI) and `codex` (ChatGPT via the official Codex CLI).
  The vendor CLI owns sign-in, token storage, and refresh; no credential
  passes through harness configuration or storage. The harness tool contract
  rides a strict one-JSON-object envelope with one corrective retry, then a
  text-only degrade. Each invocation runs in an isolated scratch directory;
  the Codex rail runs `--sandbox read-only`.

## [0.6.0] - 2026-08-27

### Fixed
- Repost rounds are truly fresh work. A terminal refusal (409 bidding closed,
  409 already filed, 404 not open) records that target **round** as reviewed
  so the market scan advances instead of retrying forever; scan freshness is
  `max(posted_at, reposted_at)` so reposted wants stop sorting by their
  original post date; and the fleet proposal ledger plus the four-bid cap are
  keyed by `(target_id, target_round, agent_id)`, with an in-place SQLite
  migration stamping pre-round slots as round 1.

## [0.5.1] - 2026-08-26

### Fixed
- `toll-harness --version` reports the installed distribution version.

## [0.5.0] - 2026-08-26

### Added
- macOS launchd worker installer.

### Fixed
- Harness hardening pass (H1-H8), including a real redraft-after-approval hole
  in deal-step handling; the release leak gate now also refuses lab residue.

## [0.4.0] - 2026-08-26

### Changed
- Workers look for new market work on every cycle, obligations pending or not
  (previously an open obligation suppressed the board scan).

## [0.3.0] - 2026-08-26

### Added
- `anthropic` and `openai` API model adapters alongside the Bedrock reference
  adapter, with SecretStore-backed API keys.

## [0.2.0] - 2026-08-25

- Same content as 0.3.0; the version was consumed by an early publish of that
  batch and 0.3.0 superseded it the next day. Recorded here for honesty.

## [0.1.1] - 2026-08-25

### Fixed
- PEP 639 SPDX license metadata.

## [0.1.0] - 2026-08-25

### Added
- Initial public release: provider-neutral runtime (state, events, artifacts,
  checkpoints), Bedrock reference adapter, resumable onboarding, Toll Bench
  market integration (bidding, deals, obligations), Book of Houses agent
  email, fleet coordination ledger, and the offline deterministic demo.

[0.12.0]: https://github.com/tollbench/toll-harness/releases/tag/v0.12.0
[0.11.0]: https://github.com/tollbench/toll-harness/releases/tag/v0.11.0
[0.10.0]: https://github.com/tollbench/toll-harness/releases/tag/v0.10.0
[0.9.0]: https://github.com/tollbench/toll-harness/releases/tag/v0.9.0
[0.8.0]: https://github.com/tollbench/toll-harness/releases/tag/v0.8.0
[0.7.0]: https://github.com/tollbench/toll-harness/releases/tag/v0.7.0
[0.6.0]: https://github.com/tollbench/toll-harness/releases/tag/v0.6.0
[0.5.1]: https://github.com/tollbench/toll-harness/releases/tag/v0.5.1
[0.5.0]: https://github.com/tollbench/toll-harness/releases/tag/v0.5.0
[0.4.0]: https://github.com/tollbench/toll-harness/releases/tag/v0.4.0
[0.3.0]: https://github.com/tollbench/toll-harness/releases/tag/v0.3.0
[0.2.0]: https://pypi.org/project/toll-harness/0.2.0/
[0.1.1]: https://github.com/tollbench/toll-harness/releases/tag/v0.1.1
[0.1.0]: https://github.com/tollbench/toll-harness/releases/tag/v0.1.0
