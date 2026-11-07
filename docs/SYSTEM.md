# System and trust boundaries

Current deployment: https://dataroom-gateway.akan72.containers.tinfoil.dev/

The approved release, tag, and artifact digest are pinned in
[`deploy/release.json`](../deploy/release.json). The image and closed network are
defined by [`tinfoil-config.yml`](../tinfoil-config.yml).

```mermaid
flowchart LR
    Visitors["Participant browsers<br/>UUID scopes and simulated tiers"]
    Board["Projector /live<br/>Browser signature checks"]
    Verifier["Independent verifier<br/>Tinfoil CLI and demo-proof"]
    Release["GitHub measured release<br/>Expected artifact and image digests"]

    subgraph Enclave["Tinfoil confidential VM - AMD SEV-SNP"]
        subgraph GatewayContainer["Gateway container"]
            Gateway["Authenticate, forward,<br/>observe HTTP outcome"]
            Journal[("Signed SQLite journal<br/>Session, sequence, previous hash")]
            DuckDB["Embedded DuckDB<br/>Fixed SQL over verified snapshot"]
            Receipt["Signed SQL receipt<br/>Query, checkpoint, nonce, result"]
            Key["Persistent journal key<br/>Encrypted on durable volume"]
            BootKey["Hardware-endorsed boot key<br/>Signs journal-key binding"]
        end
        App["FastAPI SaaS container<br/>Scoped projects and jobs"]
        Collector[("Internal OTel collector<br/>Raw trace copies")]
        Attestation["Enclave attestation<br/>Measurement and endorsed public keys"]
    end

    Visitors -->|"HTTPS API requests"| Gateway
    Gateway -->|"Internal HTTP"| App
    App -->|"HTTP result"| Gateway
    Gateway -->|"Commit observation"| Journal
    Journal -->|"Verify chain and snapshot"| DuckDB
    DuckDB -->|"SQL output"| Receipt
    Key -.->|"Sign"| Journal
    Key -.->|"Sign"| Receipt
    BootKey -.->|"Public key only"| Attestation
    BootKey -.->|"Endorse journal public key"| Key
    Disk[("Encrypted persistent volume<br/>Journal, SaaS database and OTLP files")]
    Journal --- Disk
    App --- Disk
    Collector --- Disk
    Gateway -->|"Signed record in OTLP span"| Collector
    App -->|"Ordinary OTLP spans"| Collector
    Journal -->|"Signed demo stream over SSE"| Board
    Gateway -->|"Same-origin public key"| Board
    Receipt -->|"Approved aggregate"| Board
    Release -->|"Expected workload pin"| Verifier
    Attestation -->|"Verify hardware, code and key endorsement"| Verifier
    Journal -->|"One signed public demo record"| Verifier
    Receipt -->|"Signed result for fresh client nonce"| Verifier
```

The private signing key never leaves the enclave. The dashed signing arrows are
operations inside the gateway, not exported key material. The collector has no
public ingress and there is no external OTel export in this deployment. The journal,
not the secondary trace copy, determines signed SQL results.

## How the attestation reaches the data

```mermaid
flowchart TD
    Pin["Independent expected release pin"] --> CLI["Official Tinfoil CLI verifier"]
    Quote["Hardware attestation and code provenance"] --> CLI
    CLI --> Boot["Verified usage-signing boot public key"]
    Boot --> Binding["Verify signed journal-key binding"]
    Binding --> Endorsed["Verified persistent journal public key"]
    Endorsed --> RecordCheck["Verify API record signature"]
    Endorsed --> SQLCheck["Verify SQL receipt and checkpoint signatures"]
    APIRecord["Observed API record"] --> RecordCheck
    SQLReceipt["SQL text, parameters, engine, snapshot and result"] --> SQLCheck
    Nonce["Fresh verifier-generated nonce"] --> SQLCheck
    RecordCheck --> Evidence["Attestation-backed evidence"]
    SQLCheck --> Evidence
```

There is no hardware quote for every request. Hardware attests the approved workload
and endorses a boot key; that key binds the persistent journal key which signs observations and receipts. The
independent verifier extracts the endorsed public key and checks the actual data
signatures. Browser checkmarks alone only establish signature validity under the
key the page obtained from its own origin. `make demo-proof` supplies the independent
hardware/code/key verification that connects those signatures to the enclave.

The SQL guarantee is **TEE-attested execution of approved code**, not a zero-knowledge
SQL proof. The approved code verifies the journal snapshot, runs the fixed query in
DuckDB, and signs the query/checkpoint/nonce/result together. A valid nonce-bound
signature under the endorsed key therefore attributes that execution to the approved
gateway. An arbitrary signed SQL string without that approved execution path would
not establish correct execution.

## Boundaries to say out loud

- The public stream contains synthetic demo activity. Private customer data must not
  be served with public demo mode enabled.
- One signed sample does not prove complete capture. Snapshot-chain checks establish
  the retained prefix; they do not establish all-company traffic coverage or real demand.
- Simulated tiers and UUID scopes are not independently verified paid customers.
- An encrypted volume retains data across boots. It needs the original locally held
  unlock key after independent attestation. Disk rollback is not automatically detected.
  Hardware trust is never inferred from a server flag. See [storage](STORAGE.md).

See the [presenter walkthrough](LIVE_DEMO.md) for the live sequence.
