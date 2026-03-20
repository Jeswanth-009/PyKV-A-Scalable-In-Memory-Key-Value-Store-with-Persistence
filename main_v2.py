"""
main.py  ·  Role 1: API & Systems Architect
============================================
FastAPI application that wires together all four roles:

  Role 1 (this file)  ─ endpoints, Pydantic validation, CORS, /stats
  Role 2              ─ LRUEngine (OrderedDict + asyncio.Lock)
  Role 3              ─ persistence (AOF log, recovery, compaction)

Run:
    uvicorn main:app --reload --port 8000

Swagger UI: http://127.0.0.1:8000/docs
"""

import asyncio
import os
import time
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

FRONTEND_DIR = Path(__file__).parent / "frontend"

from backend.lru_engine import LRUEngine
from backend import client as backend_client
from backend import perf_compare as backend_perf
from backend.persistence import (
    compact,
    compaction_loop,
    get_log_ring,
    log_del,
    log_set,
    recover,
    AOF_PATH,
)

# ── Shared engine (created before lifespan so routes can reference it) ──
engine = LRUEngine(capacity=5)

# ── Replication config ─────────────────────────────────────────────
REPLICA_URL = os.getenv("REPLICA_URL", "").rstrip("/")
IS_STANDBY = os.getenv("IS_STANDBY", "0") in ("1", "true", "True")
AUTO_RUN_INTEGRATION = os.getenv("AUTO_RUN_INTEGRATION", "0") in ("1", "true", "True")

async def replicate_entry(op: str, key: str, value: Any = None) -> None:
    """Mirror operations to standby instance using standard store endpoints."""
    if IS_STANDBY or not REPLICA_URL:
        return

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            if op == "SET":
                await client.post(f"{REPLICA_URL}/keys/{key}", json={"value": value})
            elif op == "DEL":
                await client.delete(f"{REPLICA_URL}/keys/{key}")
            else:
                return
    except Exception as exc:
        print(f"[REPLICATION] Failed to replicate {op} {key} to {REPLICA_URL}: {exc}")


# ════════════════════════════════════════════════════════════════════
#  Pydantic Models  (Role 1: strict input validation)
# ════════════════════════════════════════════════════════════════════

class SetRequest(BaseModel):
    """
    Payload for POST /keys/{key}.
    *value* can be any JSON-serialisable type: string, number, bool,
    list, or nested object.
    """
    value: Any = Field(
        ...,
        description="Any JSON-serialisable value.",
        examples=[{"temperature": 32, "city": "Mumbai"}],
    )

    @field_validator("value")
    @classmethod
    def must_be_json_serialisable(cls, v: Any) -> Any:
        import json
        try:
            json.dumps(v)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"value must be JSON-serialisable: {exc}") from exc
        return v


class KeyResponse(BaseModel):
    key:    str
    value:  Any
    source: str = "cache"   # always "cache" for GETs


class SetResponse(BaseModel):
    key:         str
    value:       Any
    evicted_key: str | None = None
    message:     str


class DeleteResponse(BaseModel):
    key:     str
    deleted: bool
    message: str


class StatsResponse(BaseModel):
    """
    Role 1 "Secret Task": everything the UI needs in one response.
    Returned by GET /stats.
    """
    # Engine metrics
    capacity:         int
    current_size:     int
    fill_pct:         float
    memory_bytes:     int
    total_sets:       int
    total_gets:       int
    total_hits:       int
    total_misses:     int
    total_evictions:  int
    hit_rate_pct:     float
    uptime_seconds:   float
    recent_evictions: list[dict]
    # Live key list (MRU → LRU) — used by the LRU Visualizer widget
    keys:             list[str]


class ClientCommand(BaseModel):
    action: str
    key:    str | None = None
    value:  Any | None = None


class CompactResponse(BaseModel):
    original_lines:  int
    compacted_lines: int
    removed_lines:   int
    message:         str


