# Live demo

Open `/` for participants and `/live` for the shared screen on
https://dataroom-gateway.akan72.containers.tinfoil.dev.

1. Watch the five-minute request timeline. Each dot comes from a request whose
   signature the browser checked: Free red, Pro blue, Enterprise green. Hover for
   time, route, outcome, and sequence. The stream below shows the latest requests.
2. On `/`, choose a tier, join, and run a job. Point out its highlighted request and
   sequence number. Jobs can intentionally return synthetic capacity errors;
   failures are signed observations too.
3. Select **Get signed usage totals**. The SQL is visible above its result, formatted
   for reading. The expandable receipt contains the original exact SQL bytes, SQL
   hash, engine, signed checkpoint, browser nonce, output, and signature.
4. Select a request to inspect its signed payload. Select **Try altering a record**
   to show that changing the HTTP outcome invalidates its original signature.
5. In the repository, run `make demo-proof`. This invokes the official Tinfoil
   verifier with the release/artifact pin in `deploy/release.json`, verifies the
   boot key's binding to the persistent journal key, then checks a real request
   and a fresh SQL receipt. It rejects changed records, changed results, and reuse
   of the receipt for another nonce.

The browser checks signatures using a same-origin public key. Independent hardware
and source-code verification comes from the CLI. SQL execution is backed by the
approved enclave workload; it is not a zero-knowledge SQL proof. Synthetic UUIDs
and simulated tiers do not establish real paid customers or all-company coverage.

The background workload intentionally gives one Enterprise UUID 20% of new
workload attempts. The other eight scopes each get 10%. Prior traffic, other
visitors, and setup affect totals. The fixed `customer_concentration` and
`job_reliability` queries expose concentration and the intentionally worse
Enterprise failure rate without returning per-user rows.

See [durable storage](STORAGE.md), [system diagram](SYSTEM.md), and
[synthetic traffic](SYNTHETIC_TRAFFIC.md).
