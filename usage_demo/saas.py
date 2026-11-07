import asyncio
import json
import os
import random
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from pydantic import BaseModel, Field

from usage_demo.catalog import User, authenticate
from usage_demo.telemetry import provider


async def current_user(request: Request) -> User:
    user = authenticate(request.headers.get("authorization"))
    if user is None:
        raise HTTPException(401, "A demo bearer token is required")
    return user


class ProjectInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class JobInput(BaseModel):
    project_id: UUID
    units: int = Field(default=1, ge=1, le=10)


def create_app() -> FastAPI:
    from usage_demo.storage import wait_for_storage

    wait_for_storage()
    durable = None
    if os.getenv("PERSISTENT_ROOT"):
        path = Path(os.environ["DEMO_DATA_DIR"]) / "saas.sqlite"
        path.parent.mkdir(parents=True, exist_ok=True)
        durable = sqlite3.connect(path)
        durable.execute("PRAGMA journal_mode=WAL")
        durable.execute("PRAGMA synchronous=FULL")
        durable.execute(
            "CREATE TABLE IF NOT EXISTS objects (id TEXT PRIMARY KEY, kind TEXT, data TEXT)"
        )
    telemetry = provider("mock-saas")
    tracer = telemetry.get_tracer(__name__)

    @asynccontextmanager
    async def lifespan(app):
        yield
        telemetry.shutdown()
        if durable:
            durable.close()

    app = FastAPI(title="Mock scoped SaaS", lifespan=lifespan)
    projects: dict[str, dict] = {}
    jobs: dict[str, dict] = {}
    if durable:
        for kind, data in durable.execute("SELECT kind, data FROM objects"):
            obj = json.loads(data)
            (projects if kind == "project" else jobs)[obj["id"]] = obj

    def save(kind, obj):
        if durable:
            with durable:
                durable.execute(
                    "INSERT INTO objects VALUES (?, ?, ?)", (obj["id"], kind, json.dumps(obj))
                )

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/v1/me")
    async def me(user: User = Depends(current_user)):
        return {"user_id": user.user_id, "tier": user.tier}

    @app.get("/v1/projects")
    async def list_projects(user: User = Depends(current_user)):
        return [p for p in projects.values() if p["user_id"] == user.user_id]

    @app.post("/v1/projects", status_code=201)
    async def create_project(body: ProjectInput, user: User = Depends(current_user)):
        limit = {"Free": 2, "Pro": 10, "Enterprise": 100}[user.tier]
        if sum(p["user_id"] == user.user_id for p in projects.values()) >= limit:
            raise HTTPException(429, "Project limit for this tier reached")
        project = {"id": str(uuid4()), "name": body.name, "user_id": user.user_id}
        save("project", project)
        projects[project["id"]] = project
        return project

    @app.post("/v1/jobs", status_code=201)
    async def run_job(body: JobInput, user: User = Depends(current_user)):
        project = projects.get(str(body.project_id))
        if project is None or project["user_id"] != user.user_id:
            raise HTTPException(404, "Project not found")
        if user.tier == "Free" and body.units > 2:
            raise HTTPException(403, "Free jobs support at most 2 units")
        with tracer.start_as_current_span("compute-job") as span:
            span.set_attribute("saas.user_id", user.user_id)
            span.set_attribute("saas.tier", user.tier)
            span.set_attribute("job.units", body.units)
            await asyncio.sleep(body.units * 0.003)
            # Deliberate synthetic reliability defect for diligence analysis.
            if random.random() < {"Free": 0.01, "Pro": 0.03, "Enterprise": 0.15}[user.tier]:
                raise HTTPException(503, "Synthetic compute capacity failure")
            job = {
                "id": str(uuid4()),
                "user_id": user.user_id,
                "project_id": str(body.project_id),
                "units": body.units,
                "status": "complete",
            }
            save("job", job)
            jobs[job["id"]] = job
            return job

    @app.get("/v1/jobs/{job_id}")
    async def get_job(job_id: UUID, user: User = Depends(current_user)):
        job = jobs.get(str(job_id))
        if job is None or job["user_id"] != user.user_id:
            raise HTTPException(404, "Job not found")
        return job

    FastAPIInstrumentor.instrument_app(app, tracer_provider=telemetry, excluded_urls="health")
    return app
