# Dataroom Gateway

A runnable SaaS → signed observation journal → embedded DuckDB → signed SQL receipt
pipeline. **The authoritative DuckDB queries execute inside the gateway container.**
When deployed to Tinfoil, the journal, DuckDB engine, raw query inputs, and receipt
signer all stay inside the enclave. OTel export to this machine and a second local
DuckDB index remain available for owner-side inspection and independent reproduction.

The GitHub deployment targets `akan72/dataroom-gateway`. Its measured
`tinfoil-config.yml` runs the gateway/DuckDB, SaaS, and OTel collector together inside
Tinfoil on a closed internal network. Telemetry stays inside the enclave in this
deployment; no Cloudflare tunnel or external trace export is enabled.

Public demo: https://dataroom-gateway.akan72.containers.tinfoil.dev/

Projector view: https://dataroom-gateway.akan72.containers.tinfoil.dev/live

The public deployment runs inside Tinfoil. Its workload and endorsed usage-signing
key have been independently checked with the Tinfoil CLI. The browser verifies
request signatures; it does not independently perform hardware attestation.
Running `make dev` locally still uses development keys. Do not infer hardware trust
from server-reported flags: verification happens in the client.

```sh
.venv/bin/python -m usage_demo.verify_remote https://dataroom-gateway.akan72.containers.tinfoil.dev
```

The default expected release/tag/digest is recorded in `deploy/release.json`.
The verifier checks the exact measured artifact, TLS binding, freshness, and the
enclave-endorsed Ed25519 key, then saves a public-key pin under ignored `data/tinfoil`.
This is separate from the signature-only browser check. Server payload fields named
`hardware_attestation_verified` remain false by design; a server cannot establish
its own trust. The CLI-derived verification result is the authoritative check.

```text
Synthetic clients (9 UUID user scopes; Free / Pro / Enterprise)
           │ demo bearer tokens
           ▼
Gateway :8000 ─────────────────────► FastAPI SaaS :8001
   │ authenticated identity + observed HTTP outcome    │ scoped projects/jobs
   │ SQLite FULL commit; signed hash-linked record     │ ordinary OTel spans
   ├── embedded DuckDB → signed result + snapshot       │
   │                                                   │
   └──────────── OTLP/HTTP protobuf ────────────────────┘
                           ▼
                 Local receiver :4318
                        otel.jsonl
                           │ pinned key + witnessed checkpoint
                           ▼
                 Local DuckDB inspection copy
                  usage / otel_spans
```

## DuckDB inside Tinfoil

The target deployment has this trust boundary:

```text
Tinfoil enclave
┌─────────────────────────────────────────────────────────────┐
│ Gateway → FastAPI SaaS                                      │
│    │                                                        │
│    └→ signed journal → DuckDB SQL → signed aggregate receipt │
└─────────────────────────────┬───────────────────────────────┘
                              │
               approved results to the buyer
               optional raw OTel copy to the seller's machine
```

`usage_demo/analysis.py::query_snapshot` opens an embedded in-memory DuckDB database
inside the gateway process, populates it from a verified journal snapshot, and runs
the allowlisted SQL there. The gateway then signs the result/checkpoint/SQL/nonce
receipt. Neither `/evidence/query` nor the live demo's `/demo/query` delegates SQL
execution to this machine. No DuckDB network port or separate database container is
needed. `Dockerfile` includes the locked DuckDB package in the gateway image, and
`deploy/tinfoil-config.yml.example` places that entire image inside Tinfoil.

The initial implementation rebuilds an in-memory analytical snapshot for each query.
That is appropriate for the demo workload; a continuously materialized DuckDB store
can replace it later. The durable signed journal remains SQLite with FULL commits.
Raw SQL is restricted to fixed templates and DuckDB external access is disabled.

`data/usage.duckdb` on this machine is a second, owner-side inspection copy built from
OTel export. It is not the execution engine behind the signed service response.
Real privacy from the buyer requires keeping that export on the seller's side.
Hardware-backed execution assurance still requires independent Tinfoil workload/key
verification; installing DuckDB inside a local container alone does not provide it.

