"""Opt-in public synthetic-data UI. Never enable against private customer traffic."""

import asyncio
import io
import os
import time
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

import qrcode
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from qrcode.image.svg import SvgPathImage

from usage_demo.analysis import query_snapshot
from usage_demo.catalog import issue_visitor
from usage_demo.crypto import Signer, canonical
from usage_demo.journal import Journal

STATIC = Path(__file__).with_name("static")


class JoinRequest(BaseModel):
    tier: Literal["Free", "Pro", "Enterprise"]


class PublicQuery(BaseModel):
    query_id: Literal["usage_by_tier", "customer_concentration", "job_reliability"]
    nonce: UUID


def install_demo(app: FastAPI, journal: Journal, signer: Signer):
    if len(os.getenv("DEMO_VISITOR_SECRET", "")) < 32:
        raise ValueError("Public demo UI requires a shared DEMO_VISITOR_SECRET of 32+ characters")
    app.mount("/assets", StaticFiles(directory=STATIC), name="demo-assets")
    query_lock = asyncio.Lock()
    last_query_at = 0.0

    @app.get("/", include_in_schema=False)
    @app.get("/live", include_in_schema=False)
    async def page():
        return FileResponse(STATIC / "index.html", headers={"cache-control": "no-store"})

    @app.post("/demo/join")
    async def join(body: JoinRequest):
        user = issue_visitor(body.tier)
        return {"user_id": user.user_id, "tier": user.tier, "token": user.token}

    @app.get("/demo/state")
    async def state():
        return {
            "state": journal.live_state(),
            "public_key_pem": signer.public_pem,
            "key_trust": "Same-origin demo key; not independently hardware-attested",
        }

    @app.get("/demo/qr.svg", include_in_schema=False)
    async def qr(origin: str):
        parsed = urlsplit(origin)
        if (
            len(origin) > 300
            or parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise HTTPException(422, "Expected an HTTP(S) origin")
        image = qrcode.make(
            origin.rstrip("/") + "/", image_factory=SvgPathImage, box_size=5, border=2
        )
        output = io.BytesIO()
        image.save(output)
        return Response(output.getvalue(), media_type="image/svg+xml")

    @app.get("/demo/events")
    async def events(request: Request):
        try:
            after = max(0, int(request.headers.get("last-event-id", "0")))
        except ValueError as exc:
            raise HTTPException(400, "Invalid event cursor") from exc

        async def stream():
            cursor = after or max(0, journal.live_state()["payload"]["seq"] - 16)
            while not await request.is_disconnected():
                rows = await asyncio.to_thread(journal.tail, cursor)
                for envelope in rows:
                    cursor = envelope["payload"]["seq"]
                    yield f"id: {cursor}\nevent: record\ndata: {canonical(envelope).decode()}\n\n"
                yield f"event: state\ndata: {canonical(journal.live_state()).decode()}\n\n"
                await asyncio.sleep(0.75)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "cache-control": "no-cache",
                "x-accel-buffering": "no",
            },
        )

    @app.post("/demo/query")
    async def query(body: PublicQuery):
        nonlocal last_query_at
        async with query_lock:
            if time.monotonic() - last_query_at < 2:
                raise HTTPException(429, "Wait two seconds before requesting another SQL receipt")
            last_query_at = time.monotonic()
            records, checkpoint = await asyncio.to_thread(journal.snapshot)
            try:
                payload = await asyncio.to_thread(
                    query_snapshot,
                    records,
                    checkpoint,
                    body.query_id,
                    str(body.nonce),
                )
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
        return signer.sign(payload)
