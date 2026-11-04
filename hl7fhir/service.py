"""Conversion pipeline shared by the MLLP listener, the REST API and the CLI."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .ack import build_ack
from .config import Settings
from .errors import BridgeError, UnsupportedMessageError
from .fhir_client import FhirClient
from .mappers import BundleBuilder, map_adt, map_oru
from .parser import Message, parse

logger = logging.getLogger("hl7fhir")

SUPPORTED_EVENTS: dict[str, set[str]] = {
    "ADT": {"A01", "A02", "A03", "A04", "A05", "A06", "A07", "A08", "A11", "A13", "A28", "A31"},
    "ORU": {"R01"},
}


@dataclass
class ConversionResult:
    message: Message
    bundle: dict[str, Any]


@dataclass
class ProcessOutcome:
    ack: str
    ack_code: str
    bundle: dict[str, Any] | None = None
    error: str | None = None
    message_type: str | None = None
    control_id: str | None = None


def convert(text: str, settings: Settings) -> ConversionResult:
    """Parse an HL7 v2 message and map it to a FHIR R4 transaction Bundle."""
    message = parse(text)
    try:
        events = SUPPORTED_EVENTS.get(message.message_code)
        if events is None:
            raise UnsupportedMessageError(f"Unsupported message type {message.message_code}", error_code="200")
        if message.trigger_event not in events:
            raise UnsupportedMessageError(f"Unsupported trigger event {message.message_type}", error_code="201")
        builder = BundleBuilder(message, settings)
        (map_adt if message.message_code == "ADT" else map_oru)(message, builder)
        return ConversionResult(message, builder.build())
    except BridgeError as exc:
        exc.hl7_message = message
        raise


async def process_message(text: str, settings: Settings, fhir_client: FhirClient | None = None) -> ProcessOutcome:
    """Convert, optionally forward to FHIR, and always return an ACK for the sender.

    Logs contain only message type, control ID and outcome - never patient data.
    """
    message: Message | None = None
    bundle: dict[str, Any] | None = None
    try:
        result = convert(text, settings)
        message, bundle = result.message, result.bundle
        if fhir_client is not None:
            await fhir_client.post_transaction(bundle)
    except BridgeError as exc:
        message = message or exc.hl7_message
        outcome = ProcessOutcome(
            ack=build_ack(message, exc.ack_code, settings, str(exc), exc.error_code),
            ack_code=exc.ack_code,
            bundle=bundle,
            error=str(exc),
            message_type=message.message_type if message else None,
            control_id=message.control_id if message else None,
        )
        logger.warning(
            "rejected type=%s control_id=%s ack=%s reason=%s",
            outcome.message_type, outcome.control_id, outcome.ack_code, outcome.error,
        )
        return outcome

    logger.info("accepted type=%s control_id=%s", message.message_type, message.control_id)
    return ProcessOutcome(
        ack=build_ack(message, "AA", settings, "Message accepted"),
        ack_code="AA",
        bundle=bundle,
        message_type=message.message_type,
        control_id=message.control_id,
    )
