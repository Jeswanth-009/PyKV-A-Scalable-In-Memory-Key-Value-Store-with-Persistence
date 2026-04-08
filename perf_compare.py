#!/usr/bin/env python
"""CLI entrypoint for KV store performance comparison."""

from __future__ import annotations

import argparse
import json
import sys

from backend import perf_compare


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark KV store vs local dict")
    parser.add_argument("--server", default="http://127.0.0.1:8000", help="FastAPI server base URL")
    parser.add_argument("--dict-iterations", type=int, default=10_000, help="Dict iteration count")
    parser.add_argument("--http-iterations", type=int, default=500, help="HTTP iteration count")
    parser.add_argument("--timeout", type=float, default=5.0, help="Request timeout in seconds")
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        summary = perf_compare.run_benchmarks(
            server=args.server.rstrip("/"),
            dict_iterations=args.dict_iterations,
            http_iterations=args.http_iterations,
            timeout=args.timeout,
        )
    except Exception as exc:
        print(f"Benchmark failed because server is unreachable or returned an error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(perf_compare.format_report(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
