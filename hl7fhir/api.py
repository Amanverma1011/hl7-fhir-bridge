"""REST API: convert HL7 v2 to FHIR over HTTP, with interactive docs at /docs."""

from __future__ import annotations

from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import __version__
from .config import Settings
from .errors import BridgeError, ForwardError
from .fhir_client import FHIR_JSON, FhirClient
from .mllp import decode_payload
from .service import SUPPORTED_EVENTS, convert

EXAMPLE = (
    "MSH|^~\\&|EPIC|CITYHOSP|HL7FHIRBRIDGE|CITYHOSP|20261105083000+0530||ADT^A01^ADT_A01|MSG00001|P|2.5.1\r"
    "PID|1||MRN100234^^^CITYHOSP^MR||Sharma^Priya||19900214|F\r"
    "PV1|1|I|CARDIO^0412^A"
)


def operation_outcome(status: int, code: str, text: str) -> JSONResponse:
    body = {"resourceType": "OperationOutcome", "issue": [{"severity": "error", "code": code, "diagnostics": text}]}
    return JSONResponse(body, status_code=status, media_type=FHIR_JSON)


def create_app(settings: Settings | None = None, fhir_client: FhirClient | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = fhir_client is None
        app.state.fhir_client = fhir_client or FhirClient.from_settings(settings)
        yield
        if owned and app.state.fhir_client:
            await app.state.fhir_client.aclose()

    app = FastAPI(
        title="HL7 v2 → FHIR R4 Bridge",
        version=__version__,
        description="Converts HL7 v2 ADT and ORU messages into FHIR R4 transaction Bundles.",
        lifespan=lifespan,
    )
    metrics: Counter[str] = Counter()

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__, "fhir_forwarding": bool(settings.fhir_server_url)}

    @app.get("/supported")
    async def supported() -> dict[str, list[str]]:
        return {code: sorted(events) for code, events in SUPPORTED_EVENTS.items()}

    @app.get("/metrics")
    async def get_metrics() -> dict[str, int]:
        return dict(metrics)

    @app.post(
        "/convert",
        summary="Convert one HL7 v2 message to a FHIR transaction Bundle",
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"text/plain": {"schema": {"type": "string"}, "example": EXAMPLE}},
            }
        },
    )
    async def convert_message(request: Request, forward: bool = False) -> JSONResponse:
        """Send the raw HL7 text as the body. With `?forward=true` the Bundle is also posted to
        the configured FHIR server and the server's transaction-response is returned."""
        body = await request.body()
        if len(body) > settings.max_message_bytes:
            return operation_outcome(413, "too-costly", "Message exceeds MAX_MESSAGE_BYTES")
        try:
            result = convert(decode_payload(body), settings)
        except BridgeError as exc:
            metrics["rejected"] += 1
            return operation_outcome(422, exc.issue_code, str(exc))
        metrics[f"converted.{result.message.message_type.replace('^', '_')}"] += 1

        if not forward:
            return JSONResponse(result.bundle, media_type=FHIR_JSON)
        client: FhirClient | None = request.app.state.fhir_client
        if client is None:
            return operation_outcome(400, "not-supported", "FHIR_SERVER_URL is not configured")
        try:
            response = await client.post_transaction(result.bundle)
        except ForwardError as exc:
            metrics["forward_failed"] += 1
            return operation_outcome(502, exc.issue_code, str(exc))
        metrics["forwarded"] += 1
        return JSONResponse(response, media_type=FHIR_JSON)

    return app
