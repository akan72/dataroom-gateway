# Encrypted durable storage

The `dataroom` persistent mount is shared inside the confidential VM:

| Container | Durable path | Contents |
| --- | --- | --- |
| Gateway | `/durable/gateway` | SQLite journal, metadata, persistent journal signing key |
| SaaS | `/durable/saas` | SQLite projects and completed jobs |
| Collector | `/durable/collector` | Raw OTLP trace copies |

DuckDB remains in enclave memory and reconstructs its query input from the verified
journal snapshot. Container scratch files stay in `/tmp`.

The managed disk is allocated separately from its mount declaration. An attached
disk alone is insufficient: it must be unlocked. `usage_demo.storage` starts a
locked gateway and blocks SaaS and collector startup until the mount is writable.
No application traffic or ephemeral journal fallback is accepted while locked.

## Unlock after deployment

The 32-byte disk secret is kept only in the ignored local `.env` as
`TINFOIL_VOLUME_KEY`. Never upload it as a Tinfoil-managed secret. Back up the
original key in storage you control; losing it makes the disk unrecoverable.

```sh
# Only for a newly allocated, blank disk:
.venv/bin/python -m usage_demo.unlock_remote --initialize

# Every subsequent enclave boot: reuse the original key, never initialize again.
.venv/bin/python -m usage_demo.unlock_remote
.venv/bin/python -m usage_demo.verify_remote \
  https://dataroom-gateway.akan72.containers.tinfoil.dev
```

The unlock helper opens the official Tinfoil verified proxy with the expected
release pin before sending the key. It never passes the disk key on a command line,
prints it, or stores it in the image. The approved gateway sends it to Tinfoil's
volume control socket inside the enclave. Authentication alone does not establish
the confidentiality of key delivery; the verified transport is required.

This is manual unlock: a reboot requires this local helper. Unattended boots would
require an independently controlled, attestation-aware keyserver. There is no
Tinfoil-managed fallback carrying the disk key.

## Journal continuity and trust

The hardware-endorsed `usage-signing` boot key rotates with the VM. A separate
journal key lives encrypted on the durable volume. Each approved boot signs a
`usage-journal-key-binding-v1` envelope binding that persistent public key to the
new boot key. The verifier checks hardware, release, boot endorsement, key binding,
then journal/receipt signatures. It does not pretend a saved journal key is itself
the fresh hardware-generated key.

On startup the journal verifies every retained record signature, sequence, and
predecessor hash, rebuilds counters, and resumes the same journal session. Missing
keys, changed signatures, and broken chains fail recovery. The authenticated
`GET /evidence/export` endpoint returns a signed checkpoint and its full record
prefix for independently verified backups. Never expose it as a public raw-data API.

Encryption and hash chains do **not** detect replay of an older valid disk. Retain
checkpoints and verified exports independently and compare them during recovery.
This demo does not claim automatic rollback prevention or replicated disaster recovery.

The previous RAM-only release has no complete journal-export API. Before the first
durable cutover, signed SQL receipts are archived locally under `data/archive`.
They preserve attested aggregate evidence, not a restorable copy of the old raw
journal. The new durable journal starts at the cutover.

Volume-backed updates stop the old enclave before starting its replacement, so
there is downtime. Verify, unlock using the original key, check retained data and
fresh receipts, then resume traffic. See Tinfoil's
[encrypted volumes](https://docs.tinfoil.sh/containers/encrypted-volumes) and
[update behavior](https://docs.tinfoil.sh/containers/updates).
