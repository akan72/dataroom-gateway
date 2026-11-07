# Local end-to-end verification — November 7, 2026

Scope: the local example before Tinfoil credentials. No hardware attestation or
cloud deployment has been verified. No GPU resources were needed for this stage.

## Observed run

- Started collector, SaaS, and gateway through `python -m usage_demo.cli serve`.
- Generated 1,000 workload requests plus 18 setup calls against the gateway.
- HTTP status totals: 319 × 200, 626 × 201, 73 × 503.
- Captured the `usage_by_tier` signed SQL receipt and its snapshot checkpoint.
- Verified/imported 1,018 signed records from 5,461 OTel spans.
- OTel service totals: `usage-gateway` 1,018; `mock-saas` 4,443.
- Zero rejected signatures, zero missing records, zero duplicated signed records.
- Found nine distinct user scopes and 1,018 distinct gateway trace IDs.
- Matched all 1,018 gateway requests to SaaS spans by normalized trace ID.
- Rebuilt DuckDB from the signed export and reproduced the receipt result exactly.

| Tier | Requests | Distinct scopes | 2xx | 5xx |
|---|---:|---:|---:|---:|
| Enterprise | 623 | 3 | 556 | 67 |
| Free | 106 | 3 | 106 | 0 |
| Pro | 289 | 3 | 283 | 6 |

Job analysis found 429 enterprise attempts with 67 server errors, 65 free attempts
with zero server errors, and 196 pro attempts with six server errors. The most active
scope generated 375 of 1,018 requests (36.84%). These are synthetic workload findings.

## Reproduction

With the services running, run:

```sh
make check
.venv/bin/python -m usage_demo.cli capture
.venv/bin/python -m usage_demo.cli index
.venv/bin/python -m usage_demo.cli verify
.venv/bin/python -m usage_demo.cli query job_reliability
.venv/bin/python -m usage_demo.cli query customer_concentration
```

`verify` must report `signature_valid`, `complete_snapshot`, and
`sql_result_reproduced` as true. `hardware_attestation_verified` must remain false
in this local stage. Later traffic changes the captured numbers.

## Live audience interface

- Eight isolated browser contexts joined concurrently and received eight distinct UUID scopes.
- Including the original and follow-up visitors, the active session reached ten anonymous scopes.
- The projector's count increased from 51 to 54 while a separate visitor created a workspace
  and ran a job. It verified 19 recent request signatures in-browser.
- The browser verified a SQL receipt covering 54 requests: Enterprise 18 / three scopes,
  Free 18 / four scopes, Pro 18 / three scopes.
- Changing a copy of a signed record's status caused browser verification to return false.
- Both 390px mobile and 1440px desktop layouts had zero horizontal overflow.
- A subsequent owner-side OTel import reproduced all 56 current-session records and its
  SQL receipt (page restoration added two Free requests).
- The latest test suite passes 24 tests; lint and formatting checks pass.

## Linux container / embedded DuckDB

Built `private-usage-demo:local` for Linux/amd64, then ran the entire demo in a
read-only container with `/data` and `/tmp` tmpfs mounts and Docker `--network none`.
No host directories or ports were mounted. Collection, DuckDB SQL execution, OTel
receiving, local indexing, and receipt reproduction ran inside that container.

The container processed 1,018 requests (319 × 200, 633 × 201, 66 × 503).
The embedded gateway DuckDB result matched the separately indexed export:

| Tier | Requests | Scopes | 2xx | 5xx |
|---|---:|---:|---:|---:|
| Enterprise | 623 | 3 | 560 | 63 |
| Free | 106 | 3 | 105 | 1 |
| Pro | 289 | 3 | 287 | 2 |

This caught and fixed the CLI's initial failure to respect `DEMO_DATA_DIR=/data`
on a read-only root filesystem. Regression tests cover the environment default and
explicit command-line override. The container has no hardware attestation; this check
proves packaging and execution location, not enclave security.

## Remaining Tinfoil stage

Hosted model inference has now been independently exercised through the official
Python SDK; this is distinct from deploying the usage gateway:

- Command: `make inference-smoke` using Tinfoil Python SDK 0.14.2.
- Model: `gpt-oss-120b`.
- Reply: `Tinfoil inference connected.`
- Completion ID: `chatcmpl-af823401ea4d76fa`.
- SDK verification time: November 7, 2026, 1:06 p.m. America/New_York.
- Remote enclave: `router-0.tinfoil.sh`.
- Verified configuration repository: `tinfoilsh/confidential-model-router`.
- SDK `security_verified`: true.
- Fetch digest, verify code, verify enclave, and compare measurements: all success.

The key is held only in the ignored local `.env`; no credential is included in
this report or source code. The hosted inference check attests Tinfoil's model
endpoint. It does not attest our gateway, user traffic, or DuckDB SQL receipts.

The image has been built and exercised locally but has not been deployed/validated
on Tinfoil. The Tinfoil configuration remains a template. Deployment requires credentials,
a concrete public configuration repository and image digest, an authenticated HTTPS
route back to this machine, and independent SDK verification of the approved workload
and attested signing key. See the README's Tinfoil handoff for the exact boundaries.
