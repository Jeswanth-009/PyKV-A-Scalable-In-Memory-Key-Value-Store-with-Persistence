"""Backend-accessible client operations for the KV Cache engine."""

from typing import Any

from backend.lru_engine import LRUEngine


async def set_value(engine: LRUEngine, key: str, value: Any) -> dict:
    evicted = await engine.set(key, value)
    return {"key": key, "value": value, "evicted_key": evicted}


async def get_value(engine: LRUEngine, key: str) -> dict:
    value = await engine.get(key)
    return {"key": key, "value": value}


async def delete_value(engine: LRUEngine, key: str) -> dict:
    deleted = await engine.delete(key)
    return {"key": key, "deleted": deleted}


async def all_keys(engine: LRUEngine) -> dict:
    items = await engine.all_items()
    return {"count": len(items), "items": items}
