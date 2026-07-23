#!/usr/bin/env python3
"""
app/main.py

FastAPI backend for the Memory Inspector UI. Wraps scripts/memory_orchestrator.py
directly -- no logic duplicated or rewritten. One endpoint runs the full
classify -> route -> retrieve -> fuse -> reason pipeline and returns every
stage's output as JSON for the frontend to render.

Couchbase and Bedrock connections are established ONCE at server startup
(via the lifespan handler below) and reused across every request -- not
reconnected per-request. Opening a fresh Couchbase connection on every
request was slow (every request paid the connection setup cost) and
fragile (a cold cluster can time out on the very first attempt, which is
almost certainly what caused the first-request 500 seen in testing before
this fix -- the exact same wait_until_ready timeout pattern seen earlier
when running memory_orchestrator.py standalone against a cold cluster).

Run:
  pip install -r requirements.txt
  export CB_CONN_STR=... CB_USERNAME=... CB_PASSWORD=... CB_CA_BUNDLE=... (as before)
  export BEDROCK_REGION=us-east-1 EMBED_MODEL_ID=amazon.titan-embed-text-v1 LLM_MODEL_ID=meta.llama3-70b-instruct-v1:0
  uvicorn app.main:app --reload

Then open http://127.0.0.1:8000
"""

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

# scripts/ is a sibling directory, not a package -- add it to the path so
# we can import memory_orchestrator directly instead of duplicating any
# of its logic here.
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
import memory_orchestrator  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent / "static"

REQUIRED_ENV_VARS = ["BEDROCK_REGION", "EMBED_MODEL_ID", "LLM_MODEL_ID", "CB_CONN_STR", "CB_USERNAME", "CB_PASSWORD"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    missing = [name for name in REQUIRED_ENV_VARS if not os.environ.get(name)]
    if missing:
        # Fail loudly and immediately at startup rather than on the first
        # request -- there is no good reason to let uvicorn come up "ready"
        # if it can't actually serve a single /ask call.
        sys.exit(f"Missing required environment variables: {', '.join(missing)}")

    print("Connecting to Couchbase and Bedrock once at startup...")
    app.state.bedrock_client = memory_orchestrator.build_bedrock_client()
    app.state.cluster = memory_orchestrator.build_couchbase_cluster()
    print("Ready. Connections will be reused across all requests.")

    yield

    # Nothing to explicitly close -- the couchbase Cluster object doesn't
    # require an explicit disconnect call for this SDK version, and the
    # process is exiting anyway.


app = FastAPI(title="Agent Memory Fabric -- Memory Inspector", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AskRequest(BaseModel):
    question: str
    customer_id: str | None = None
    session_id: str | None = None


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/ask")
def ask(req: AskRequest):
    try:
        result = memory_orchestrator.run(
            req.question,
            req.customer_id,
            req.session_id,
            cluster=app.state.cluster,
            bedrock_client=app.state.bedrock_client,
        )
    except Exception as exc:  # noqa: BLE001 -- surface the real error to the UI rather than a bare 500
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return result
