"""Map HL7 v2 ADT and ORU messages to a FHIR R4 transaction Bundle.

Resources with a business identifier are written with conditional updates
(``PUT Patient?identifier=system|value``), so replaying the same HL7 message updates
records instead of creating duplicates.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from . import datatypes as dt
from .config import Settings
from .errors import MappingError
from .parser import Components, Message, Segment, pick

Resource = dict[str, Any]

_NAMESPACE = uuid.UUID("6f1b2a8e-3c4d-4e5f-9a0b-1c2d3e4f5a6b")

V3_ACT_CODE = "http://terminology.hl7.org/CodeSystem/v3-ActCode"
V3_NULL_FLAVOR = "http://terminology.hl7.org/CodeSystem/v3-NullFlavor"
V3_MARITAL = "http://terminology.hl7.org/CodeSystem/v3-MaritalStatus"
V3_PARTICIPATION = "http://terminology.hl7.org/CodeSystem/v3-ParticipationType"
V3_INTERPRETATION = "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"
OBS_CATEGORY = "http://terminology.hl7.org/CodeSystem/observation-category"
ALLERGY_CLINICAL = "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical"
CONDITION_CLINICAL = "http://terminology.hl7.org/CodeSystem/condition-clinical"
CONDITION_VERIFICATION = "http://terminology.hl7.org/CodeSystem/condition-ver-status"
CONDITION_CATEGORY = "http://terminology.hl7.org/CodeSystem/condition-category"
DATA_ABSENT = "http://terminology.hl7.org/CodeSystem/data-absent-reason"
PROVENANCE_AGENT = "http://terminology.hl7.org/CodeSystem/provenance-participant-type"

GENDER = {"M": "male", "F": "female", "O": "other", "A": "other", "U": "unknown", "N": "unknown"}
MARITAL = {"M": "M", "S": "S", "D": "D", "W": "W", "A": "L", "P": "T", "U": "UNK"}
PATIENT_CLASS = {
    "E": ("EMER", "emergency"),
    "I": ("IMP", "inpatient encounter"),
    "O": ("AMB", "ambulatory"),
    "R": ("AMB", "ambulatory"),
    "P": ("PRENC", "pre-admission"),
    "B": ("IMP", "inpatient encounter"),
}
# ADT trigger event → Encounter.status. Others are derived from the discharge date.
TRIGGER_STATUS = {
    "A01": "in-progress",
    "A02": "in-progress",
    "A03": "finished",
    "A04": "arrived",
    "A05": "planned",
    "A06": "in-progress",
    "A07": "in-progress",
    "A11": "cancelled",
    "A13": "in-progress",
}
PARTICIPANTS = ((7, "ATND", "attender"), (8, "REF", "referrer"), (9, "CON", "consultant"), (17, "ADM", "admitter"))
PATIENT_ONLY_TRIGGERS = {"A28", "A31"}

REPORT_STATUS = {
    "F": "final",
    "C": "corrected",
    "P": "preliminary",
    "A": "partial",
    "R": "partial",
    "S": "partial",
    "I": "registered",
    "O": "registered",
    "X": "cancelled",
}
OBSERVATION_STATUS = {
    "F": "final",
    "U": "final",
    "C": "corrected",
    "P": "preliminary",
    "S": "preliminary",
    "R": "registered",
    "I": "registered",
    "X": "cancelled",
    "D": "entered-in-error",
    "W": "entered-in-error",
}
_OBSERVATION_STATUSES = {"registered", "preliminary", "final", "amended", "corrected", "cancelled",
                         "entered-in-error", "unknown"}
INTERPRETATION = {
    "H": "High", "L": "Low", "HH": "Critical high", "LL": "Critical low", "N": "Normal",
    "A": "Abnormal", "AA": "Critical abnormal", "<": "Off scale low", ">": "Off scale high",
    "S": "Susceptible", "R": "Resistant", "I": "Intermediate", "HU": "Significantly high",
    "LU": "Significantly low", "U": "Significant change up", "D": "Significant change down",
}
ALLERGY_CATEGORY = {"DA": "medication", "FA": "food", "EA": "environment", "AA": "environment",
                    "PA": "environment", "LA": "environment"}
ALLERGY_SEVERITY = {"SV": ("severe", "high"), "MO": ("moderate", "low"), "MI": ("mild", "low")}
DIAGNOSIS_VERIFICATION = {"A": "provisional", "W": "provisional", "F": "confirmed"}
LAB_CATEGORY = {"coding": [{"system": OBS_CATEGORY, "code": "laboratory", "display": "Laboratory"}]}


def _ref(full_url: str) -> dict[str, str]:
    return {"reference": full_url}


class BundleBuilder:
    """Collects resources into a FHIR transaction Bundle with stable urn:uuid fullUrls."""

    def __init__(self, message: Message, settings: Settings) -> None:
        self.message = message
        self.settings = settings
        self.base = settings.identifier_system_base
        self.tz = settings.default_timezone
        self._entries: list[dict[str, Any]] = []
        self._by_key: dict[str, str] = {}

    def add(self, resource: Resource, identifier: dict[str, Any] | None = None) -> str:
        rt = resource["resourceType"]
        if identifier and identifier.get("system") and identifier.get("value"):
            key = f"{rt}|{identifier['system']}|{identifier['value']}"
            if key in self._by_key:  # e.g. the same doctor is attending and admitting
                return self._by_key[key]
            token = quote(f"{identifier['system']}|{identifier['value']}", safe=":/|.-_~")
            request = {"method": "PUT", "url": f"{rt}?identifier={token}"}
        else:
            key = f"{rt}|{self.message.control_id}|{len(self._entries)}"
            request = {"method": "POST", "url": rt}
        full_url = f"urn:uuid:{uuid.uuid5(_NAMESPACE, key)}"
        self._by_key[key] = full_url
        self._entries.append({"fullUrl": full_url, "resource": resource, "request": request})
        return full_url

    def build(self) -> Resource:
        msh = self.message.msh
        now = datetime.now(UTC).isoformat(timespec="seconds")
        sender = "/".join(p for p in (msh.get(3), msh.get(4)) if p) or "unknown sender"
        provenance: Resource = {
            "resourceType": "Provenance",
            "target": [_ref(e["fullUrl"]) for e in self._entries],
            "recorded": now,
            "agent": [
                {
                    "type": {"coding": [{"system": PROVENANCE_AGENT, "code": "author"}]},
                    "who": {"display": sender},
                },
                {
                    "type": {"coding": [{"system": PROVENANCE_AGENT, "code": "assembler"}]},
                    "who": {"display": "hl7-fhir-bridge"},
                },
            ],
            "entity": [
                {
                    "role": "source",
                    "what": {
                        "identifier": {"system": f"{self.base}/hl7v2-message", "value": self.message.control_id},
                        "display": f"HL7 v2 {self.message.message_type} message {self.message.control_id}",
                    },
                }
            ],
        }
        entries = [*self._entries]
        if entries:
            entries.append({
                "fullUrl": f"urn:uuid:{uuid.uuid5(_NAMESPACE, 'Provenance|' + self.message.control_id)}",
                "resource": provenance,
                "request": {"method": "POST", "url": "Provenance"},
            })
        return {
            "resourceType": "Bundle",
            "identifier": {"system": f"{self.base}/hl7v2-message", "value": self.message.control_id},
            "type": "transaction",
            "timestamp": dt.to_fhir_instant(msh.get(7), self.tz) or now,
            "entry": entries,
        }


# ----------------------------------------------------------------------------- Patient


def map_patient(pid: Segment | None, b: BundleBuilder) -> tuple[str, str]:
    """PID → Patient. Returns (fullUrl, primary identifier value)."""
    if pid is None:
        raise MappingError("PID segment is required")
    identifiers = [i for rep in pid.repetitions(3) if (i := dt.identifier(rep, b.base, "patient"))]
    if not identifiers:
        raise MappingError("PID-3 (patient identifier list) is required")
    primary = next(
        (i for i in identifiers if i.get("type", {}).get("coding", [{}])[0].get("code") == "MR"), identifiers[0]
    )

    patient: Resource = {"resourceType": "Patient", "identifier": identifiers, "active": True}
    if names := [n for rep in pid.repetitions(5) if (n := dt.human_name(rep))]:
        patient["name"] = names
    phones = [t for rep in pid.repetitions(13) if (t := dt.telecom(rep))]
    phones += [t for rep in pid.repetitions(14) if (t := dt.telecom(rep, default_use="work"))]
    if phones:
        patient["telecom"] = phones
    if gender := GENDER.get(pid.get(8).upper()):
        patient["gender"] = gender
    if birth := dt.to_fhir_date(pid.get(7)):
        patient["birthDate"] = birth
    death, indicator = dt.to_fhir_datetime(pid.get(29), b.tz), pid.get(30).upper()
    if death:
        patient["deceasedDateTime"] = death
    elif indicator in ("Y", "N"):
        patient["deceasedBoolean"] = indicator == "Y"
    if addresses := [a for rep in pid.repetitions(11) if (a := dt.address(rep))]:
        patient["address"] = addresses
    if marital := MARITAL.get(pid.get(16).upper()):
        system = V3_NULL_FLAVOR if marital == "UNK" else V3_MARITAL
        patient["maritalStatus"] = {"coding": [{"system": system, "code": marital}]}
    return b.add(patient, primary), primary["value"]


def map_practitioner(comps: Components, b: BundleBuilder) -> str | None:
    """XCN: id^family^given^middle^suffix^prefix^degree^source table^assigning authority."""
    value, family = pick(comps, 1), pick(comps, 2)
    if not value and not family:
        return None
    practitioner: Resource = {"resourceType": "Practitioner"}
    ident = None
    if value:
        system = dt.identifier_system(pick(comps, 9, 1), pick(comps, 9, 2), pick(comps, 9, 3), b.base, "practitioner")
        ident = {"system": system, "value": value}
        practitioner["identifier"] = [ident]
    name: dict[str, Any] = {}
    if family:
        name["family"] = family
    if given := [g for g in (pick(comps, 3), pick(comps, 4)) if g]:
        name["given"] = given
    if prefix := pick(comps, 6):
        name["prefix"] = [prefix]
    if suffix := [s for s in (pick(comps, 5), pick(comps, 7)) if s]:
        name["suffix"] = suffix
    if name:
        practitioner["name"] = [name]
    return b.add(practitioner, ident)


# ----------------------------------------------------------------------------- Encounter


def map_encounter(
    pv1: Segment | None, b: BundleBuilder, patient_ref: str, trigger: str
) -> tuple[str | None, str | None]:
    """PV1 → Encounter (+ Practitioners). Returns (fullUrl, visit number)."""
    if pv1 is None:
        return None, None
    start = dt.to_fhir_datetime(pv1.get(44), b.tz)
    end = dt.to_fhir_datetime(pv1.get(45), b.tz)
    encounter: Resource = {"resourceType": "Encounter"}
    visit = dt.identifier(pv1.components(19), b.base, "visit")
    if visit:
        encounter["identifier"] = [visit]
    encounter["status"] = TRIGGER_STATUS.get(trigger) or ("finished" if end else "in-progress")
    cls = PATIENT_CLASS.get(pv1.get(2).upper())
    encounter["class"] = (
        {"system": V3_ACT_CODE, "code": cls[0], "display": cls[1]}
        if cls
        else {"system": V3_NULL_FLAVOR, "code": "UNK", "display": "unknown"}
    )
    if service := pv1.get(10):
        encounter["serviceType"] = dt.v2_concept("0069", service)
    encounter["subject"] = _ref(patient_ref)

    participants = []
    for field_no, code, display in PARTICIPANTS:
        for rep in pv1.repetitions(field_no):
            if ref := map_practitioner(rep, b):
                participants.append({
                    "type": [{"coding": [{"system": V3_PARTICIPATION, "code": code, "display": display}]}],
                    "individual": _ref(ref),
                })
    if participants:
        encounter["participant"] = participants
    if start or end:
        encounter["period"] = {k: v for k, v in (("start", start), ("end", end)) if v}

    hospitalization: dict[str, Any] = {}
    if admit_source := pv1.get(14):
        hospitalization["admitSource"] = dt.v2_concept("0023", admit_source)
    if disposition := pv1.get(36):
        hospitalization["dischargeDisposition"] = dt.v2_concept("0112", disposition)
    if hospitalization:
        encounter["hospitalization"] = hospitalization

    location = " ".join(p for p in (pv1.get(3, 1), pv1.get(3, 2), pv1.get(3, 3)) if p)
    if location:
        encounter["location"] = [{"location": {"display": location}}]
    return b.add(encounter, visit), (visit or {}).get("value")


# ----------------------------------------------------------------------------- Allergies & diagnoses


def map_allergy(al1: Segment, b: BundleBuilder, patient_ref: str, patient_key: str) -> str | None:
    comps = al1.components(3)
    code = dt.codeable_concept(comps)
    if not code:
        return None
    ident = {"system": f"{b.base}/allergy", "value": f"{patient_key}-{pick(comps, 1) or dt.slug(pick(comps, 2))}"}
    allergy: Resource = {
        "resourceType": "AllergyIntolerance",
        "identifier": [ident],
        "clinicalStatus": {"coding": [{"system": ALLERGY_CLINICAL, "code": "active"}]},
    }
    if category := ALLERGY_CATEGORY.get(al1.get(2).upper()):
        allergy["category"] = [category]
    severity = ALLERGY_SEVERITY.get(al1.get(4).upper())
    if severity:
        allergy["criticality"] = severity[1]
    allergy["code"] = code
    allergy["patient"] = _ref(patient_ref)
    if recorded := dt.to_fhir_datetime(al1.get(6), b.tz):
        allergy["recordedDate"] = recorded
    manifestations = [{"text": text} for rep in al1.repetitions(5) if (text := pick(rep, 1))]
    if manifestations:
        reaction: dict[str, Any] = {"manifestation": manifestations}
        if severity:
            reaction["severity"] = severity[0]
        allergy["reaction"] = [reaction]
    return b.add(allergy, ident)


def map_condition(
    dg1: Segment, b: BundleBuilder, patient_ref: str, encounter_ref: str | None, scope: str
) -> str | None:
    comps = dg1.components(3)
    code = dt.codeable_concept(comps) or ({"text": dg1.get(4)} if dg1.get(4) else None)
    if not code:
        return None
    ident = {"system": f"{b.base}/diagnosis", "value": f"{scope}-{pick(comps, 1) or dt.slug(dg1.get(4))}"}
    condition: Resource = {
        "resourceType": "Condition",
        "identifier": [ident],
        "clinicalStatus": {"coding": [{"system": CONDITION_CLINICAL, "code": "active"}]},
    }
    if verification := DIAGNOSIS_VERIFICATION.get(dg1.get(6).upper()):
        condition["verificationStatus"] = {"coding": [{"system": CONDITION_VERIFICATION, "code": verification}]}
    condition["category"] = [{"coding": [{"system": CONDITION_CATEGORY, "code": "encounter-diagnosis",
                                          "display": "Encounter Diagnosis"}]}]
    condition["code"] = code
    condition["subject"] = _ref(patient_ref)
    if encounter_ref:
        condition["encounter"] = _ref(encounter_ref)
    if recorded := dt.to_fhir_datetime(dg1.get(5), b.tz):
        condition["recordedDate"] = recorded
    return b.add(condition, ident)


# ----------------------------------------------------------------------------- ADT


def map_adt(message: Message, b: BundleBuilder) -> None:
    trigger = message.trigger_event
    patient_ref, patient_key = map_patient(message.first("PID"), b)
    encounter_ref, visit = (None, None)
    if trigger not in PATIENT_ONLY_TRIGGERS:
        encounter_ref, visit = map_encounter(message.first("PV1"), b, patient_ref, trigger)
    for al1 in message.all("AL1"):
        map_allergy(al1, b, patient_ref, patient_key)
    for dg1 in message.all("DG1"):
        map_condition(dg1, b, patient_ref, encounter_ref, visit or patient_key)


# ----------------------------------------------------------------------------- ORU (lab results)


def _quantity(value: int | float, units: Components, simple: bool = False) -> dict[str, Any]:
    q: dict[str, Any] = {"value": value}
    code, text, system = pick(units, 1), pick(units, 2), pick(units, 3).upper()
    if text or code:
        q["unit"] = text or code
    if code and system == "UCUM":  # only claim UCUM when the sender says the unit is UCUM
        q["system"] = dt.UCUM
        q["code"] = code
    return q


def _observation_value(obx: Segment, tz: str) -> tuple[str | None, Any]:
    raw = obx.raw(5).strip()
    if not raw or raw == '""':
        return None, None
    vtype = obx.get(2).upper()
    units = obx.components(6)
    value = obx.get(5)

    if vtype == "NM":
        number = dt.parse_number(value)
        return ("valueQuantity", _quantity(number, units)) if number is not None else ("valueString", value)
    if vtype == "SN":
        comps = obx.components(5)
        comparator, first, separator, second = (pick(comps, i) for i in (1, 2, 3, 4))
        n1, n2 = dt.parse_number(first), dt.parse_number(second)
        if separator and n1 is not None and n2 is not None:
            if separator == "-":
                return "valueRange", {"low": _quantity(n1, units), "high": _quantity(n2, units)}
            if separator in (":", "/"):
                return "valueRatio", {"numerator": {"value": n1}, "denominator": {"value": n2}}
        if n1 is not None and not separator:
            q = _quantity(n1, units)
            if comparator in ("<", "<=", ">", ">="):
                q["comparator"] = comparator
            return "valueQuantity", q
        return "valueString", "".join(p for p in (comparator, first, separator, second) if p)
    if vtype in ("CE", "CWE", "CNE"):
        concept = dt.codeable_concept(obx.components(5))
        return ("valueCodeableConcept", concept) if concept else (None, None)
    if vtype in ("DT", "TS", "DTM"):
        converted = dt.to_fhir_datetime(value, tz)
        return ("valueDateTime", converted) if converted else ("valueString", value)
    if vtype == "TM":
        converted = dt.to_fhir_time(value)
        return ("valueTime", converted) if converted else ("valueString", value)
    # ST, TX, FT and anything else: keep the text, one line per repetition
    text = "\n".join(pick(rep, 1) for rep in obx.repetitions(5) if pick(rep, 1))
    return ("valueString", text) if text else (None, None)


def _reference_range(raw: str, units: Components) -> dict[str, Any] | None:
    raw = raw.strip()
    if not raw:
        return None
    number = r"(-?\d+(?:\.\d+)?)"
    if m := re.fullmatch(rf"{number}\s*-\s*{number}", raw):
        return {
            "low": _quantity(dt.parse_number(m.group(1)), units),
            "high": _quantity(dt.parse_number(m.group(2)), units),
            "text": raw,
        }
    if m := re.fullmatch(rf"(<=|>=|<|>)\s*{number}", raw):
        side = "high" if m.group(1).startswith("<") else "low"
        return {side: _quantity(dt.parse_number(m.group(2)), units), "text": raw}
    return {"text": raw}


def _interpretation(obx: Segment) -> list[dict[str, Any]]:
    result = []
    for rep in obx.repetitions(8):
        flag = pick(rep, 1).upper()
        if not flag:
            continue
        if flag in INTERPRETATION:
            result.append({"coding": [{"system": V3_INTERPRETATION, "code": flag, "display": INTERPRETATION[flag]}]})
        else:
            result.append({"text": flag})
    return result


def map_observation(
    obx: Segment,
    notes: list[str],
    b: BundleBuilder,
    patient_ref: str,
    encounter_ref: str | None,
    order_key: str,
    default_effective: str | None,
    report_status: str,
) -> str:
    code = dt.codeable_concept(obx.components(3))
    if not code:
        raise MappingError("OBX-3 (observation identifier) is required")
    key_parts = [order_key, obx.get(3, 1) or obx.get(1), obx.get(4)]
    ident = {"system": f"{b.base}/observation", "value": "-".join(p for p in key_parts if p)}
    status = OBSERVATION_STATUS.get(obx.get(11).upper()) or (
        report_status if report_status in _OBSERVATION_STATUSES else "unknown"
    )

    obs: Resource = {
        "resourceType": "Observation",
        "identifier": [ident],
        "status": status,
        "category": [LAB_CATEGORY],
        "code": code,
        "subject": _ref(patient_ref),
    }
    if encounter_ref:
        obs["encounter"] = _ref(encounter_ref)
    if effective := dt.to_fhir_datetime(obx.get(14), b.tz) or default_effective:
        obs["effectiveDateTime"] = effective
    value_key, value = _observation_value(obx, b.tz)
    if value_key:
        obs[value_key] = value
    elif status not in ("cancelled", "entered-in-error"):
        obs["dataAbsentReason"] = {"coding": [{"system": DATA_ABSENT, "code": "unknown"}]}
    if interpretation := _interpretation(obx):
        obs["interpretation"] = interpretation
    if notes:
        obs["note"] = [{"text": n} for n in notes]
    if reference_range := _reference_range(obx.get(7), obx.components(6)):
        obs["referenceRange"] = [reference_range]
    return b.add(obs, ident)


def map_oru(message: Message, b: BundleBuilder) -> None:
    patient_ref, _ = map_patient(message.first("PID"), b)
    encounter_ref, _ = map_encounter(message.first("PV1"), b, patient_ref, message.trigger_event)

    # Group OBX and NTE segments under the OBR (order) they follow.
    groups: list[dict[str, Any]] = []
    for seg in message.segments:
        if seg.name == "OBR":
            groups.append({"obr": seg, "obx": [], "notes": []})
        elif seg.name == "OBX":
            if not groups:
                raise MappingError("OBX segment found before any OBR segment")
            groups[-1]["obx"].append((seg, []))
        elif seg.name == "NTE" and groups:
            text = "\n".join(pick(rep, 1) for rep in seg.repetitions(3) if pick(rep, 1))
            if text:
                target = groups[-1]["obx"][-1][1] if groups[-1]["obx"] else groups[-1]["notes"]
                target.append(text)
    if not groups:
        raise MappingError("ORU^R01 needs at least one OBR segment")

    for index, group in enumerate(groups, start=1):
        obr: Segment = group["obr"]
        code = dt.codeable_concept(obr.components(4))
        if not code:
            raise MappingError("OBR-4 (universal service identifier) is required")
        order_key = obr.get(3) or obr.get(2) or f"{message.control_id}-{obr.get(1) or index}"
        status = REPORT_STATUS.get(obr.get(25).upper(), "unknown")
        effective = dt.to_fhir_datetime(obr.get(7), b.tz)

        results = [
            map_observation(obx, notes, b, patient_ref, encounter_ref, order_key, effective, status)
            for obx, notes in group["obx"]
        ]
        ident = {"system": f"{b.base}/diagnostic-report", "value": order_key}
        report: Resource = {
            "resourceType": "DiagnosticReport",
            "identifier": [ident],
            "status": status,
            "category": [dt.v2_concept("0074", obr.get(24).upper() or "LAB")],
            "code": code,
            "subject": _ref(patient_ref),
        }
        if encounter_ref:
            report["encounter"] = _ref(encounter_ref)
        if effective:
            report["effectiveDateTime"] = effective
        if issued := dt.to_fhir_instant(obr.get(22), b.tz):
            report["issued"] = issued
        if results:
            report["result"] = [_ref(r) for r in results]
        if group["notes"]:
            report["conclusion"] = "\n".join(group["notes"])
        b.add(report, ident)