## Private model inference

The Python Tinfoil SDK is installed and locked. Run one hosted model call with:

```sh
make inference-smoke
```

`usage_demo/inference.py` loads `TINFOIL_API_KEY` from the project's ignored `.env`
file, creates the official `TinfoilAI` client, and calls `gpt-oss-120b`. The SDK verifies
the remote enclave and code provenance before sending the prompt. The command requires
a successful SDK verification document before reporting success. Its output includes
the model response and public verification metadata, never credentials or HTTP headers.
The API credential is stored only in `.env`, which has mode 0600 and is excluded from
both Git and the Docker build context. No inference endpoint is exposed on the public UI.

The installed Python SDK 0.14.2 defaults to a literal placeholder API key, rather than
automatically reading `TINFOIL_API_KEY`. Our wrapper follows its documented API and
passes the loaded environment value in memory. Callers never supply a literal key.
The client uses a fresh in-memory prompt-cache secret per invocation.

This is a separate path from signed usage analytics:

- **Usage pipeline:** SaaS requests → gateway journal → embedded DuckDB → signed SQL receipt.
- **Model inference:** local CLI → Tinfoil SDK verification → confidential hosted model → response.

Successful hosted inference does not attest our local gateway, logs, or SQL execution.
Deploying those inside Tinfoil uses Tinfoil Containers and an admin credential, supplied
locally as `TINFOIL_ADMIN_KEY`. An inference key does not replace that admin credential.
The SDK integration does not deploy any cloud resources or change the UI's development label.

