"""Fixed, public DEMO credentials. UUIDs identify scopes; they are not credentials."""

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import asdict, dataclass
from uuid import NAMESPACE_DNS, UUID, uuid4, uuid5


@dataclass(frozen=True)
class User:
    user_id: str
    tier: str
    token: str


USERS = tuple(
    User(str(uuid5(NAMESPACE_DNS, f"usage-demo/{tier}/{n}")), tier, f"demo-{tier.lower()}-{n}")
    for tier in ("Free", "Pro", "Enterprise")
    for n in range(1, 4)
)
BY_TOKEN = {u.token: u for u in USERS}
TIERS = ("Free", "Pro", "Enterprise")


def issue_visitor(tier: str) -> User:
    secret = os.environ.get("DEMO_VISITOR_SECRET", "")
    if len(secret) < 32:
        raise ValueError("Anonymous demo sessions require DEMO_VISITOR_SECRET")
    if tier not in TIERS:
        raise ValueError("Invalid tier")
    user_id = str(uuid4())
    payload = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "user_id": user_id,
                    "tier": tier,
                    "expires": int(time.time()) + 14400,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return User(user_id, tier, f"visitor.{payload}.{signature}")


def authenticate(header: str | None) -> User | None:
    if not (header or "").startswith("Bearer "):
        return None
    token = header.removeprefix("Bearer ")
    if token in BY_TOKEN:
        return BY_TOKEN[token]
    secret = os.environ.get("DEMO_VISITOR_SECRET", "")
    if len(secret) < 32 or len(token) > 1024:
        return None
    try:
        prefix, payload, signature = token.split(".")
        expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if prefix != "visitor" or not hmac.compare_digest(signature, expected):
            return None
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if data["tier"] not in TIERS or data["expires"] <= time.time():
            return None
        return User(str(UUID(data["user_id"])), data["tier"], token)
    except (ValueError, KeyError, TypeError):
        return None


def public_catalog() -> list[dict]:
    return [{k: v for k, v in asdict(u).items() if k != "token"} for u in USERS]
