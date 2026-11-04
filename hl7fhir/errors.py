"""Error types. Each carries the HL7 ACK code, HL7 table 0357 error code and FHIR issue code it maps to.

Error messages never include patient data, so they are safe to log and to return in ACKs.
"""

from __future__ import annotations

from typing import Any


class BridgeError(Exception):
    ack_code = "AE"
    error_code = "207"
    issue_code = "exception"

    def __init__(self, message: str, *, error_code: str | None = None) -> None:
        super().__init__(message)
        if error_code:
            self.error_code = error_code
        # Set by the converter when the message parsed, so an ACK can still echo MSH values.
        self.hl7_message: Any = None


class HL7ParseError(BridgeError):
    """The text is not a structurally valid HL7 v2 message."""

    ack_code = "AR"
    error_code = "102"
    issue_code = "structure"


class UnsupportedMessageError(BridgeError):
    """Valid HL7, but a message type or trigger event this bridge does not convert."""

    ack_code = "AR"
    error_code = "200"
    issue_code = "not-supported"


class MappingError(BridgeError):
    """A field required to build valid FHIR resources is missing or invalid."""

    ack_code = "AE"
    error_code = "101"
    issue_code = "required"


class ForwardError(BridgeError):
    """The FHIR server could not be reached or rejected the bundle. Senders should retry."""

    ack_code = "AE"
    error_code = "207"
    issue_code = "transient"


HL7_ERROR_TEXT = {
    "100": "Segment sequence error",
    "101": "Required field missing",
    "102": "Data type error",
    "200": "Unsupported message type",
    "201": "Unsupported event code",
    "207": "Application internal error",
}
