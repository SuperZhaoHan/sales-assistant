from __future__ import annotations

import sys

import httpx


def main() -> int:
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    try:
        response = httpx.get(f"{base_url.rstrip('/')}/health/ready", timeout=5.0)
    except httpx.HTTPError as exc:
        print(f"smoke test failed: {exc}", file=sys.stderr)
        return 1
    print(response.status_code, response.text)
    return 0 if response.status_code == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
