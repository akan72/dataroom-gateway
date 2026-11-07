import base64
import hashlib
import json
import os
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


class Signer:
    def __init__(self, path: Path, *, allow_create: bool = True):
        if not path.exists():
            if not allow_create:
                raise ValueError(
                    "Configured signing key is missing; refusing a development fallback"
                )
            path.parent.mkdir(parents=True, exist_ok=True)
            key = Ed25519PrivateKey.generate()
            with path.open("xb") as file:
                os.chmod(path, 0o600)
                file.write(
                    key.private_bytes(
                        serialization.Encoding.PEM,
                        serialization.PrivateFormat.PKCS8,
                        serialization.NoEncryption(),
                    )
                )
        self.key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(self.key, Ed25519PrivateKey):
            raise ValueError("An Ed25519 signing key is required")
        public = self.key.public_key()
        self.public_pem = public.public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
        self.key_id = hashlib.sha256(
            public.public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        ).hexdigest()

    def sign(self, payload: dict) -> dict:
        # Only internal call sites can sign; there is no sign-arbitrary-data endpoint.
        return {
            "payload": payload,
            "key_id": self.key_id,
            "signature": base64.b64encode(self.key.sign(canonical(payload))).decode(),
        }


def verify(envelope: dict, public_pem: str) -> dict:
    if (
        not isinstance(envelope, dict)
        or not isinstance(envelope.get("payload"), dict)
        or not isinstance(envelope.get("signature"), str)
        or not isinstance(envelope.get("key_id"), str)
    ):
        raise ValueError("Malformed signed envelope")
    public = serialization.load_pem_public_key(public_pem.encode())
    if not isinstance(public, Ed25519PublicKey):
        raise ValueError("Expected Ed25519 public key")
    key_id = hashlib.sha256(
        public.public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    ).hexdigest()
    if envelope.get("key_id") != key_id:
        raise ValueError("Signing key does not match the pinned public key")
    try:
        public.verify(
            base64.b64decode(envelope["signature"], validate=True), canonical(envelope["payload"])
        )
    except (InvalidSignature, KeyError, TypeError) as exc:
        raise ValueError("Invalid evidence signature") from exc
    return envelope["payload"]
