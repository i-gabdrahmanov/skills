#!/usr/bin/env python3
"""Download the pinned PlantUML jar into the per-user cache directory."""
from __future__ import annotations

import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import JAR_URL, get_jar_path  # noqa: E402

MIN_EXPECTED_BYTES = 5 * 1024 * 1024


def main() -> int:
    target = get_jar_path()
    if target.is_file() and target.stat().st_size >= MIN_EXPECTED_BYTES:
        print(f"jar already present at {target}, skipping")
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {JAR_URL}")
    print(f"            → {target}")
    with requests.get(JAR_URL, stream=True, timeout=60) as response:
        response.raise_for_status()
        tmp = target.with_suffix(".jar.part")
        written = 0
        with tmp.open("wb") as fh:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if chunk:
                    fh.write(chunk)
                    written += len(chunk)
        if written < MIN_EXPECTED_BYTES:
            tmp.unlink(missing_ok=True)
            print(
                f"ERROR: downloaded file is suspiciously small ({written} bytes)",
                file=sys.stderr,
            )
            return 1
        tmp.replace(target)

    print(f"OK: saved {target} ({written // 1024} KiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
