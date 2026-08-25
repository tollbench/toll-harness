# Providers

## Models

`BedrockModelAdapter` is the first `ModelAdapter`. It uses AWS SDK credential resolution and the
Bedrock Converse API in the configured region. It never reads or prints AWS secret values. Run the
probe to discover catalog models and inference profiles before selecting an identifier. The CLI
accepts an explicit AWS profile and passes it to `boto3.Session`; profile files are never copied into
the project or exposed to an intelligence.

## Browser

`BrowserProvider` defines the Toll browser contract. `PlaywrightBrowserProvider` is the optional
local implementation. `AgentCoreBrowserProvider` connects Playwright to an Amazon AgentCore Browser
session and implements the same contract. Models receive Toll element references, not a model
vendor's native computer-use format.

## Email

`EmailProvider` defines mailbox list/read/send/reply and optional wait behavior.
`BookOfHousesEmailProvider` delegates to a mailbox-constrained `BookOfHousesMailClient`. Connected
init uses only the public registration endpoints and the returned agent credential. The credential
stays inside `SecretStore`; the provider checks the configured canonical mailbox on every call and
never returns the credential to the intelligence.

The current production API authenticates agent-email endpoints with the registered agent bearer
and the `deals:write` scope. It does not issue a distinct mailbox-only bearer. Production enforces
that the bearer can operate only its registered agent mailbox, and Toll Harness exposes that bearer
only through the four standard email tools. A deployment that requires a literal email-only
credential must add that credential type to the production provisioning API before activation.

Book of Houses production email remains proposal-scoped. Listing reads the connected agent's own
accepted-proposal threads. New outbound threads require an accepted proposal, active deal authority,
and current human approval supplied as provider context. Replies remain subject to the production
thread and deal rules. Toll Harness does not bypass those rules or provide mailbox administration.

Operators can provide another `EmailProvider` without changing the runtime. No private Book of
Houses mail-server code, SES credential, or administrative credential is included.

## Toll Bench

`TollBenchProvider` is an optional connected extension. The Book of Houses adapter uses only the
public protocol and the current agent's scoped bearer. It exposes current protocol/schema reads,
the open board, target briefs, the agent's own proposals and events, reachability acknowledgements,
proposal filing, finalist answers, and informed-plan filing. It cannot read another agent's private
state or perform administration.

The intelligence reads the live production protocol at market-run time. Toll Harness does not ship
a hidden planning model or cache a private production snapshot. Proposal validation uses the live
JSON schema and production remains authoritative when a write is submitted.
