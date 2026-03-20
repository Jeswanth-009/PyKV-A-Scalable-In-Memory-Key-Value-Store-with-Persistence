# PYKV Cache Engine

A full-stack LRU key-value cache platform built with FastAPI, featuring:

- In-memory LRU eviction with O(1) operations
- Durable append-only persistence and crash recovery
- Automatic and manual log compaction
- Primary-to-standby replication for high availability
- Browser UI with login/register flow and real-time dashboard
- Integrated admin routes for internal client actions and benchmarking

This README is intentionally detailed and maps directly to each project file.

---

## 1) What This Project Can Do

At runtime, this project can act as a complete cache service and monitoring app:

1. Serve a browser-authenticated dashboard
2. Accept key-value writes, reads, deletes, list, and clear
3. Track cache metrics (hits, misses, hit-rate, memory estimate, evictions)
4. Persist all state-changing operations to disk (AOF log)
5. Recover in-memory cache from AOF on startup
6. Compact AOF to reduce log size growth
7. Replicate write/delete operations from primary to standby
8. Expose admin APIs for direct internal client-style actions
9. Run a benchmark comparing local dict speed versus HTTP API speed

---

## 2) Project Structure and File-by-File Purpose

## Root files

- [main_v2.py](main_v2.py)
  - Main FastAPI application
  - Registers all routes and response models
  - Wires backend engine + persistence + replication + frontend page serving
  - Handles app lifespan startup/shutdown tasks

- [FEATURES.md](FEATURES.md)
  - Notes describing added features and operation model

- [client.py](client.py)
  - Lightweight wrapper script (current behavior: informational output)

- [perf_compare.py](perf_compare.py)
  - Wrapper script delegating benchmark run to backend benchmark module

## Backend

- [backend/lru_engine.py](backend/lru_engine.py)
  - Core async LRU engine
  - Uses OrderedDict and asyncio.Lock
  - Maintains counters and recent evictions

- [backend/persistence.py](backend/persistence.py)
  - AOF append/write functions
  - Startup recovery replay
  - Log compaction (with atomic replace)
  - In-memory log ring for dashboard log stream

- [backend/client.py](backend/client.py)
  - Async helper operations wrapping engine methods for admin client route

- [backend/perf_compare.py](backend/perf_compare.py)
  - Benchmark logic:
    - local Python dict
    - HTTP requests against cache API

## Frontend

- [frontend/login.html](frontend/login.html)
  - Login page
  - Browser localStorage user/session handling
  - Redirects to dashboard

- [frontend/register.html](frontend/register.html)
  - Registration page
  - localStorage-backed user creation
  - Redirect to login on success

- [frontend/dashboard.html](frontend/dashboard.html)
  - Real-time dashboard UI
  - Polls stats/log endpoints
  - Visualizes MRU -> LRU ordering
  - Provides testing controls for set/get/del/list/clear/compact

## Data

- [data/kvcache.log](data/kvcache.log)
  - Append-only operation log file used for persistence and recovery

---

## 3) Core Architecture

### 3.1 LRU store design

The cache engine in [backend/lru_engine.py](backend/lru_engine.py) uses OrderedDict semantics:

- MRU item at right side
- LRU item at left side
- GET or SET promotes key via move_to_end(last=True)
- Capacity overflow evicts via popitem(last=False)

Time complexity characteristics:

- GET: O(1)
- SET update: O(1)
- SET insert + possible eviction: O(1)
- DELETE: O(1)

### 3.2 Concurrency model

All public engine methods are async and guarded by asyncio.Lock.
This prevents concurrent coroutine races in shared state.

### 3.3 Persistence model (AOF)

For each mutating operation:

- SET writes a JSON line record
- DEL writes a JSON line record

AOF replay on startup reconstructs state deterministically using last-write-wins behavior.

### 3.4 Compaction model

Compaction:

1. Reads AOF
2. Collapses latest state per key
3. Writes compact snapshot to temp file
4. Atomically swaps temp -> AOF

This keeps long-lived logs manageable.

### 3.5 Replication model

Primary instance optionally forwards write/delete operations to standby by calling standby store endpoints.

- Configured by REPLICA_URL
- Standby mode controlled by IS_STANDBY
- Replication failures are non-fatal and logged

---

## 4) FastAPI Endpoints

All routes are declared in [main_v2.py](main_v2.py).

### 4.1 Frontend pages

- GET /
  - Redirect to /login
- GET /login
  - Serve login page
- GET /register
  - Serve register page
- GET /dashboard
  - Serve dashboard page

### 4.2 Health

- GET /health
  - Returns status and timestamp

### 4.3 Store operations

- GET /keys/{key}
  - Read key (promotes to MRU)
  - 404 when absent

- POST /keys/{key}
  - Upsert JSON-serializable value
  - Returns evicted key when eviction happened
  - Writes AOF
  - Replicates SET and potential eviction DEL when configured

- DELETE /keys/{key}
  - Delete a single key
  - 404 when absent
  - Writes AOF
  - Replicates DEL when configured

- GET /keys
  - List all items in MRU-first order

- DELETE /keys
  - Clear all keys
  - Writes DEL per key to AOF
  - Replicates each DEL when configured

### 4.4 Stats and logs

- GET /stats
  - Unified metrics payload:
    - capacity, current_size, fill_pct
    - memory_bytes
    - total_sets/gets/hits/misses/evictions
    - hit_rate_pct
    - uptime_seconds
    - recent_evictions
    - keys (MRU -> LRU)

- GET /logs?limit=50
  - Returns recent ring-buffer log entries for dashboard terminal monitor

### 4.5 Admin

- POST /admin/compact
  - Manually trigger compaction

- DELETE /admin/reset
  - Clears in-memory cache and removes AOF

