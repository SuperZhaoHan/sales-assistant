from __future__ import annotations

import asyncio
import sys

from app.core.config import load_config
from app.core.errors import AppError
from app.security.secrets.factory import build_secret_resolver, validate_required_secrets


async def main() -> int:
    settings = load_config()
    resolver = build_secret_resolver(settings)
    try:
        await validate_required_secrets(settings, resolver)
    except AppError as exc:
        print(f"secret check failed: {exc.code}: {exc.message}", file=sys.stderr)
        return 1
    print(f"secret check ok (env={settings.env}, provider={settings.secrets.provider})")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
