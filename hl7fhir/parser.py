"""Dependency-free HL7 v2.x parser for the standard pipe-delimited (ER7) encoding.

Fields are addressed with 1-based HL7 numbering, so ``pid.get(5, 2)`` is PID-5.2 (given name).
For MSH, MSH-1 is the field separator and MSH-2 the encoding characters, as in the standard.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .errors import HL7ParseError

# A parsed field repetition: a list of components, each a list of subcomponents.
Components = list[list[str]]

_LINE_SPLIT = re.compile(r"\r\n|\n|\r")
_SEGMENT_ID = re.compile(r"^[A-Z][A-Z0-9]{2}$")
_HL7_NULL = '""'


@dataclass(frozen=True)
class Delimiters:
    field: str = "|"
    component: str = "^"
    repetition: str = "~"
    escape: str = "\\"
    subcomponent: str = "&"

    @property
    def encoding_characters(self) -> str:
        return f"{self.component}{self.repetition}{self.escape}{self.subcomponent}"


def unescape(value: str, d: Delimiters) -> str:
    """Decode HL7 escape sequences such as ``\\F\\`` (field separator) or ``\\X0D\\`` (hex)."""
    esc = d.escape
    if esc not in value:
        return value
    simple = {
        "F": d.field,
        "S": d.component,
        "T": d.subcomponent,
        "R": d.repetition,
        "E": d.escape,
        ".br": "\n",
        "H": "",  # highlight on/off: formatting only
        "N": "",
    }
    out: list[str] = []
    i = 0
    while i < len(value):
        if value[i] != esc:
            out.append(value[i])
            i += 1
            continue
        end = value.find(esc, i + 1)
        if end == -1:
            out.append(value[i:])
            break
        seq = value[i + 1 : end]
        if seq in simple:
            out.append(simple[seq])
        elif seq.startswith("X") and len(seq) > 1:
            try:
                out.append(bytes.fromhex(seq[1:]).decode("latin-1"))
            except ValueError:
                out.append(value[i : end + 1])
        else:
            out.append(value[i : end + 1])
        i = end + 1
    return "".join(out)


def escape(value: str, d: Delimiters) -> str:
    """Encode delimiter characters so free text can be placed inside a field."""
    value = value.replace(d.escape, f"{d.escape}E{d.escape}")
    for char, code in (
        (d.field, "F"),
        (d.component, "S"),
        (d.repetition, "R"),
        (d.subcomponent, "T"),
    ):
        value = value.replace(char, f"{d.escape}{code}{d.escape}")
    return value.replace("\r", " ").replace("\n", f"{d.escape}.br{d.escape}")


def pick(comps: Components, component: int = 1, subcomponent: int = 1) -> str:
    """Return one component/subcomponent (1-based), or "" when absent or HL7 null (``""``)."""
    if component < 1 or component > len(comps):
        return ""
    subs = comps[component - 1]
    if subcomponent < 1 or subcomponent > len(subs):
        return ""
    value = subs[subcomponent - 1].strip()
    return "" if value == _HL7_NULL else value


class Segment:
    __slots__ = ("name", "fields", "delimiters")

    def __init__(self, name: str, fields: list[str], delimiters: Delimiters) -> None:
        self.name = name
        self.fields = fields  # fields[n] is field n; fields[0] is the segment name
        self.delimiters = delimiters

    def __repr__(self) -> str:
        return f"Segment({self.name}, {len(self.fields) - 1} fields)"

    def raw(self, n: int) -> str:
        return self.fields[n] if 0 < n < len(self.fields) else ""

    def repetitions(self, n: int) -> list[Components]:
        raw = self.raw(n)
        if not raw or raw == _HL7_NULL:
            return []
        if self.name == "MSH" and n in (1, 2):
            return [[[raw]]]
        d = self.delimiters
        return [
            [[unescape(sub, d) for sub in comp.split(d.subcomponent)] for comp in rep.split(d.component)]
            for rep in raw.split(d.repetition)
        ]

    def components(self, n: int, repetition: int = 1) -> Components:
        reps = self.repetitions(n)
        return reps[repetition - 1] if 0 < repetition <= len(reps) else []

    def get(self, n: int, component: int = 1, subcomponent: int = 1, repetition: int = 1) -> str:
        return pick(self.components(n, repetition), component, subcomponent)


class Message:
    def __init__(self, segments: list[Segment], delimiters: Delimiters) -> None:
        self.segments = segments
        self.delimiters = delimiters

    @property
    def msh(self) -> Segment:
        return self.segments[0]

    def first(self, name: str) -> Segment | None:
        return next((s for s in self.segments if s.name == name), None)

    def all(self, name: str) -> list[Segment]:
        return [s for s in self.segments if s.name == name]

    @property
    def message_code(self) -> str:
        return self.msh.get(9, 1).upper()

    @property
    def trigger_event(self) -> str:
        return self.msh.get(9, 2).upper()

    @property
    def message_type(self) -> str:
        return f"{self.message_code}^{self.trigger_event}"

    @property
    def control_id(self) -> str:
        return self.msh.get(10)

    @property
    def version(self) -> str:
        return self.msh.get(12)


def parse(text: str) -> Message:
    """Parse one HL7 v2 message. Raises :class:`HL7ParseError` if it is malformed."""
    text = text.strip("\x0b\x1c\r\n \t﻿")
    if not text.startswith("MSH"):
        raise HL7ParseError("Message must start with an MSH segment", error_code="100")
    if len(text) < 8:
        raise HL7ParseError("MSH segment is too short")

    fs = text[3]
    enc_end = text.find(fs, 4)
    if enc_end == -1:
        raise HL7ParseError("MSH-2 encoding characters are not terminated")
    enc = text[4:enc_end]
    chars = [fs, *enc[:4]]
    if len(enc) < 4 or len(set(chars)) != 5 or any(c.isalnum() or c.isspace() for c in chars):
        raise HL7ParseError("Invalid delimiter characters in MSH-1/MSH-2")
    d = Delimiters(field=fs, component=enc[0], repetition=enc[1], escape=enc[2], subcomponent=enc[3])

    segments: list[Segment] = []
    for lineno, line in enumerate(_LINE_SPLIT.split(text), start=1):
        if not line.strip():
            continue
        name = line[:3]
        if not _SEGMENT_ID.match(name) or (len(line) > 3 and line[3] != fs):
            raise HL7ParseError(f"Invalid segment identifier on line {lineno}", error_code="100")
        if name == "MSH":
            if segments:
                raise HL7ParseError("Batch messages (more than one MSH) are not supported", error_code="100")
            fields = ["MSH", fs, *line.split(fs)[1:]]
        else:
            fields = line.split(fs)
        segments.append(Segment(name, fields, d))

    message = Message(segments, d)
    if not message.message_code or not message.trigger_event:
        raise HL7ParseError("MSH-9 message type and trigger event are required", error_code="101")
    if not message.control_id:
        raise HL7ParseError("MSH-10 message control ID is required", error_code="101")
    return message
