"""Command line: ``hl7fhir convert|serve-api|serve-mllp|send``."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from .config import Settings
from .errors import BridgeError
from .mllp import decode_payload, run_mllp, send_message
from .service import convert


def _read(path: str) -> str:
    return decode_payload(sys.stdin.buffer.read() if path == "-" else Path(path).read_bytes())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hl7fhir", description="HL7 v2 → FHIR R4 bridge")
    sub = parser.add_subparsers(dest="command", required=True)

    c = sub.add_parser("convert", help="convert an HL7 file (or - for stdin) and print the FHIR Bundle")
    c.add_argument("file")
    c.add_argument("--compact", action="store_true", help="print JSON on one line")

    a = sub.add_parser("serve-api", help="run the REST API")
    a.add_argument("--host", default="127.0.0.1")
    a.add_argument("--port", type=int, default=8000)

    sub.add_parser("serve-mllp", help="run the MLLP listener (MLLP_HOST / MLLP_PORT)")

    s = sub.add_parser("send", help="send an HL7 file to an MLLP listener and print the ACK")
    s.add_argument("file")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=2575)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = Settings.from_env()

    if args.command == "convert":
        try:
            result = convert(_read(args.file), settings)
        except BridgeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(result.bundle, indent=None if args.compact else 2, ensure_ascii=False))
    elif args.command == "serve-api":
        import uvicorn

        from .api import create_app

        uvicorn.run(create_app(settings), host=args.host, port=args.port)
    elif args.command == "serve-mllp":
        try:
            asyncio.run(run_mllp(settings))
        except KeyboardInterrupt:
            pass
    elif args.command == "send":
        ack = asyncio.run(send_message(args.host, args.port, _read(args.file)))
        print(ack.replace("\r", "\n"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
