"""Conversions from HL7 v2 data types (TS, CE/CWE, CX, XPN, XAD, XTN, NM) to FHIR R4 data types."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from .parser import Components, pick

V2_TABLE = "http://terminology.hl7.org/CodeSystem/v2-{table}"
UCUM = "http://unitsofmeasure.org"

# Coding-system names seen in CE/CWE component 3 (HL7 table 0396) → FHIR canonical URIs.
CODE_SYSTEMS = {
    "LN": "http://loinc.org",
    "LOINC": "http://loinc.org",
    "SCT": "http://snomed.info/sct",
    "SNM": "http://snomed.info/sct",
    "SNOMED": "http://snomed.info/sct",
    "I10": "http://hl7.org/fhir/sid/icd-10",
    "ICD10": "http://hl7.org/fhir/sid/icd-10",
    "I10C": "http://hl7.org/fhir/sid/icd-10-cm",
    "ICD10CM": "http://hl7.org/fhir/sid/icd-10-cm",
    "I9C": "http://hl7.org/fhir/sid/icd-9-cm",
    "RXNORM": "http://www.nlm.nih.gov/research/umls/rxnorm",
    "RXN": "http://www.nlm.nih.gov/research/umls/rxnorm",
    "NDC": "http://hl7.org/fhir/sid/ndc",
    "CPT": "http://www.ama-assn.org/go/cpt",
    "C4": "http://www.ama-assn.org/go/cpt",
    "CVX": "http://hl7.org/fhir/sid/cvx",
    "UCUM": UCUM,
}

_TS = re.compile(
    r"^(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?(?:\.(\d{1,6}))?([+-]\d{4})?$"
)
_OID = re.compile(r"^[0-2](\.\d+)+$")


# ----------------------------------------------------------------------------- dates


def to_fhir_datetime(value: str, default_tz: str = "+00:00") -> str | None:
    """HL7 TS/DTM (``YYYY[MM[DD[HH[MM[SS[.S]]]]]][+/-ZZZZ]``) → FHIR dateTime, keeping precision."""
    m = _TS.match(value.strip()) if value else None
    if not m:
        return None
    year, month, day, hour, minute, second, frac, tz = m.groups()
    try:
        datetime(int(year), int(month or 1), int(day or 1), int(hour or 0), int(minute or 0), int(second or 0))
    except ValueError:
        return None
    if tz and not (0 <= int(tz[1:3]) <= 14 and 0 <= int(tz[3:5]) <= 59):
        return None
    if month is None:
        return year
    if day is None:
        return f"{year}-{month}"
    date = f"{year}-{month}-{day}"
    if hour is None:
        return date
    out = f"{date}T{hour}:{minute or '00'}:{second or '00'}"
    if frac and second:
        out += f".{frac}"
    return out + (f"{tz[:3]}:{tz[3:]}" if tz else default_tz)


def to_fhir_date(value: str) -> str | None:
    converted = to_fhir_datetime(value)
    return converted[:10] if converted else None


def to_fhir_instant(value: str, default_tz: str = "+00:00") -> str | None:
    """FHIR instant needs a full time, so date-only HL7 values return None."""
    converted = to_fhir_datetime(value, default_tz)
    return converted if converted and "T" in converted else None


def to_fhir_time(value: str) -> str | None:
    m = re.match(r"^(\d{2})(\d{2})?(\d{2})?", value.strip()) if value else None
    if not m or int(m.group(1)) > 23 or int(m.group(2) or 0) > 59 or int(m.group(3) or 0) > 59:
        return None
    return f"{m.group(1)}:{m.group(2) or '00'}:{m.group(3) or '00'}"


# ----------------------------------------------------------------------------- numbers


def parse_number(raw: str) -> int | float | None:
    try:
        number = Decimal(raw.strip())
    except (InvalidOperation, AttributeError):
        return None
    if not number.is_finite():
        return None
    if number == number.to_integral_value() and "." not in raw and "e" not in raw.lower():
        return int(number)
    return float(number)


# ----------------------------------------------------------------------------- codes


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "local"


def code_system_uri(name: str) -> str | None:
    if not name:
        return None
    key = name.strip().upper()
    if key in CODE_SYSTEMS:
        return CODE_SYSTEMS[key]
    m = re.fullmatch(r"HL7(\d{4})", key)
    if m:
        return V2_TABLE.format(table=m.group(1))
    if name.startswith(("http://", "https://", "urn:")):
        return name
    return None


def coding(code: str, display: str = "", system_name: str = "") -> dict[str, str] | None:
    if not code:
        return None
    result: dict[str, str] = {}
    system = code_system_uri(system_name)
    if system:
        result["system"] = system
    result["code"] = code
    if display:
        result["display"] = display
    return result


def codeable_concept(comps: Components) -> dict[str, Any] | None:
    """CE/CWE: identifier^text^system^alt identifier^alt text^alt system."""
    codings = [
        c
        for offset in (0, 3)
        if (c := coding(pick(comps, 1 + offset), pick(comps, 2 + offset), pick(comps, 3 + offset)))
    ]
    text = pick(comps, 2) or pick(comps, 5)
    if not codings and not text:
        return None
    concept: dict[str, Any] = {}
    if codings:
        concept["coding"] = codings
    if text:
        concept["text"] = text
    return concept


def v2_concept(table: str, code: str, display: str = "") -> dict[str, Any]:
    entry = {"system": V2_TABLE.format(table=table), "code": code}
    if display:
        entry["display"] = display
    return {"coding": [entry]}


# ----------------------------------------------------------------------------- identifiers


def identifier_system(namespace: str, universal_id: str, universal_type: str, base: str, kind: str) -> str:
    """Pick an Identifier.system: the sender's OID/URI when given, else a stable URI under ``base``."""
    if universal_id:
        if universal_type.upper() == "ISO" or _OID.match(universal_id):
            return f"urn:oid:{universal_id}"
        if universal_id.startswith(("http://", "https://", "urn:")):
            return universal_id
    if namespace:
        return f"{base}/{slug(namespace)}/{kind}"
    return f"{base}/{kind}"


