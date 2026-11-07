# Public Tinfoil deployment

Repository: https://github.com/akan72/dataroom-gateway

Participants: https://dataroom-gateway.akan72.containers.tinfoil.dev/

Projector: https://dataroom-gateway.akan72.containers.tinfoil.dev/live

The deployment image was built by GitHub Actions and pinned to:

```text
ghcr.io/akan72/dataroom-gateway@sha256:550f47deb404f8a2c2fa52aefcc9b604bbabf7946433ab7cbe7eb725d7cc40bb
```

The measured `v0.1.1` release is published at:
https://github.com/akan72/dataroom-gateway/releases/tag/v0.1.1

The organization runtime secrets `GATEWAY_CONTROL_TOKEN` and `DEMO_VISITOR_SECRET`
have been created. Their values and the admin credential are excluded from Git.
The three configured services are gateway (with embedded DuckDB), SaaS, and an
internal OTel collector. The network has no external egress; telemetry remains
inside Tinfoil. No Cloudflare tunnel was opened and no image was uploaded to ttl.sh.

## Running instance

The instance is running with confidential computing enabled and debug mode disabled.
Inspect it rather than creating a duplicate:

```sh
.venv/bin/python -m usage_demo.deploy container get dataroom-gateway -o json
```

## Verification after deployment

Independently verify the exact release digest recorded in `deploy/release.json`:

```sh
.venv/bin/python -m usage_demo.verify_remote https://dataroom-gateway.akan72.containers.tinfoil.dev
```

This invokes the official Tinfoil CLI against the pinned GitHub release artifact,
then checks successful workload verification, TLS-key binding, evidence freshness,
and the endorsed Ed25519 `usage-signing` key. It saves public evidence in
`data/tinfoil/attestation.json` and pins the public key in `data/tinfoil/public-key.pem`.
The JSON parser is not a substitute for attestation verification: it accepts output
only after the real CLI verification exits successfully. Strict local-config
verification requires a runtime-manifest digest; the version string in the official
release template is not that digest. The GitHub artifact pin is used instead, with
real measurement comparison and TLS/key verification; checks are never disabled.

The browser verifies record and receipt signatures. Hardware attestation is checked
independently by the CLI, not self-asserted by the server's JSON flags.

For owner requests through attestation-verified transport, run:

```sh
.venv/bin/python -m usage_demo.deploy --stream container connect dataroom-gateway --port 8002
```

Then use the runtime control token already kept in ignored `data/tinfoil`:

```sh
.venv/bin/python -m usage_demo.cli --data-dir data/tinfoil capture --gateway http://127.0.0.1:8002
```

The control-only `/evidence/telemetry` endpoint reports internal OTel collector
counts without returning raw spans. The public site cannot read runtime secrets.

This hackathon deployment uses ephemeral memory-backed storage. Reboots and
redeployments reset projects, jobs, the trace store, and the journal session.
New enclave boots generate new signing keys, requiring fresh attestation and
explicit repinning with `--repin`. No persistent production storage is claimed.

To stop the running demo:

```sh
.venv/bin/python -m usage_demo.deploy container stop dataroom-gateway -o json
```
