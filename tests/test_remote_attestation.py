import copy
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from usage_demo.verify_remote import KEY_FORMAT, key_from_verified_report


@pytest.fixture
def verified_report():
    key = Ed25519PrivateKey.generate().public_key()
    return {
        "status": "ok",
        "enclave": "demo.example.com",
        "keys": {"enclave": "public-fingerprint", "connection": "public-fingerprint"},
        "freshness_expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "crypto_material": [
            {
                "id": "usage-signing",
                "format": KEY_FORMAT,
                "data": key.public_bytes(
                    serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
                ).hex(),
            }
        ],
    }


def test_endorsed_usage_key_can_be_pinned(verified_report):
    assert key_from_verified_report(verified_report, "demo.example.com").startswith(
        "-----BEGIN PUBLIC KEY-----",
    )


@pytest.mark.parametrize(
    "change", ["host", "tls", "expiry", "missing-key", "duplicate-key", "status"]
)
def test_invalid_verified_report_metadata_is_rejected(verified_report, change):
    report = copy.deepcopy(verified_report)
    if change == "host":
        report["enclave"] = "other.example.com"
    elif change == "tls":
        report["keys"]["connection"] = "different-key"
    elif change == "expiry":
        report["freshness_expires_at"] = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    elif change == "missing-key":
        report["crypto_material"] = []
    elif change == "duplicate-key":
        report["crypto_material"] *= 2
    else:
        report["status"] = "fail"
    with pytest.raises(ValueError):
        key_from_verified_report(report, "demo.example.com")