# ════════════════════════════════════════════════════════════════════
#  Lifespan: startup recovery + compaction scheduler
# ════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ───────────────────────────────────────────────────
    print("[STARTUP] Replaying AOF ...")
    summary = await recover(engine)
    print(f"[STARTUP] Recovery done: {summary}")

    # Background compaction task (runs every 60 s, fires at 50 lines)
    compact_task = asyncio.create_task(
        compaction_loop(engine, interval=60, threshold=50),
        name="compaction-loop",
    )
    print("[STARTUP] Compaction scheduler started.")

    if AUTO_RUN_INTEGRATION and not IS_STANDBY:
        print("[STARTUP] Running integrated client/benchmark checks.")
        try:
            bench_summary = await asyncio.to_thread(backend_perf.run_benchmarks)
            print(f"[STARTUP] Benchmark summary: {bench_summary}")
        except Exception as exc:
            print(f"[STARTUP] Benchmark failed: {exc}")

    yield   # ← server is live and serving requests

    # ── Shutdown ──────────────────────────────────────────────────
    compact_task.cancel()
    try:
        await compact_task
    except asyncio.CancelledError:
        pass
    print("[SHUTDOWN] Clean exit.")


# ════════════════════════════════════════════════════════════════════
#  App
# ════════════════════════════════════════════════════════════════════

