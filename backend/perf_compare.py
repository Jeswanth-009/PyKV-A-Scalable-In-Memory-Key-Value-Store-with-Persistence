"""Performance comparison utilities for the KV Cache engine."""

from __future__ import annotations

import random
import string
import time
from typing import Any

import requests


def random_key(length: int = 10) -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=length))


def _request_timeout(timeout: float) -> tuple[float, float]:
    """Split timeout into connect/read values for faster failure behavior."""
    return (min(2.0, timeout), timeout)


def _ensure_server_ready(session: requests.Session, server: str, timeout: float) -> None:
    response = session.get(f"{server}/health", timeout=_request_timeout(timeout))
    response.raise_for_status()


def _reset_server_state(session: requests.Session, server: str, timeout: float) -> None:
    response = session.delete(f"{server}/admin/reset", timeout=_request_timeout(timeout))
    response.raise_for_status()


def local_dict_benchmark(iterations: int = 10_000) -> float:
    """Benchmark local dict with set/get/delete sequence."""
    store: dict[str, Any] = {}
    keys = [random_key() for _ in range(iterations)]
    start = time.perf_counter()
    for key in keys:
        store[key] = key
        _ = store.get(key)
        store.pop(key, None)
    return time.perf_counter() - start


def http_store_benchmark(
    server: str = "http://127.0.0.1:8000",
    iterations: int = 500,
    timeout: float = 5.0,
) -> float:
    """Benchmark FastAPI store through POST/GET/DELETE endpoints."""
    if iterations < 1:
        raise ValueError("http iterations must be >= 1")

    keys = [random_key() for _ in range(iterations)]
    start = time.perf_counter()
    with requests.Session() as session:
        _ensure_server_ready(session, server, timeout)
        _reset_server_state(session, server, timeout)

        for idx, key in enumerate(keys, start=1):
            try:
                post_response = session.post(
                    f"{server}/keys/{key}",
                    json={"value": key},
                    timeout=_request_timeout(timeout),
                )
                post_response.raise_for_status()

                get_response = session.get(
                    f"{server}/keys/{key}",
                    timeout=_request_timeout(timeout),
                )
                get_response.raise_for_status()

                delete_response = session.delete(
                    f"{server}/keys/{key}",
                    timeout=_request_timeout(timeout),
                )
                delete_response.raise_for_status()
            except requests.RequestException as exc:
                raise RuntimeError(
                    "HTTP benchmark failed at iteration "
                    f"{idx}/{iterations} for key '{key}': {exc}"
                ) from exc

    return time.perf_counter() - start


def run_benchmarks(
    server: str = "http://127.0.0.1:8000",
    dict_iterations: int = 10_000,
    http_iterations: int = 500,
    timeout: float = 5.0,
) -> dict[str, Any]:
    dict_time = local_dict_benchmark(dict_iterations)
    http_time = http_store_benchmark(server, http_iterations, timeout)

    dict_ops = dict_iterations * 3
    http_ops = http_iterations * 3

    dict_ops_per_sec = dict_ops / dict_time if dict_time else 0.0
    http_ops_per_sec = http_ops / http_time if http_time else 0.0

    return {
        "server": server,
        "dict_iterations": dict_iterations,
        "http_iterations": http_iterations,
        "dict_seconds": dict_time,
        "http_seconds": http_time,
        "dict_total_ops": dict_ops,
        "http_total_ops": http_ops,
        "dict_ops_per_sec": dict_ops_per_sec,
        "http_ops_per_sec": http_ops_per_sec,
        "slowdown_ratio": (http_ops_per_sec and (dict_ops_per_sec / http_ops_per_sec)) or None,
    }


def format_report(summary: dict[str, Any]) -> str:
    """Return a human-readable performance report."""
    return (
        "Performance Report\n"
        "==================\n"
        f"Server:           {summary['server']}\n"
        f"Dict runtime:     {summary['dict_seconds']:.6f}s\n"
        f"HTTP runtime:     {summary['http_seconds']:.6f}s\n"
        f"Dict throughput:  {summary['dict_ops_per_sec']:.2f} ops/s\n"
        f"HTTP throughput:  {summary['http_ops_per_sec']:.2f} ops/s\n"
        f"Slowdown ratio:   {summary['slowdown_ratio']:.2f}x\n"
        "\n"
        f"Dict ops:         {summary['dict_total_ops']}\n"
        f"HTTP ops:         {summary['http_total_ops']}\n"
        f"Dict iterations:  {summary['dict_iterations']}\n"
        f"HTTP iterations:  {summary['http_iterations']}"
    )


if __name__ == "__main__":
    result = run_benchmarks()
    print(format_report(result))