- POST /admin/client
  - Executes internal backend client action:
    - set/get/delete/keys/stats

- GET /admin/benchmark
  - Runs backend performance comparison and returns metrics

---

## 5) Startup and Lifespan Behavior

Implemented in [main_v2.py](main_v2.py):

On startup:

1. Replays AOF via recover()
2. Starts background compaction scheduler
3. Optionally runs integrated benchmark when AUTO_RUN_INTEGRATION is enabled and instance is primary

On shutdown:

1. Cancels compaction task cleanly

---

## 6) Frontend Behavior

### 6.1 Authentication UX

Frontend authentication is browser-local and not server-authenticated:

- Users stored in localStorage key mf_users
- Session stored in localStorage key mf_session

Login and register pages ensure simple app entry flow.

### 6.2 Dashboard data flow

[frontend/dashboard.html](frontend/dashboard.html) polls:

- /stats for metrics and keys
- /logs for terminal stream

It renders:

- Top summary strip
- Fill bar
- LRU visual list
- Operation tool panel
- Log terminal
- Stats cards
- Recent eviction list

The stats render function includes defensive numeric fallbacks to avoid undefined/NaN visual states.

---

## 7) Configuration

Environment variables used in [main_v2.py](main_v2.py):

- REPLICA_URL
  - Example: http://127.0.0.1:8001
  - If empty, replication is disabled

- IS_STANDBY
  - true/1 marks process as standby
  - Standby does not forward replication

- AUTO_RUN_INTEGRATION
  - true/1 runs benchmark in startup lifespan on primary

---

## 8) How To Run

## 8.1 Install dependencies

```bash
pip install fastapi uvicorn aiofiles httpx requests pydantic
```

## 8.2 Start a single instance

```bash
python main_v2.py
```

Open:

- http://127.0.0.1:8000/login
- http://127.0.0.1:8000/docs

### Demo credentials

- Username: admin
- Password: admin1234

## 8.3 Start primary + standby replication

Terminal A (standby):

```bash
set IS_STANDBY=1
python -m uvicorn main_v2:app --reload --port 8001
```

Terminal B (primary):

```bash
set REPLICA_URL=http://127.0.0.1:8001
python -m uvicorn main_v2:app --reload --port 8000
```

Use port 8000 for normal operations; writes/deletes mirror to standby.

---

## 9) Example API Calls

## 9.1 Write

```bash
curl -X POST http://127.0.0.1:8000/keys/user:1 \
  -H "Content-Type: application/json" \
  -d "{\"value\":{\"name\":\"Alice\",\"score\":98}}"
```

## 9.2 Read

```bash
curl http://127.0.0.1:8000/keys/user:1
```

## 9.3 Delete

```bash
curl -X DELETE http://127.0.0.1:8000/keys/user:1
```

## 9.4 List

```bash
curl http://127.0.0.1:8000/keys
```

## 9.5 Stats

```bash
curl http://127.0.0.1:8000/stats
```

## 9.6 Admin benchmark

```bash
curl http://127.0.0.1:8000/admin/benchmark
```

---

## 10) Internal Data Contracts

### 10.1 AOF record types

- SET record

```json
{"op":"SET","key":"foo","value":42,"ts":1718000000.123}
```

- DEL record

```json
{"op":"DEL","key":"foo","ts":1718000001.456}
```

- COMPACT sentinel

```json
{"op":"COMPACT","ts":1718000120.000}
```

### 10.2 /stats payload shape

```json
{
  "capacity": 5,
  "current_size": 1,
  "fill_pct": 20.0,
  "memory_bytes": 468,
  "total_sets": 1,
  "total_gets": 0,
  "total_hits": 0,
  "total_misses": 0,
  "total_evictions": 0,
  "hit_rate_pct": 0.0,
  "uptime_seconds": 12.4,
  "recent_evictions": [],
  "keys": ["user:1"]
}
```

---

## 11) Benchmarking Details

Benchmark logic in [backend/perf_compare.py](backend/perf_compare.py):

- local_dict_benchmark(n=20000)
  - pure in-process dict set/get
- http_benchmark(n=1000)
  - POST+GET against running API
- run_benchmarks()
  - returns dict_time, http_time, ratio, server

Interpretation:

- local dict is baseline upper-bound speed
- HTTP benchmark includes serialization, network stack, and API overhead
- ratio indicates service overhead relative to pure in-memory baseline

---

## 12) Operational Notes

1. If dashboard seems stale, hard refresh browser cache
2. If API is unreachable, verify server process and port binding
3. If stats show zero unexpectedly, confirm app instance and endpoint target are consistent
4. Use /admin/compact periodically in long-running sessions

---

## 13) Known Constraints

1. Browser auth is localStorage-based (not secure server-side auth)
2. Replication is best-effort (no ack/retry queue)
3. AOF fsync on each write favors durability over max throughput
4. No formal test suite file exists yet in this repository

---

## 14) Future Improvements (Recommended)

1. Replace localStorage auth with server-side auth + password hashing
2. Add retry/backoff queue for replication durability
3. Add unit/integration tests for engine, persistence, and routes
4. Add CI checks for lint/type/tests
5. Add configurable cache capacity via env var injection into engine constructor

---

## 15) Quick Feature Checklist

- LRU eviction: Yes
- Async-safe core engine: Yes
- AOF durability: Yes
- Crash recovery: Yes
- Automatic compaction: Yes
- Manual compaction endpoint: Yes
- Dashboard visualization: Yes
- Login/register UI: Yes
- Primary/standby replication: Yes
- Integrated backend client route: Yes
- Integrated benchmark route: Yes

This project currently functions as a complete educational and practical cache-service platform with observability and durability features built in.