See [the Python SDK](https://docs.tinfoil.sh/sdk/python-sdk) and
[quickstart](https://docs.tinfoil.sh/quickstart).

## Start locally

Requires Python 3.12+ and `uv`. Docker and a Tinfoil key are not needed locally.

```sh
cd /Users/alexkan/Documents/ChatGPT/personal/private-usage-demo
make setup
make dev
```

`make dev` starts all three processes on **127.0.0.1 only**, waits for readiness,
and supervises them. Ctrl-C shuts down all three. Ports 8000, 8001, and 4318 must
be free; the supervisor never stops unrelated processes. Logs are in `data/*.log`.

## Live audience demo

Open **http://localhost:8000/** for the participant page and
**http://localhost:8000/live** for the projector view. Both are served by the gateway.

Participants choose a simulated tier and receive a separate anonymous UUID scope
with a four-hour signed session token. They can create a workspace, run jobs, and
watch their requests arrive. Each browser verifies Ed25519 signatures using Web Crypto.
The live page includes a locally generated QR code for its current origin, a request
stream, tier totals, a signed SQL receipt button, and a tamper experiment that changes
a copy of a record and demonstrates signature rejection.

Suggested live sequence:

1. Put `/live` on the projector. At the public HTTPS URL, the QR code lets people join
   on their phones. A localhost QR is explicitly labeled as a local preview.
2. Have visitors choose different tiers and run jobs. Watch scopes, requests, and the
   request mix update. These are anonymous simulated tiers, not verified paid accounts.
3. Click **Get signed usage totals**. Released cohorts require three distinct scopes
   in that tier. Inspect the receipt's SQL, snapshot checkpoint, and client nonce.
4. Click **Try altering a record** to see browser verification reject a modified copy.
5. Run `make capture` and `make verify` to reproduce the SQL result from the OTel export.

The live stream comes directly from committed gateway records; OTel is exported
separately to this machine. `Signatures checked here` counts the records this browser
has seen; a new viewer starts with the most recent 16 records, not the full history.
The board's signed totals cover the complete current gateway session.

**Public demo mode exposes raw synthetic records, including anonymous UUID scopes.**
It must not be enabled against private customer traffic. The supervised local demo
explicitly enables it with `DEMO_PUBLIC_UI=1` and creates a separate `visitor-secret`
file shared by the SaaS and gateway. Run `DEMO_PUBLIC_UI=0 make dev` to disable all
public demo routes. Starting the gateway directly leaves this mode off by default.
Visitor tokens cannot access `/evidence/*` control endpoints.

Phone participation needs a reachable HTTPS deployment/tunnel because browser
cryptography requires a secure context. No public tunnel has been started. The UI
labels all current verification as development signatures, not hardware attestation.

In a second terminal:

```sh
cd /Users/alexkan/Documents/ChatGPT/personal/private-usage-demo
make traffic
make capture
make index
make query
make verify
```

- `traffic`: 1,000 workload requests plus setup calls for the nine scopes. First run
  adds 18 setup requests (list/create projects); subsequent runs normally add nine.
- `capture`: asks the gateway for a signed `usage_by_tier` SQL receipt, saves its
  checkpoint and the client's expected nonce, and flushes the gateway OTel exporter.
- `index`: verifies signatures and the entire checkpoint prefix, deduplicates OTLP
  retries, and builds `data/usage.duckdb`. Missing evidence fails the import.
- `query`: runs the same SQL locally over the verified export.
- `verify`: reimports the signed records, verifies receipt/nonce/snapshot binding,
  and compares the recomputed SQL result with the signed result.

Other analyses and workload settings:

```sh
.venv/bin/python -m usage_demo.cli traffic --requests 2500 --concurrency 20 --seed 7
.venv/bin/python -m usage_demo.cli capture --query-id job_reliability
.venv/bin/python -m usage_demo.cli verify
.venv/bin/python -m usage_demo.cli query customer_concentration
```

`--seed` controls client workload choices; server-side synthetic failures and UUIDs
are intentionally not deterministic. Stop generating traffic before capture for a
simple final snapshot. Later traffic is excluded from that checkpoint's prefix.
Use `--data-dir PATH` **before** the subcommand to run with a different local data directory.

## The mock SaaS

The nine fixed UUID user IDs and public test tokens are in `usage_demo/catalog.py`.
Each scope owns its own projects and jobs. UUIDs identify the scope; bearer tokens
authenticate it. A caller cannot select someone else's scope through a header.

```sh
curl -s http://127.0.0.1:8000/v1/me -H 'Authorization: Bearer demo-free-1'
curl -s http://127.0.0.1:8000/v1/me -H 'Authorization: Bearer demo-pro-1'
curl -s http://127.0.0.1:8000/v1/me -H 'Authorization: Bearer demo-enterprise-1'
```

| Tier | Project limit | Job size | Synthetic job failure probability |
|---|---:|---:|---:|
| Free | 2 | 1–2 units | 1% |
| Pro | 10 | 1–10 units | 3% |
| Enterprise | 100 | 1–10 units | 15% |

The traffic generator deliberately concentrates activity in one enterprise scope.
Its enterprise failure rate is deliberately worse, giving diligence queries something
to find. These are simulated users and configured probabilities, not real business data.
SaaS projects/jobs live in memory and reset when the SaaS process restarts.

Endpoints: `GET /v1/me`, `GET/POST /v1/projects`, `POST /v1/jobs`, and
`GET /v1/jobs/{job_id}`. Cross-scope object access returns 404. Direct access to
the SaaS port is available to the local developer but is **outside gateway coverage**.

## Evidence and SQL

Each journal record binds a session UUID, monotonic sequence, previous-record hash,
completion timestamp, authenticated user UUID/tier, normalized route/method, observed
status, processing duration, request UUID, and trace ID. It carries an Ed25519 signature
over canonical JSON. Raw request/response bodies, credentials, arbitrary paths, and query
strings are not included. Recorded duration ends before the journal commit; it is not
the client's total latency. The gateway strips query strings from upstream requests
because this demo's endpoints do not use them.

SQLite uses WAL + `synchronous=FULL`. Successful proxy responses are returned only
after committing the record. If that commit fails, the proxy returns an error, although
the upstream operation may already have happened. There is no exactly-once business
transaction guarantee. Process crashes/client disconnects during requests can leave
unobserved outcomes. This is a completed-response journal, not a transactional ingress
ledger that proves every incoming connection was recorded.

The OTel SDK exports the signed record as a span attribute, separately from normal
SaaS spans. The receiver accepts OTLP/HTTP protobuf and stores protobuf-JSON batches
as NDJSON. App-generated spans and service labels are untrusted; they never determine
the authoritative `usage` table. Forged signature claims are counted as rejected;
valid-looking signatures from another key do not establish trust.

The indexer requires a pinned public key and an independently retained checkpoint.
It checks all records from sequence 1 through the checkpoint, detecting alterations,
gaps, conflicting duplicates, and suffix deletion relative to that checkpoint.
It does not prove that an earlier checkpoint was the latest one ever issued.

The three query templates are in `usage_demo/analysis.py`:

| Query | Definition |
|---|---|
| `usage_by_tier` | Authenticated HTTP requests, distinct user scopes, 2xx successes, 5xx errors, mean recorded duration by tier |
| `customer_concentration` | Largest user scope's share of authenticated HTTP requests; user identity withheld |
| `job_reliability` | Observed `POST /v1/jobs` requests, 2xx responses, and 5xx responses by tier; includes rejected attempts |

No arbitrary SQL is accepted by the gateway. Its DuckDB instance disables external
access. Released cohorts require three distinct UUID scopes. This is coarse suppression,
**not differential privacy**, and there is no cross-query privacy budget yet. User scopes
are not independently verified companies or paid accounts. These metrics do not establish
revenue, real customers, unique business operations, or genuine demand.

SQL receipts bind the exact template text/hash, parameters, DuckDB version, signed
checkpoint, result, and client nonce. In local mode the signature establishes possession
of the pinned key, not honest execution; local reproduction checks the calculation.
Hardware-backed execution claims require the Tinfoil verification work below.

The control API requires a separate generated token in `data/control-token`:

- `GET /evidence/public-key`: public signing key; fetching it is not attestation.
- `GET /evidence/checkpoint`: current signed checkpoint.
- `POST /evidence/query`: fixed query ID and client UUID nonce → signed receipt.
- `POST /evidence/flush`: flush secondary OTel export.

Normal SaaS bearer tokens cannot call these endpoints. There is no raw-data API or
sign-arbitrary-payload API. The local DuckDB file and receiver are owner-side tools;
do not give a buyer access to this machine's raw-data directory.

## Files and inspection

All runtime data, signing keys, and logs are ignored by Git and Docker build context.

| File | Purpose |
|---|---|
| `data/journal.sqlite` | Authoritative gateway records; old sessions retained |
| `data/signing-key.pem` | Local development private key; mode 0600; never share |
| `data/public-key.pem` | Local public-key pin, established from this local process |
| `data/control-token` | Generated control-plane token; mode 0600 |
| `data/visitor-secret` | Separate anonymous-demo session secret; mode 0600 |
| `data/otel.jsonl` | Raw exported spans; may contain synthetic user UUIDs |
| `data/checkpoint.json` | Saved signed snapshot boundary |
| `data/receipt.json` | Signed query result |
| `data/expected-request.json` | Client's expected query ID/nonce |
| `data/usage.duckdb` | Verified snapshot plus ordinary OTel spans |

Every gateway restart starts a new session. Imports select only the checkpoint's
session; an old session cannot be passed off as a continuation. Local keys persist.
A configured replacement key requires a new data directory and explicit repinning.
The locally stored checkpoint/pin can themselves be replaced by this machine's owner;
buyer-side retention and hardware verification are needed for an adversarial demo.

For owner-side SQL exploration:

```python
import duckdb

db = duckdb.connect("data/usage.duckdb", read_only=True)
print(db.sql("SELECT tier, count(*) FROM usage GROUP BY tier").fetchall())
print(db.sql("SELECT service, count(*) FROM otel_spans GROUP BY service").fetchall())
print(
    db.sql("""SELECT count(DISTINCT u.request_id) FROM usage u
    JOIN otel_spans s ON u.trace_id = s.trace_id WHERE s.service = 'mock-saas'""").fetchall()
)
```

The indexer normalizes protobuf base64 trace IDs to hexadecimal for this join.
Joined application spans remain untrusted diagnostic context.

## Tinfoil handoff

The current deployment path is:

1. Publish this repository to GitHub, then run **Build demo image**. It builds the
   Linux/amd64 image in GHCR and commits its immutable digest into `tinfoil-config.yml`.
2. Ensure the GHCR image is readable by Tinfoil (public package, or separately configured
   registry credentials). No API keys are baked into the image.
3. Run the official **Tinfoil Release** workflow with a new version tag. Its follow-up
   workflow measures the config/image and publishes the signed release evidence.
4. Create the two organization secrets named `GATEWAY_CONTROL_TOKEN` and
   `DEMO_VISITOR_SECRET`, then create the `dataroom-gateway` instance from that release.
5. Independently verify the exact released workload and its `usage-signing` attested key,
   test the public participant/projector URLs, and check `/evidence/telemetry` through
   the authenticated control plane to confirm OTel capture inside the enclave.

The project-local CLI is `data/bin/tinfoil`. For finite management commands,
`.venv/bin/python -m usage_demo.deploy COMMAND ...` loads the organization admin key
from `.env` without saving it into a CLI login file. This wrapper redacts local `.env`
values from captured output. No Git credential routing or helpers are changed by it.

The older external-export configuration below remains an optional future setup.

`Dockerfile` packages the same code using `uv.lock`.
`deploy/tinfoil-config.yml.example` is an **undeployed template**, not a validated
production configuration. It puts gateway + SaaS in an enclave, grants the gateway
an Ed25519 key, and restricts export to one receiver hostname. Its `/data` storage
is ephemeral tmpfs: a new enclave starts a new collection history. Persistent sealed
storage and rollback protection are intentionally not claimed.

Before live Tinfoil verification:

1. Build the image for Tinfoil's target architecture, publish it, and replace image
   placeholders with immutable digests. Publish the concrete measured config in a
   public configuration repository and select an approved V3 CVM version.
2. Expose this machine's receiver through authenticated HTTPS with a reachable hostname.
   Bind it intentionally for the tunnel and configure `COLLECTOR_TOKEN`; configure the
   exporters' `OTEL_EXPORTER_OTLP_TRACES_HEADERS` with matching bearer authorization.
   No tunnel or public receiver has been started by this demo.
3. Provision the Tinfoil deployment credentials/control secret and confirm V3 attested-key
   access. The API key should enter through the deployment tool's secret mechanism.
4. Independently verify the exact approved workload/config and obtain `usage-signing`
   from its verified `crypto_material`. Verify that it matches the application's public
   key. Ordinary HTTPS plus `/evidence/public-key` is insufficient.
5. Connect using verified Tinfoil client transport, retain checkpoints on the buyer's
   side, and test wrong-workload, wrong-key, stale-session, and tampered-result rejection.
   The current CLI is an ordinary local HTTP client, not a Tinfoil verifier.

`SIGNING_KEY_PATH` loads a configured PKCS#8 Ed25519 PEM file (for example
`/run/tinfoil/keys/usage-signing/private_key.pem`). A missing configured key fails startup;
it never silently creates a local key. Configured keys still report
`hardware_attestation_verified: false` until the client verification is implemented.

References: [Tinfoil configuration](https://docs.tinfoil.sh/containers/configuration),
[attested keys](https://docs.tinfoil.sh/containers/attested-keys),
[OTLP Python exporter](https://opentelemetry-python.readthedocs.io/en/latest/exporter/otlp/otlp.html).

## Checks

```sh
make check
```

Tests cover user isolation, tier restrictions, timeout recording, fail-closed journal
errors, control-plane restrictions, cohort suppression, invalid signatures, missing
records, duplicate exports, nonce replay, snapshot substitution, and SQL/result changes.
Use the live commands above to validate actual HTTP export, not only in-process tests.
