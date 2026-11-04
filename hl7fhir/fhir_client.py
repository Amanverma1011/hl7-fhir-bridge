"""Async client that sends transaction Bundles to a FHIR R4 server (HAPI, Azure, Google, AWS HealthLake...)."""

from __future__ import annotations

import json
from typing import Any

import httpx

from .config import Settings
from .errors import ForwardError

FHIR_JSON = "application/fhir+json"


class FhirClient:
    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {"Accept": FHIR_JSON, "Content-Type": FHIR_JSON}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/", headers=headers, timeout=timeout, transport=transport
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> FhirClient | None:
        if not settings.fhir_server_url:
            return None
        return cls(settings.fhir_server_url, settings.fhir_auth_token, settings.fhir_timeout_seconds)

    async def post_transaction(self, bundle: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post("", content=json.dumps(bundle))
        except httpx.HTTPError as exc:
            raise ForwardError(f"FHIR server unreachable ({type(exc).__name__})") from exc
        if response.status_code >= 400:
            raise ForwardError(f"FHIR server rejected the bundle (HTTP {response.status_code})")
        try:
            return response.json() if response.content else {}
        except ValueError:
            return {}

    async def aclose(self) -> None:
        await self._client.aclose()
