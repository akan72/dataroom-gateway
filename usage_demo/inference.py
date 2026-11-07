"""Confidential hosted inference, separate from gateway/SQL attestation."""

import json
import os
import secrets
import sys
from importlib.metadata import version
from pathlib import Path

from dotenv import load_dotenv
from tinfoil import TinfoilAI

DEFAULT_MODEL = "gpt-oss-120b"
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def private_client() -> TinfoilAI:
    load_dotenv(ENV_PATH, override=False)
    if not os.environ.get("TINFOIL_API_KEY"):
        raise ValueError("Configure TINFOIL_API_KEY in the project's ignored .env file")
    # tinfoil 0.14.x defaults to the literal key 'tinfoil', rather than reading this env var.
    # Follow its documented Python API; the credential exists only in .env and memory.
    return TinfoilAI(
        api_key=os.environ["TINFOIL_API_KEY"],
        timeout=60,
        user_cache_secret=secrets.token_hex(32),
    )


def smoke_test() -> dict:
    client = private_client()  # The SDK verifies the enclave before constructing its transport.
    try:
        completion = client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": "Reply with exactly this sentence: Tinfoil inference connected.",
                }
            ],
            max_completion_tokens=256,
            reasoning_effort="low",
        )
        document = client.get_verification_document()
        if document is None or not document.security_verified:
            raise ValueError("Inference returned without successful SDK attestation verification")
        reply = completion.choices[0].message.content
        if not reply:
            raise ValueError("Inference returned an empty reply")
        # Allowlist metadata: never serialize the client, credential, HTTP headers, or errors.
        return {
            "provider": "tinfoil",
            "sdk_version": version("tinfoil"),
            "model": completion.model,
            "reply": reply,
            "completion_id": completion.id,
            "sdk_attestation_verified": document.security_verified,
            "enclave_host": document.enclave_host,
            "config_repo": document.config_repo,
            "release_digest": document.release_digest,
            "verified_at": document.verified_at,
            "verification_steps": {name: step.status for name, step in document.steps.items()},
            "scope": "Hosted model inference only; the usage gateway is still local development",
        }
    finally:
        client.client.close()


def main():
    try:
        result = smoke_test()
    except Exception as exc:
        # Provider exceptions can include request/response details; do not log those.
        print(
            json.dumps(
                {
                    "success": False,
                    "error_type": type(exc).__name__,
                    "http_status": getattr(exc, "status_code", None),
                    "message": "Tinfoil inference failed; no credentials or provider body logged",
                }
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
