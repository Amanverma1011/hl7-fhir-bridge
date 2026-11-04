"""MLLP (Minimal Lower Layer Protocol) TCP listener and client - how hospitals ship HL7 v2.

Each message is framed as <VT> message <FS><CR>. Every inbound message gets an ACK on the
same connection. An AE ACK tells the sender to retry later (e.g. the FHIR server was down).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

from .ack import build_ack
from .config import Settings
from .fhir_client import FhirClient
from .service import ProcessOutcome, process_message

logger = logging.getLogger("hl7fhir.mllp")

START_BLOCK = b"\x0b"
END_BLOCK = b"\x1c\x0d"

Processor = Callable[[str], Awaitable[ProcessOutcome]]


def frame(text: str) -> bytes:
    return START_BLOCK + text.encode("utf-8") + END_BLOCK


def decode_payload(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")  # common in older HL7 feeds


class MLLPServer:
    def __init__(self, settings: Settings, processor: Processor) -> None:
        self.settings = settings
        self.processor = processor
        self._server: asyncio.Server | None = None

    async def start(self, host: str | None = None, port: int | None = None) -> asyncio.Server:
        self._server = await asyncio.start_server(
            self._handle, host or self.settings.mllp_host, self.settings.mllp_port if port is None else port
        )
        return self._server

    @property
    def port(self) -> int:
        assert self._server is not None, "server not started"
        return self._server.sockets[0].getsockname()[1]

    async def serve_forever(self) -> None:
        assert self._server is not None, "server not started"
        await self._server.serve_forever()

    async def close(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        buffer = bytearray()
        try:
            while chunk := await reader.read(65536):
                buffer.extend(chunk)
                while (end := buffer.find(END_BLOCK)) != -1:
                    framed = bytes(buffer[:end])
                    del buffer[: end + len(END_BLOCK)]
                    start = framed.find(START_BLOCK)
                    if start == -1:
                        logger.warning("discarded data without an MLLP start block")
                        continue
                    outcome = await self.processor(decode_payload(framed[start + 1 :]))
                    writer.write(frame(outcome.ack))
                    await writer.drain()
                if len(buffer) > self.settings.max_message_bytes:
                    logger.warning("message over MAX_MESSAGE_BYTES; closing connection")
                    writer.write(frame(build_ack(None, "AR", self.settings, "Message too large", "100")))
                    await writer.drain()
                    break
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()


async def send_message(host: str, port: int, text: str, timeout: float = 10.0) -> str:
    """Send one HL7 message over MLLP and return the ACK text."""
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    try:
        writer.write(frame(text))
        await writer.drain()
        data = bytearray()
        while END_BLOCK not in data:
            chunk = await asyncio.wait_for(reader.read(65536), timeout)
            if not chunk:
                break
            data.extend(chunk)
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
    start, end = data.find(START_BLOCK), data.find(END_BLOCK)
    if start == -1 or end == -1:
        raise ConnectionError("No complete MLLP acknowledgement received")
    return decode_payload(bytes(data[start + 1 : end]))


async def run_mllp(settings: Settings) -> None:
    client = FhirClient.from_settings(settings)

    async def processor(text: str) -> ProcessOutcome:
        return await process_message(text, settings, client)

    server = MLLPServer(settings, processor)
    await server.start()
    logger.info(
        "MLLP listening on %s:%s (forwarding to FHIR: %s)",
        settings.mllp_host, server.port, "yes" if client else "no",
    )
    try:
        await server.serve_forever()
    finally:
        await server.close()
        if client:
            await client.aclose()
