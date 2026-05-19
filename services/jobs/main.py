#!/usr/bin/env python3
"""Run aggregation on interval (hourly by default)."""
import os
import time

from aggregate import run_all

INTERVAL = int(os.environ.get("JOB_INTERVAL", "3600"))


def main():
    print(f"Aggregation jobs every {INTERVAL}s")
    while True:
        try:
            result = run_all()
            print(f"Aggregation complete: {result}")
        except Exception as e:
            print(f"Aggregation error: {e}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
