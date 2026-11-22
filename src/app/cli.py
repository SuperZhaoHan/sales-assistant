from __future__ import annotations

import argparse
import asyncio
import os
import sys

from app.core.config import load_config
from app.core.errors import AppError
from app.security.secrets.factory import build_secret_resolver, validate_required_secrets


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    settings = load_config()
    workers = 1 if args.reload else (args.workers or settings.server.workers)
    uvicorn.run(
        "app.asgi:app",
        host=args.host or settings.server.host,
        port=args.port or settings.server.port,
        workers=workers,
        reload=args.reload,
    )
    return 0


def _check_config(args: argparse.Namespace) -> int:
    del args
    settings = load_config()
    print(settings.model_dump_json(indent=2))
    return 0


async def _check_secrets_async() -> None:
    settings = load_config()
    resolver = build_secret_resolver(settings)
    await validate_required_secrets(settings, resolver)


def _check_secrets(args: argparse.Namespace) -> int:
    del args
    try:
        asyncio.run(_check_secrets_async())
    except AppError as exc:
        print(f"secret check failed: {exc.code}: {exc.message}", file=sys.stderr)
        return 1
    print("secret check ok")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sales-assistant")
    parser.add_argument(
        "--env",
        choices=["local", "test", "staging", "prod"],
        help="Override APP_ENV for this invocation.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="Run the HTTP service.")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--workers", type=int)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(handler=_serve)

    check_config = subparsers.add_parser("check-config", help="Print the resolved config.")
    check_config.set_defaults(handler=_check_config)

    check_secrets = subparsers.add_parser("check-secrets", help="Verify required secrets.")
    check_secrets.set_defaults(handler=_check_secrets)
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    if args.env:
        os.environ["APP_ENV"] = args.env
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
