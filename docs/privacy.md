# Privacy and Data Ownership

Toll Harness has no telemetry and no phone-home behavior. It runs without Toll Bench.

The operator retains model conversations, events, checkpoints, browser state/history, files, email
content, credentials, cookies, and raw tool results. Any future Toll Bench integration must be an
explicit adapter that selects only the marketplace and measurement records the operator intends to
share.

Secrets are resolved within provider implementations. They are not capability results, checkpoint
fields, onboarding state, or ordinary logs. Local file secrets use owner-only directories and files;
the secret-store interface is non-enumerable. Checkpoint keys that look like secrets or credentials
are rejected.

Agent-owned browser sessions persist only in that agent's owner-only local profile. Book of Houses
never receives the profile, cookie jar, or credential. Person-owned passwords, OTPs, sessions, and
cookies are outside the harness authority boundary; person-owned access arrives only as a signed
target-bound `GRANT`.