def identifier(comps: Components, base: str, kind: str = "id") -> dict[str, Any] | None:
    """CX: id^check digit^check scheme^assigning authority (HD)^identifier type code."""
    value = pick(comps, 1)
    if not value:
        return None
    type_code = pick(comps, 5).upper()
    system = identifier_system(
        pick(comps, 4, 1), pick(comps, 4, 2), pick(comps, 4, 3), base, slug(type_code) if type_code else kind
    )
    result: dict[str, Any] = {}
    if type_code:
        result["type"] = v2_concept("0203", type_code)
    result["system"] = system
    result["value"] = value
    return result


# ----------------------------------------------------------------------------- people

_NAME_USE = {"L": "official", "D": "usual", "M": "maiden", "N": "nickname", "S": "anonymous"}


def human_name(comps: Components) -> dict[str, Any] | None:
    """XPN: family^given^middle^suffix^prefix^degree^name type."""
    family = pick(comps, 1)
    given = [g for g in (pick(comps, 2), pick(comps, 3)) if g]
    suffix, prefix, degree = pick(comps, 4), pick(comps, 5), pick(comps, 6)
    if not family and not given:
        return None
    name: dict[str, Any] = {}
    if use := _NAME_USE.get(pick(comps, 7).upper()):
        name["use"] = use
    if family:
        name["family"] = family
    if given:
        name["given"] = given
    if prefix:
        name["prefix"] = [prefix]
    if suffixes := [s for s in (suffix, degree) if s]:
        name["suffix"] = suffixes
    return name


_ADDRESS_USE = {"H": ("home", None), "B": ("work", None), "O": ("work", None), "C": ("temp", None),
                "BA": ("billing", None), "M": (None, "postal")}


def address(comps: Components) -> dict[str, Any] | None:
    """XAD: street^other designation^city^state^zip^country^address type."""
    lines = [line for line in (pick(comps, 1), pick(comps, 2)) if line]
    city, state, postal, country = pick(comps, 3), pick(comps, 4), pick(comps, 5), pick(comps, 6)
    if not (lines or city or state or postal or country):
        return None
    use, kind = _ADDRESS_USE.get(pick(comps, 7).upper(), (None, None))
    result: dict[str, Any] = {}
    if use:
        result["use"] = use
    if kind:
        result["type"] = kind
    if lines:
        result["line"] = lines
    for key, value in (("city", city), ("state", state), ("postalCode", postal), ("country", country)):
        if value:
            result[key] = value
    return result


_TELECOM_USE = {"PRN": "home", "ORN": "home", "VHN": "home", "WPN": "work", "PRS": "mobile"}


def telecom(comps: Components, default_use: str | None = None) -> dict[str, Any] | None:
    """XTN: number^use code^equipment type^email^country^area^local number."""
    use_code, equipment, email = pick(comps, 2).upper(), pick(comps, 3).upper(), pick(comps, 4)
    if email or use_code == "NET" or equipment in ("INTERNET", "X.400"):
        system, value = "email", email or pick(comps, 1)
    else:
        country, area, local = pick(comps, 5), pick(comps, 6), pick(comps, 7)
        if local:
            value = " ".join(p for p in (f"+{country}" if country else "", area, local) if p)
        else:
            value = pick(comps, 1)
        system = {"FX": "fax", "BP": "pager"}.get(equipment, "phone")
    if not value:
        return None
    result: dict[str, Any] = {"system": system, "value": value}
    use = "mobile" if equipment == "CP" else _TELECOM_USE.get(use_code, default_use)
    if use:
        result["use"] = use
    return result
