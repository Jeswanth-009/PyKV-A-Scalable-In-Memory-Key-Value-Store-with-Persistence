#!/usr/bin/env python
"""Wrapper that delegates to backend perf compare module."""

from backend import perf_compare


if __name__ == "__main__":
    print("Running backend perf benchmark:")
    print(perf_compare.run_benchmarks())
