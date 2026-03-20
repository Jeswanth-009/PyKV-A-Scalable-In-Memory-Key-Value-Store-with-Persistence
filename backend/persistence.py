"""
persistence.py  ·  Role 3: Persistence & Recovery Lead
========================================================
Three responsibilities:

1. AOF APPEND   — write every SET / DEL to kvcache.log using aiofiles
                   so disk I/O never blocks the event loop.
2. RECOVERY     — on startup, read and replay the log line-by-line to
                   rebuild the exact in-memory state before a crash.
3. COMPACTION   — background asyncio task that "squashes" the log down
                   to one SET per live key + a COMPACT sentinel, using
                   an atomic os.replace() rename.

Log format  (one JSON line per record):
    {"op":"SET","key":"foo","value":42,          "ts":1718000000.123}
    {"op":"DEL","key":"foo",                     "ts":1718000001.456}
    {"op":"COMPACT",                             "ts":1718000120.000}

In-memory ring buffer (last 200 records) is exposed via get_log_ring()
for the UI's terminal-style log monitor — no file polling required.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiofiles

if TYPE_CHECKING:
    from backend.lru_engine import LRUEngine

# ── File paths ───────────────────────────────────────────────────────
DATA_DIR     = Path("data")
AOF_PATH     = DATA_DIR / "kvcache.log"
COMPACT_TMP  = DATA_DIR / "kvcache.log.tmp"

# ── Op codes ─────────────────────────────────────────────────────────
OP_SET     = "SET"
OP_DEL     = "DEL"
OP_COMPACT = "COMPACT"

# ── In-memory log ring (for the UI log monitor) ──────────────────────
_ring: list[dict] = []
_ring_lock = asyncio.Lock()
_MAX_RING  = 200


async def _push_ring(record: dict) -> None:
    async with _ring_lock:
        _ring.append(record)
        if len(_ring) > _MAX_RING:
            _ring.pop(0)


def get_log_ring(limit: int = 100) -> list[dict]:
    """Return the most-recent *limit* log records (newest last)."""
    return list(_ring[-limit:])


# ── Helpers ───────────────────────────────────────────────────────────

async def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


async def _aof_append(record: dict) -> None:
    """
    Write *record* as a single JSON line to the AOF and fsync.
    aiofiles keeps all file I/O off the event-loop thread.
    """
    await _ensure_data_dir()
    line = json.dumps(record, separators=(",", ":")) + "\n"
    async with aiofiles.open(AOF_PATH, mode="a", encoding="utf-8") as fh:
        await fh.write(line)
        await fh.flush()
        os.fsync(fh.fileno())  # type: ignore[arg-type]
    await _push_ring(record)


# ── Public log writers ────────────────────────────────────────────────

async def log_set(key: str, value: Any) -> None:
    """Append a SET record to the AOF."""
    await _aof_append({"op": OP_SET, "key": key, "value": value, "ts": round(time.time(), 3)})


async def log_del(key: str) -> None:
    """Append a DEL record to the AOF."""
    await _aof_append({"op": OP_DEL, "key": key, "ts": round(time.time(), 3)})


# ── Recovery ─────────────────────────────────────────────────────────

async def recover(engine: LRUEngine) -> dict:
    """
    Replay the AOF on startup and warm *engine* with the recovered state.

    Algorithm
    ---------
    Walk every line in order:
      SET key value  →  engine.set(key, value)
      DEL key        →  engine.delete(key)
      COMPACT        →  no-op (just a marker)
      corrupt line   →  skip and warn

    Full replay is correct and idempotent because last-write-wins: a key
    that was SET then DEL ends up absent; a key SET three times ends up
    with its last value. OrderedDict preserves that final state.

    Returns a summary dict logged by main.py at startup.
    """
    if not AOF_PATH.exists():
        print("[RECOVERY] No AOF found — fresh start.")
        return {"replayed": 0, "skipped": 0, "keys_restored": 0}

    replayed = skipped = 0

    async with aiofiles.open(AOF_PATH, mode="r", encoding="utf-8") as fh:
        async for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                skipped += 1
                print(f"[RECOVERY] Skipping corrupt line: {raw!r}")
                continue

            op  = entry.get("op")
            key = entry.get("key", "")

            if op == OP_SET and key:
                await engine.set(key, entry.get("value"))
                replayed += 1
            elif op == OP_DEL and key:
                await engine.delete(key)
                replayed += 1
            elif op == OP_COMPACT:
                pass  # sentinel — nothing to do
            else:
                skipped += 1

    keys = len(await engine.keys_ordered())
    print(f"[RECOVERY] {replayed} entries replayed, {skipped} skipped -> {keys} keys live.")
    return {"replayed": replayed, "skipped": skipped, "keys_restored": keys}


# ── Compaction ────────────────────────────────────────────────────────

async def compact(engine: LRUEngine) -> dict:
    """
    Squash the AOF to a minimal snapshot of current live keys.

    Steps
    -----
    1. Read the existing AOF and build a latest-value map.
    2. Write the snapshot (one SET per live key + COMPACT sentinel)
       to a temp file, fsync.
    3. os.replace(temp → AOF) — atomic POSIX rename; the directory
       entry flips in one syscall, no reader ever sees a half-file.

    Returns a summary dict for the /admin/compact endpoint.
    """
    await _ensure_data_dir()

    # ── 1. Read and collapse ──────────────────────────────────────
    original_lines = 0
    latest: dict[str, Any] = {}

    if AOF_PATH.exists():
        async with aiofiles.open(AOF_PATH, mode="r", encoding="utf-8") as fh:
            async for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                original_lines += 1
                try:
                    entry = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                op  = entry.get("op")
                key = entry.get("key", "")
                if op == OP_SET and key:
                    latest[key] = entry.get("value")
                elif op == OP_DEL and key:
                    latest.pop(key, None)

    # ── 2. Write compacted snapshot to temp ──────────────────────
    now = round(time.time(), 3)
    async with aiofiles.open(COMPACT_TMP, mode="w", encoding="utf-8") as tmp:
        for k, v in latest.items():
            line = json.dumps({"op": OP_SET, "key": k, "value": v, "ts": now},
                              separators=(",", ":")) + "\n"
            await tmp.write(line)
        sentinel = json.dumps({"op": OP_COMPACT, "ts": now}, separators=(",", ":")) + "\n"
        await tmp.write(sentinel)
        await tmp.flush()
        os.fsync(tmp.fileno())  # type: ignore[arg-type]

    compacted_lines = len(latest) + 1  # SET lines + COMPACT sentinel

    # ── 3. Atomic rename ─────────────────────────────────────────
    os.replace(COMPACT_TMP, AOF_PATH)

    removed = max(original_lines - compacted_lines, 0)
    summary = {
        "original_lines":  original_lines,
        "compacted_lines": compacted_lines,
        "removed_lines":   removed,
    }
    print(f"[COMPACT] {original_lines} -> {compacted_lines} lines ({removed} removed).")

    # Push a COMPACT sentinel to the ring so the UI can show it
    await _push_ring({"op": OP_COMPACT, "ts": now})
    return summary


async def compaction_loop(
    engine: LRUEngine,
    interval: int = 60,
    threshold: int = 50,
) -> None:
    """
    Background asyncio task: compact every *interval* seconds if the AOF
    has grown past *threshold* lines.

    Runs forever; cancelled cleanly on server shutdown via task.cancel().
    Uses asyncio.sleep so it never blocks the event loop.
    """
    print(f"[COMPACT] Scheduler ready — interval={interval}s, threshold={threshold} lines.")
    while True:
        await asyncio.sleep(interval)
        try:
            if not AOF_PATH.exists():
                continue
            async with aiofiles.open(AOF_PATH, "r") as fh:
                line_count = 0
                async for _ in fh:
                    line_count += 1
            if line_count >= threshold:
                print(f"[COMPACT] Triggering — {line_count} lines in AOF.")
                await compact(engine)
            else:
                print(f"[COMPACT] Skipping — {line_count} lines < threshold {threshold}.")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[COMPACT] ERROR: {exc}")