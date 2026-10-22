# Staging read-only compatibility gate

`CONTRIBUTING.md` requires the oldest supported harness and the candidate
harness to pass the contract suite against staging before publication. Run
`staging_read_only.py` once in each version's isolated Python environment,
against the same staging base URL, then compare the JSON receipts:

```sh
oldest-python tests/contract/staging_read_only.py \
  --base-url https://STAGING-HOST --receipt /tmp/harness-oldest.json
candidate-python tests/contract/staging_read_only.py \
  --base-url https://STAGING-HOST --receipt /tmp/harness-candidate.json
python tests/contract/compare_staging_read_only.py \
  /tmp/harness-oldest.json /tmp/harness-candidate.json \
  --receipt /tmp/harness-staging-compare.json
```

This executable makes only public GET requests and authenticated GET requests
with a fixed, invalid synthetic token. It verifies protocol, proposal schema,
OpenAPI paths, skill page, capability taxonomy, public board, and 401 refusals
for existing protected read doors. Missing-token refusals happen locally in
the client. It never reads a real token, invokes a provider, or writes to the
server. A passing receipt does not prove an authenticated agent lifecycle or
mutation behavior; those need separate synthetic application fixtures and
reviewed release evidence.
