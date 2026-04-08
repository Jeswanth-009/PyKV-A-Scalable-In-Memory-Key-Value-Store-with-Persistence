#!/usr/bin/env python
"""Command-line client for the FastAPI KV store."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import requests


def _parse_json_value(raw: str) -> Any:
    """Allow JSON values while still supporting plain strings."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _print_response(response: requests.Response) -> int:
    status = response.status_code
    try:
        payload = response.json()
        print(json.dumps(payload, indent=2, sort_keys=True))
    except ValueError:
        print(response.text)

    if 200 <= status < 300:
        return 0
    print(f"Request failed with status code {status}.", file=sys.stderr)
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CLI client for FastAPI KV store")
    parser.add_argument(
        "--server",
        default="http://127.0.0.1:8000",
        help="Base server URL (default: http://127.0.0.1:8000)",
    )
    parser.add_argument("--timeout", type=float, default=5.0, help="HTTP timeout seconds")

    subparsers = parser.add_subparsers(dest="command", required=True)

    get_parser = subparsers.add_parser("get", help="GET /keys/{key}")
    get_parser.add_argument("key", help="Key to fetch")

    post_parser = subparsers.add_parser("post", help="POST /keys/{key}")
    post_parser.add_argument("key", help="Key to set")
    post_parser.add_argument(
        "value",
        help="Value to set. JSON supported, plain text also accepted.",
    )

    del_parser = subparsers.add_parser("delete", help="DELETE /keys/{key}")
    del_parser.add_argument("key", help="Key to delete")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    server = args.server.rstrip("/")
    timeout = args.timeout

    try:
        if args.command == "get":
            response = requests.get(f"{server}/keys/{args.key}", timeout=timeout)
            return _print_response(response)

        if args.command == "post":
            value = _parse_json_value(args.value)
            response = requests.post(
                f"{server}/keys/{args.key}",
                json={"value": value},
                timeout=timeout,
            )
            return _print_response(response)

        if args.command == "delete":
            response = requests.delete(f"{server}/keys/{args.key}", timeout=timeout)
            return _print_response(response)

        parser.print_help()
        return 2
    except requests.RequestException as exc:
        print(f"Network error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
