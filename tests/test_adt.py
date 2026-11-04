import pytest
from conftest import msh

from hl7fhir.errors import MappingError, UnsupportedMessageError
from hl7fhir.service import convert


def all_references(node):
    if isinstance(node, dict):
        if isinstance(node.get("reference"), str):
            yield node["reference"]
        for value in node.values():
            yield from all_references(value)
    elif isinstance(node, list):
        for value in node:
            yield from all_references(value)


def test_a01_bundle_shape(sample, settings, of_type):
    bundle = convert(sample("adt_a01.hl7"), settings).bundle
    assert bundle["resourceType"] == "Bundle"
    assert bundle["type"] == "transaction"
    assert bundle["identifier"]["value"] == "MSG00001"
    assert bundle["timestamp"] == "2026-11-05T08:30:00+05:30"
    types = [e["resource"]["resourceType"] for e in bundle["entry"]]
    assert types == ["Patient", "Practitioner", "Encounter", "AllergyIntolerance", "Condition", "Provenance"]


def test_a01_patient(sample, settings, of_type):
    bundle = convert(sample("adt_a01.hl7"), settings).bundle
    (patient,) = of_type(bundle, "Patient")
    mrn, member = patient["identifier"]
    assert mrn["system"] == "http://hospital.example.org/identifiers/cityhosp/mr"
    assert mrn["value"] == "MRN100234"
    assert member["type"]["coding"][0]["code"] == "MB"
    assert patient["name"] == [{"use": "official", "family": "Sharma", "given": ["Priya", "K"], "prefix": ["Ms."]}]
    assert patient["gender"] == "female"
    assert patient["birthDate"] == "1990-02-14"
    assert patient["address"][0]["city"] == "Bengaluru"
    assert [t["system"] for t in patient["telecom"]] == ["phone", "email", "phone"]
    assert patient["telecom"][2]["use"] == "work"
    assert patient["maritalStatus"]["coding"][0]["code"] == "M"
    request = bundle["entry"][0]["request"]
    assert request == {"method": "PUT", "url": f"Patient?identifier={mrn['system']}|MRN100234"}


def test_a01_encounter(sample, settings, of_type):
    bundle = convert(sample("adt_a01.hl7"), settings).bundle
    (encounter,) = of_type(bundle, "Encounter")
    (practitioner,) = of_type(bundle, "Practitioner")  # attending == admitting, so only one
    patient_url = bundle["entry"][0]["fullUrl"]
    assert encounter["status"] == "in-progress"
    assert encounter["class"]["code"] == "IMP"
    assert encounter["identifier"][0]["value"] == "V20261105-01"
    assert encounter["subject"] == {"reference": patient_url}
    assert encounter["period"] == {"start": "2026-11-05T08:15:00+05:30"}
    assert encounter["location"][0]["location"]["display"] == "CARDIO 0412 A"
    assert encounter["hospitalization"]["admitSource"]["coding"][0]["code"] == "7"
    assert [p["type"][0]["coding"][0]["code"] for p in encounter["participant"]] == ["ATND", "ADM"]
    assert practitioner["name"] == [{"family": "Rao", "given": ["Anil"], "prefix": ["Dr."], "suffix": ["MD"]}]
    assert practitioner["identifier"][0]["system"].endswith("/cityhosp/practitioner")


def test_a01_allergy_and_condition(sample, settings, of_type):
    bundle = convert(sample("adt_a01.hl7"), settings).bundle
    (allergy,) = of_type(bundle, "AllergyIntolerance")
    assert allergy["code"]["coding"][0]["system"] == "http://www.nlm.nih.gov/research/umls/rxnorm"
    assert allergy["category"] == ["medication"]
    assert allergy["criticality"] == "high"
    assert allergy["reaction"][0]["severity"] == "severe"
    assert [m["text"] for m in allergy["reaction"][0]["manifestation"]] == ["Hives", "Shortness of breath"]

    (condition,) = of_type(bundle, "Condition")
    (encounter_entry,) = [e for e in bundle["entry"] if e["resource"]["resourceType"] == "Encounter"]
    assert condition["code"]["coding"][0] == {
        "system": "http://hl7.org/fhir/sid/icd-10",
        "code": "I21.4",
        "display": "Non-ST elevation (NSTEMI) myocardial infarction",
    }
    assert condition["verificationStatus"]["coding"][0]["code"] == "provisional"
    assert condition["encounter"] == {"reference": encounter_entry["fullUrl"]}