app = FastAPI(
    title="KV Cache Engine",
    description="LRU Key-Value store · AOF persistence · Real-time stats",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS — allow any origin so the HTML dashboard works from file:// or
# a local dev server without extra configuration.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Serve frontend pages ──────────────────────────────────────────
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

@app.get("/login", include_in_schema=False)
async def serve_login():
    return FileResponse(FRONTEND_DIR / "login.html")

@app.get("/register", include_in_schema=False)
async def serve_register():
    return FileResponse(FRONTEND_DIR / "register.html")

@app.get("/dashboard", include_in_schema=False)
async def serve_dashboard():
    return FileResponse(FRONTEND_DIR / "dashboard.html")


# ════════════════════════════════════════════════════════════════════
#  Routes
# ════════════════════════════════════════════════════════════════════

# ── Health / root redirect ────────────────────────────────────────
@app.get("/", include_in_schema=False)
async def root():
    """Redirect browser to login page."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/login")

@app.get("/health", tags=["Health"])
async def health():
    return {
        "status":  "ok",
        "message": "KV Cache Engine is running",
        "ts":      round(time.time(), 3),
    }


# ── GET a value by key ────────────────────────────────────────────
@app.get(
    "/keys/{key}",
    response_model=KeyResponse,
    tags=["Store"],
    summary="Get a cached value",
)
async def get_key(key: str):
    """
    Retrieve the value for *key* from the LRU cache.
    Accessing a key marks it as Most-Recently-Used (moves it to the
    top of the eviction order).
    """
    value = await engine.get(key)
    if value is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Key '{key}' not found in cache.",
        )
    return KeyResponse(key=key, value=value, source="cache")


# ── SET a key-value pair ──────────────────────────────────────────
@app.post(
    "/keys/{key}",
    response_model=SetResponse,
    tags=["Store"],
    summary="Set a key-value pair",
    status_code=status.HTTP_201_CREATED,
)
async def set_key(key: str, body: SetRequest):
    """
    Insert or update *key* with any JSON-serialisable *value*.

    If the cache is already at capacity the Least-Recently-Used entry
    is automatically evicted and its key is reported in the response.
    Both the SET and any resulting eviction DEL are written to the AOF.
    """
    evicted = await engine.set(key, body.value)
    await log_set(key, body.value)
    if evicted:
        await log_del(evicted)

    # Replication to secondary (primary only)
    await replicate_entry("SET", key, body.value)
    if evicted:
        await replicate_entry("DEL", evicted)

    msg = f"Key '{key}' set successfully."
    if evicted:
        msg += f" Evicted LRU key: '{evicted}'."

    return SetResponse(key=key, value=body.value, evicted_key=evicted, message=msg)


# ── DELETE a specific key ─────────────────────────────────────────
@app.delete(
    "/keys/{key}",
    response_model=DeleteResponse,
    tags=["Store"],
    summary="Delete a key",
)
async def delete_key(key: str):
    """Explicitly remove *key* from the cache. Writes a DEL to the AOF."""
    deleted = await engine.delete(key)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Key '{key}' not found in cache.",
        )
    await log_del(key)

    # Replication to secondary (primary only)
    await replicate_entry("DEL", key)

    return DeleteResponse(key=key, deleted=True, message=f"Key '{key}' deleted.")


# ── LIST all keys ─────────────────────────────────────────────────
@app.get(
    "/keys",
    tags=["Store"],
    summary="List all keys (MRU first)",
)
async def list_keys():
    items = await engine.all_items()
    return {"count": len(items), "items": items}


# ── CLEAR entire cache ────────────────────────────────────────────
@app.delete(
    "/keys",
    tags=["Store"],
    summary="Clear entire cache",
)
async def clear_all():
    cleared = await engine.clear()
    for k in cleared:
        await log_del(k)
        await replicate_entry("DEL", k)
    return {"cleared": cleared, "count": len(cleared), "message": "Cache cleared."}


# ── /stats  (Role 1 "Secret Task") ────────────────────────────────
@app.get(
    "/stats",
    response_model=StatsResponse,
    tags=["Stats"],
    summary="Live memory usage, metrics, and key list for the UI",
)
async def get_stats():
    """
    Single endpoint that gives the dashboard everything it needs:
      • Live keys ordered MRU → LRU  (drives the LRU Visualizer)
      • Hit/miss/eviction counters   (drives the Stats panel)
      • Memory usage estimate        (drives the Memory gauge)
      • Fill percentage              (drives the capacity bar)
    """
    snap = engine.snapshot()
    keys = await engine.keys_ordered()
    return StatsResponse(**snap, keys=keys)


# ── /admin/client — execute key operations through backend client module ─
@app.post(
    "/admin/client",
    tags=["Admin"],
    summary="Run a backend client action in the current process",
)
async def admin_client_cmd(cmd: ClientCommand):
    action = cmd.action.lower()
    if action == "set":
        if cmd.key is None or cmd.value is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="key and value required for set")
        result = await backend_client.set_value(engine, cmd.key, cmd.value)
        await log_set(cmd.key, cmd.value)
        await replicate_entry("SET", cmd.key, cmd.value)
        return result
    if action == "get":
        if cmd.key is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="key required for get")
        result = await backend_client.get_value(engine, cmd.key)
        return result
    if action == "delete":
        if cmd.key is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="key required for delete")
        result = await backend_client.delete_value(engine, cmd.key)
        if result.get("deleted"):
            await log_del(cmd.key)
            await replicate_entry("DEL", cmd.key)
        return result
    if action == "keys":
        return await backend_client.all_keys(engine)
    if action == "stats":
        return await get_stats()

    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown client action: {cmd.action}")


# ── /admin/benchmark — run perf comparison periodically or on demand ─
@app.get(
    "/admin/benchmark",
    tags=["Admin"],
    summary="Run the integrated performance benchmark",
)
async def admin_benchmark(server: str = "http://127.0.0.1:8000"):
    result = await asyncio.to_thread(backend_perf.run_benchmarks, server)
    return result


# ── /logs  — AOF ring buffer for the UI terminal monitor ──────────
@app.get(
    "/logs",
    tags=["Stats"],
    summary="Recent AOF log entries (for the terminal log monitor)",
)
async def get_logs(limit: int = Query(default=50, ge=1, le=200)):
    """
    Returns the last *limit* records from the in-memory ring buffer.
    The UI polls this endpoint every second to simulate a live terminal.
    """
    return {
        "count":   limit,
        "entries": get_log_ring(limit),
    }


# ── /admin/compact  — manual compaction trigger ───────────────────
@app.post(
    "/admin/compact",
    response_model=CompactResponse,
    tags=["Admin"],
    summary="Manually trigger AOF compaction",
)
async def trigger_compact():
    """Compact the AOF immediately. Also available automatically on schedule."""
    summary = await compact(engine)
    return CompactResponse(**summary, message="Compaction complete.")


# ── /admin/reset  — wipe everything (dev/testing) ─────────────────
@app.delete(
    "/admin/reset",
    tags=["Admin"],
    summary="Wipe cache and delete AOF (dev only)",
)
async def admin_reset():
    await engine.clear()
    if AOF_PATH.exists():
        AOF_PATH.unlink()
    return {"message": "Cache and AOF wiped.", "ts": round(time.time(), 3)}


if __name__ == "__main__":
    import uvicorn
    print("[MAIN] Starting server from main_v2.py")
    uvicorn.run("main_v2:app", host="127.0.0.1", port=8000, reload=True)
