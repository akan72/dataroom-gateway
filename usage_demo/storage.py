"""Manual encrypted-volume unlock; the key arrives only over an attested connection."""

import asyncio
import hmac
import os
import socket
import time
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse


def writable() -> bool:
    root = Path(os.environ["PERSISTENT_ROOT"])
    probe = root / f".write-probe-{uuid4()}"
    try:
        with probe.open("xb") as file:
            file.write(b"ready")
            file.flush()
            os.fsync(file.fileno())
        probe.unlink()
        return True
    except OSError:
        return False


def wait_for_storage():
    if not os.getenv("PERSISTENT_ROOT"):
        return
    while not writable():
        time.sleep(1)


def unlock(key: bytes, initialize: bool):
    name = os.environ["PERSISTENT_VOLUME_NAME"]
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as connection:
        connection.settimeout(60)
        connection.connect(f"/run/tinfoil/volumes/{name}/control.sock")
        connection.sendall(b"\x02" + (b"i" if initialize else b"u") + key)
        if connection.recv(64) != b"ok":
            raise ValueError("Encrypted volume unlock was rejected")
    if not writable():
        raise ValueError("Volume did not become writable")


class StorageGateway:
    def __init__(self):
        self.delegate = None
        self.stack = AsyncExitStack()
        self.lock = asyncio.Lock()

        @asynccontextmanager
        async def lifespan(app):
            # A process restart can reuse an already-unlocked volume in this VM.
            if writable():
                await self.start_gateway()
            yield
            await self.stack.aclose()

        self.bootstrap = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)

        @self.bootstrap.get("/health")
        async def health():
            return {"status": "storage-locked", "storage": "encrypted-volume", "ready": False}

        @self.bootstrap.post("/evidence/storage/unlock")
        async def release(request: Request):
            token = os.environ.get("GATEWAY_CONTROL_TOKEN", "")
            if len(token) < 24 or not hmac.compare_digest(
                request.headers.get("authorization", ""), f"Bearer {token}"
            ):
                raise HTTPException(401, "Control-plane authorization required")
            operation = request.headers.get("x-volume-operation", "unlock")
            if operation not in ("unlock", "initialize"):
                raise HTTPException(422, "Unknown storage operation")
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 64:
                    raise HTTPException(413, "Invalid volume key length")
            if len(body) != 32:
                raise HTTPException(422, "A 32-byte volume key is required")
            async with self.lock:
                if self.delegate is None:
                    try:
                        await asyncio.to_thread(unlock, bytes(body), operation == "initialize")
                        await self.start_gateway()
                    except (OSError, ValueError) as exc:
                        raise HTTPException(
                            503, "Storage unlock or journal recovery failed"
                        ) from exc
            return {"status": "unlocked", "storage": "encrypted-volume", "ready": True}

        @self.bootstrap.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
        async def locked(path: str):
            return JSONResponse({"detail": "Encrypted storage is locked"}, status_code=503)

    async def start_gateway(self):
        from usage_demo.gateway import create_app

        candidate = create_app()
        await self.stack.enter_async_context(candidate.router.lifespan_context(candidate))
        self.delegate = candidate

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "lifespan"
            and self.delegate is not None
            and scope.get("path") != "/evidence/storage/unlock"
        ):
            await self.delegate(scope, receive, send)
        else:
            await self.bootstrap(scope, receive, send)


def gateway_app():
    return StorageGateway()
