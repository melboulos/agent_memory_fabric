#!/usr/bin/env python3
"""
app/main.py

FastAPI backend for the Memory Inspector UI. Wraps scripts/memory_orchestrator.py
directly -- no logic duplicated or rewritten. One endpoint runs the full
classify -> route -> retrieve -> fuse -> reason pipeline and returns every
stage's output as JSON for the frontend to render.

Run:
  pip install -r requirements.txt
  export CB_CONN_STR=... CB_USERNAME=... CB_PASSWORD=... CB_CA_BUNDLE=... (as before)
  export BEDROCK_REGION=us-east-1 EMBED_MODEL_ID=amazon.titan-embed-text-v1 LLM_MODEL_ID=meta.llama3-70b-instruct-v1:0
  uvicorn app.main:app --reload

Then open http://127.0.0.1:8000
"""

import os
import sys
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

app = FastAPI(title="Agent Memory Fabric -- Memory Inspector")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).resolve().parent / "static"


class AskRequest(BaseModel):
    question: str
    customer_id: str | None = None
    session_id: str | None = None


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/ask")
def ask(req: AskRequest):
    required = ["BEDROCK_REGION", "EMBED_MODEL_ID", "LLM_MODEL_ID", "CB_CONN_STR", "CB_USERNAME", "CB_PASSWORD"]
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise HTTPException(
            status_code=500,
            detail=f"Server is missing required environment variables: {', '.join(missing)}. "
                   f"Set them before starting uvicorn, same as when running memory_orchestrator.py directly.",
        )

    try:
        result = memory_orchestrator.run(req.question, req.customer_id, req.session_id)
    except Exception as exc:  # noqa: BLE001 -- surface the real error to the UI rather than a bare 500
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return result