def test_every_reference_resolves_inside_the_bundle(sample, settings):
    for name in ("adt_a01.hl7", "adt_a03.hl7", "oru_r01.hl7"):
        bundle = convert(sample(name), settings).bundle
        full_urls = {e["fullUrl"] for e in bundle["entry"]}
        refs = list(all_references(bundle))
        assert refs
        assert set(refs) <= full_urls, name


def test_provenance_points_at_every_resource(sample, settings, of_type):
    bundle = convert(sample("adt_a01.hl7"), settings).bundle
    (provenance,) = of_type(bundle, "Provenance")
    others = {e["fullUrl"] for e in bundle["entry"] if e["resource"]["resourceType"] != "Provenance"}
    assert {t["reference"] for t in provenance["target"]} == others
    assert provenance["entity"][0]["what"]["identifier"]["value"] == "MSG00001"
    assert provenance["agent"][0]["who"]["display"] == "EPIC/CITYHOSP"


def test_conversion_is_deterministic(sample, settings):
    first = convert(sample("adt_a01.hl7"), settings).bundle
    second = convert(sample("adt_a01.hl7"), settings).bundle
    assert [e["fullUrl"] for e in first["entry"]] == [e["fullUrl"] for e in second["entry"]]


def test_a03_discharge(sample, settings, of_type):
    bundle = convert(sample("adt_a03.hl7"), settings).bundle
    (encounter,) = of_type(bundle, "Encounter")
    assert encounter["status"] == "finished"
    assert encounter["period"]["end"] == "2026-11-09T11:30:00+05:30"
    assert encounter["hospitalization"]["dischargeDisposition"]["coding"][0]["code"] == "01"
    (condition,) = of_type(bundle, "Condition")
    assert condition["verificationStatus"]["coding"][0]["code"] == "confirmed"


def test_same_patient_maps_to_same_conditional_update(sample, settings):
    a01 = convert(sample("adt_a01.hl7"), settings).bundle
    a03 = convert(sample("adt_a03.hl7"), settings).bundle
    assert a01["entry"][0]["request"] == a03["entry"][0]["request"]
    assert a01["entry"][0]["fullUrl"] == a03["entry"][0]["fullUrl"]


@pytest.mark.parametrize(("trigger", "pv1", "status"), [
    ("A08", "PV1|1|O", "in-progress"),
    ("A08", "PV1|1|O" + "|" * 43 + "20261105", "finished"),
    ("A04", "PV1|1|O", "arrived"),
    ("A11", "PV1|1|I", "cancelled"),
])
def test_encounter_status_by_trigger(settings, of_type, trigger, pv1, status):
    text = "\r".join([msh(f"ADT^{trigger}"), "PID|1||X1^^^H^MR", pv1])
    (encounter,) = of_type(convert(text, settings).bundle, "Encounter")
    assert encounter["status"] == status


def test_a31_updates_patient_only(settings, of_type):
    text = "\r".join([msh("ADT^A31"), "PID|1||X1^^^H^MR||Doe^Jane", "PV1|1|N"])
    bundle = convert(text, settings).bundle
    assert of_type(bundle, "Encounter") == []
    assert of_type(bundle, "Patient")[0]["name"][0]["family"] == "Doe"


def test_unknown_patient_class_is_null_flavor(settings, of_type):
    text = "\r".join([msh("ADT^A01"), "PID|1||X1", "PV1|1|Z"])
    (encounter,) = of_type(convert(text, settings).bundle, "Encounter")
    assert encounter["class"]["code"] == "UNK"


def test_deceased_patient(settings, of_type):
    pid = "PID|1||X1" + "|" * 26 + "20261101120000|Y"
    (patient,) = of_type(convert("\r".join([msh("ADT^A08"), pid]), settings).bundle, "Patient")
    assert patient["deceasedDateTime"] == "2026-11-01T12:00:00+05:30"


def test_missing_patient_identifier_is_a_mapping_error(settings):
    with pytest.raises(MappingError, match="PID-3"):
        convert("\r".join([msh("ADT^A01"), "PID|1||", "PV1|1|I"]), settings)


def test_missing_pid_is_a_mapping_error(settings):
    with pytest.raises(MappingError, match="PID"):
        convert(msh("ADT^A01"), settings)


@pytest.mark.parametrize(("message_type", "code"), [("SIU^S12", "200"), ("ADT^A40", "201")])
def test_unsupported_messages(settings, message_type, code):
    with pytest.raises(UnsupportedMessageError) as info:
        convert("\r".join([msh(message_type), "PID|1||X1"]), settings)
    assert info.value.error_code == code
    assert info.value.hl7_message.control_id == "CTRL1"
