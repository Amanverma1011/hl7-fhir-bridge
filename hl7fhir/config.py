"""Runtime settings, read from environment variables (see .env.example)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

_TZ = re.compile(r"^(Z|[+-](0\d|1[0-4]):[0-5]\d)$")


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else default


@dataclass(frozen=True)
class Settings:
    # Base URI for identifier systems that the sending system doesn't give as an OID/URL.
    identifier_system_base: str = "http://hospital.example.org/identifiers"
    # Used for HL7 timestamps that have a time but no offset (FHIR requires an offset).
    default_timezone: str = "+00:00"
    # When set, converted bundles are POSTed to this FHIR R4 server as transactions.
    fhir_server_url: str | None = None
    fhir_auth_token: str | None = None
    fhir_timeout_seconds: float = 15.0
    mllp_host: str = "127.0.0.1"
    mllp_port: int = 2575
    max_message_bytes: int = 1_048_576
    # Used as MSH-3/MSH-4 of ACKs when the inbound message names no receiver.
    receiving_application: str = "HL7FHIRBRIDGE"
    receiving_facility: str = ""

    def __post_init__(self) -> None:
        if not _TZ.match(self.default_timezone):
            raise ValueError("DEFAULT_TIMEZONE must look like +05:30, -04:00 or Z")
        if self.max_message_bytes < 1:
            raise ValueError("MAX_MESSAGE_BYTES must be positive")

    @classmethod
    def from_env(cls) -> Settings:
        defaults = cls()
        return cls(
            identifier_system_base=(
                _env("IDENTIFIER_SYSTEM_BASE", defaults.identifier_system_base) or ""
            ).rstrip("/"),
            default_timezone=_env("DEFAULT_TIMEZONE", defaults.default_timezone) or "+00:00",
            fhir_server_url=_env("FHIR_SERVER_URL"),
            fhir_auth_token=_env("FHIR_AUTH_TOKEN"),
            fhir_timeout_seconds=float(_env("FHIR_TIMEOUT_SECONDS", "15") or 15),
            mllp_host=_env("MLLP_HOST", defaults.mllp_host) or defaults.mllp_host,
            mllp_port=int(_env("MLLP_PORT", str(defaults.mllp_port)) or defaults.mllp_port),
            max_message_bytes=int(_env("MAX_MESSAGE_BYTES", str(defaults.max_message_bytes)) or 0),
            receiving_application=_env("RECEIVING_APPLICATION", defaults.receiving_application) or "",
            receiving_facility=_env("RECEIVING_FACILITY", "") or "",
        )
