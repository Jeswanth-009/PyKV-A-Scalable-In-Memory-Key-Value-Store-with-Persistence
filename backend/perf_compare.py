"""Performance comparison code for KV Cache engine."""

import random
import string
import time
from typing import Dict, Any

import requests


def random_key(n=8):
    return "".join(random.choices(string.ascii_letters + string.digits, k=n))


def local_dict_benchmark(n=20000):
    d = {}
    keys = [random_key() for _ in range(n)]
    t0 = time.time()
    for k in keys:
        d[k] = k
    for k in keys:
        _ = d.get(k)
    t1 = time.time()
    return t1 - t0


def http_benchmark(server: str = "http://127.0.0.1:8000", n=1000):
    keys = [random_key() for _ in range(n)]
    t0 = time.time()
    for k in keys:
        requests.post(f"{server}/keys/{k}", json={"value": k})
    for k in keys:
        requests.get(f"{server}/keys/{k}")
    t1 = time.time()
    return t1 - t0


def run_benchmarks(server: str = "http://127.0.0.1:8000") -> Dict[str, Any]:
    dict_time = local_dict_benchmark()
    http_time = http_benchmark(server)
    return {
        "dict_time": dict_time,
        "http_time": http_time,
        "ratio": http_time / dict_time if dict_time else None,
        "server": server,
    }


if __name__ == "__main__":
    summary = run_benchmarks()
    print("Benchmark result:", summary)
