"""Verify the workload with Tinfoil's CLI, then pin its endorsed Ed25519 usage key."""

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from usage_demo.crypto import verify

ROOT = Path(__file__).resolve().parent.parent
KEY_FORMAT = "https://tinfoil.sh/key/spki/v1"


def key_from_verified_report(report: dict, host: str) -> str:
    """Parse only CLI-verified output, never an arbitrary uploaded attestation JSON file."""
    if report.get("status") != "ok" or report.get("enclave") != host:
        raise ValueError("Attestation did not verify the requested enclave")
    keys = report.get("keys", {})
    if not keys.get("enclave") or keys.get("enclave") != keys.get("connection"):
        raise ValueError("TLS connection does not match the attested key")
    expires = datetime.fromisoformat(report["freshness_expires_at"].replace("Z", "+00:00"))
    if expires <= datetime.now(UTC):
        raise ValueError("Attestation freshness evidence has expired")
    matches = [
        item
        for item in report.get("crypto_material", [])
        if item.get("id") == "usage-signing" and item.get("format") == KEY_FORMAT
    ]
    if len(matches) != 1:
        raise ValueError("Expected exactly one endorsed usage-signing key")
    public = serialization.load_der_public_key(bytes.fromhex(matches[0]["data"]))
    if not isinstance(public, Ed25519PublicKey):
        raise ValueError("The endorsed usage key must be Ed25519")
    return public.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--config", type=Path)
    source.add_argument("--repo")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data/tinfoil")
    parser.add_argument("--repin", action="store_true")
    args = parser.parse_args()
    url = urlsplit(args.url)
    if url.scheme != "https" or not url.hostname or url.username or url.password:
        parser.error("Provide the public HTTPS enclave URL")
    if args.config:
        expected = ["--config", str(args.config.resolve())]
    else:
        reference = (
            args.repo
            or json.loads((ROOT / "deploy/release.json").read_text())["expected_reference"]
        )
        expected = ["--repo", reference]
    result = subprocess.run(
        [
            str(ROOT / "data/bin/tinfoil"),
            "attestation",
            "verify",
            "--json",
            "--enclave",
            url.hostname,
            *expected,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise SystemExit("Tinfoil CLI rejected workload attestation: " + result.stderr.strip())
    report = json.loads(result.stdout)
    public_pem = key_from_verified_report(report, url.hostname)
    boot_public_pem = public_pem
    response = httpx.get(f"https://{url.hostname}/demo/state", timeout=30)
    response.raise_for_status()
    state = response.json()
    if "key_binding" in state:
        binding = verify(state["key_binding"], boot_public_pem)
        if (
            binding.get("kind") != "usage-journal-key-binding-v1"
            or binding.get("public_key_pem") != state["public_key_pem"]
        ):
            raise ValueError("Enclave did not endorse the durable journal key")
        public_pem = binding["public_key_pem"]
    elif state["public_key_pem"] != boot_public_pem:
        raise ValueError("Unendorsed journal key")
    verify(state["state"], public_pem)
    args.data_dir.mkdir(parents=True, exist_ok=True)
    pin = args.data_dir / "public-key.pem"
    if pin.exists() and pin.read_text() != public_pem and not args.repin:
        raise SystemExit("Endorsed signing key changed; verify the new boot and use --repin")
    pin.write_text(public_pem)
    (args.data_dir / "boot-public-key.pem").write_text(boot_public_pem)
    (args.data_dir / "attestation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "workload_attestation_verified": True,
                "usage_key_endorsed": True,
                "enclave": url.hostname,
                "config_digest": report.get("digest"),
                "freshness_expires_at": report["freshness_expires_at"],
                "public_key_pin": str(pin),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
