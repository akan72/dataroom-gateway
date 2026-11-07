# Tinfoil deployment status

Repository: https://github.com/akan72/dataroom-gateway

The deployment image was built by GitHub Actions and pinned to:

```text
ghcr.io/akan72/dataroom-gateway@sha256:380102975f41f57c75a042106ff89c2ec04aeb3c9583571877c503b2bbd2d055
```

The measured `v0.1.0` release is published at:
https://github.com/akan72/dataroom-gateway/releases/tag/v0.1.0

The organization runtime secrets `GATEWAY_CONTROL_TOKEN` and `DEMO_VISITOR_SECRET`
have been created. Their values and the admin credential are excluded from Git.
The three configured services are gateway (with embedded DuckDB), SaaS, and an
internal OTel collector. The network has no external egress; telemetry remains
inside Tinfoil. No Cloudflare tunnel was opened and no image was uploaded to ttl.sh.

## Current blocker

Tinfoil rejected instance creation because the organization has a $0 credit balance.
Its API requires at least $20 in credits before creating an instance. No Tinfoil
instance or public demo URL has been created yet. Add credits in the organization
context, then run:

```sh
.venv/bin/python -m usage_demo.deploy container create dataroom-gateway \
  --repo akan72/dataroom-gateway --tag v0.1.0 \
  --secret GATEWAY_CONTROL_TOKEN --secret DEMO_VISITOR_SECRET --no-wait -o json
```

After an accepted request, inspect that same instance rather than recreating it:

```sh
.venv/bin/python -m usage_demo.deploy container get dataroom-gateway -o json
```

## Verification after deployment

Use the actual HTTPS URL returned by Tinfoil:

```sh
.venv/bin/python -m usage_demo.verify_remote https://THE_RETURNED_HOSTNAME
```

This invokes the official Tinfoil CLI against the exact local `tinfoil-config.yml`,
then checks successful workload verification, TLS-key binding, evidence freshness,
and the endorsed Ed25519 `usage-signing` key. It saves public evidence in
`data/tinfoil/attestation.json` and pins the public key in `data/tinfoil/public-key.pem`.
The JSON parser is not a substitute for attestation verification: it accepts output
only after the real CLI verification exits successfully.

Remaining runtime checks: verified HTTP transport; healthy gateway/SaaS/collector;
public `/` and `/live`; concurrent visitor actions; signed SQL receipts bound to the
endorsed key; tamper rejection; `/evidence/telemetry` confirming internal OTel receipt.
No hardware verification result is claimed before these run against a live instance.
