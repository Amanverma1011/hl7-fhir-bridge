"""Build HL7 v2 acknowledgement (ACK) messages in original acknowledgement mode."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from .config import Settings
from .errors import HL7_ERROR_TEXT
from .parser import Delimiters, Message, escape


def build_ack(
    message: Message | None,
    code: str,
    settings: Settings,
    text: str = "",
    error_code: str | None = None,
) -> str:
    """Return an ACK. ``code`` is AA (accepted), AE (error, sender may retry) or AR (rejected)."""
    d = message.delimiters if message else Delimiters()
    msh = message.msh if message else None
    now = datetime.now(UTC).strftime("%Y%m%d%H%M%S+0000")
    trigger = message.trigger_event if message else ""

    def field(n: int) -> str:
        return msh.raw(n) if msh else ""

    msh_fields = [
        "MSH",
        d.encoding_characters,
        field(5) or escape(settings.receiving_application, d),  # we are now the sender
        field(6) or escape(settings.receiving_facility, d),
        field(3),
        field(4),
        now,
        "",
        d.component.join(["ACK", trigger, "ACK"]) if trigger else "ACK",
        uuid.uuid4().hex[:20].upper(),
        field(11) or "P",
        field(12) or "2.5.1",
    ]
    segments = [
        d.field.join(msh_fields),
        d.field.join(["MSA", code, message.control_id if message else "", escape(text, d)]),
    ]
    if code != "AA":
        err_code = error_code or "207"
        hl7_code = d.component.join([err_code, HL7_ERROR_TEXT.get(err_code, "Error"), "HL70357"])
        segments.append(d.field.join(["ERR", "", "", hl7_code, "E", "", "", "", escape(text, d)]))
    return "\r".join(segments)
