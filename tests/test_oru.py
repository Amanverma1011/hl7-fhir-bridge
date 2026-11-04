import pytest
from conftest import msh

from hl7fhir.errors import MappingError
from hl7fhir.service import convert


def by_code(observations):
    return {o["code"]["coding"][0]["code"]: o for o in observations}


def test_reports_group_their_observations(sample, settings, of_type):
    bundle = convert(sample("oru_r01.hl7"), settings).bundle
    lipid, troponin = of_type(bundle, "DiagnosticReport")
    assert lipid["status"] == "final"
    assert lipid["code"]["coding"][0]["code"] == "57698-3"
    assert lipid["identifier"][0]["value"] == "FIL9981"
    assert lipid["category"][0]["coding"][0]["code"] == "CH"
    assert lipid["effectiveDateTime"] == "2026-11-05T09:00:00+05:30"
    assert lipid["issued"] == "2026-11-05T10:10:00+05:30"
    assert lipid["conclusion"] == "Fasting sample (12 hours)."
    assert len(lipid["result"]) == 4
    assert len(troponin["result"]) == 1

    urls = {e["resource"]["identifier"][0]["value"]: e["fullUrl"]
            for e in bundle["entry"] if e["resource"]["resourceType"] == "Observation"}
    assert troponin["result"] == [{"reference": urls["FIL9982-10839-9"]}]


def test_numeric_results(sample, settings, of_type):
    obs = by_code(of_type(convert(sample("oru_r01.hl7"), settings).bundle, "Observation"))
    chol = obs["2093-3"]
    assert chol["status"] == "final"
    assert chol["valueQuantity"] == {"value": 245, "unit": "mg/dL", "system": "http://unitsofmeasure.org",
                                     "code": "mg/dL"}
    assert chol["interpretation"][0]["coding"][0]["code"] == "H"
    assert chol["referenceRange"][0]["high"]["value"] == 200
    assert chol["category"][0]["coding"][0]["code"] == "laboratory"

    assert obs["2085-9"]["referenceRange"][0]["low"]["value"] == 40
    assert obs["2085-9"]["interpretation"][0]["coding"][0]["display"] == "Low"
    assert obs["2571-8"]["interpretation"][0]["coding"][0]["code"] == "N"

    trop = obs["10839-9"]
    assert trop["valueQuantity"]["value"] == 2.31
    assert trop["interpretation"][0]["coding"][0]["display"] == "Critical high"
    assert trop["referenceRange"][0]["low"]["value"] == 0.0
    assert trop["referenceRange"][0]["high"]["value"] == 0.04
    assert trop["note"] == [{"text": "Critical value phoned to Dr. Rao at 10:12 and read back."}]


def test_observations_link_patient_and_encounter(sample, settings, of_type):
    bundle = convert(sample("oru_r01.hl7"), settings).bundle
    patient_url = bundle["entry"][0]["fullUrl"]
    encounter_url = bundle["entry"][1]["fullUrl"]
    for observation in of_type(bundle, "Observation"):
        assert observation["subject"] == {"reference": patient_url}
        assert observation["encounter"] == {"reference": encounter_url}


def oru(*obx_and_more: str) -> str:
    return "\r".join([msh("ORU^R01"), "PID|1||X1^^^H^MR", "OBR|1||F1|CODE^Panel^LN", *obx_and_more])


@pytest.mark.parametrize(
    ("obx", "key", "expected"),
    [
        ("OBX|1|SN|A^A^LN||<^5|mmol/L^^UCUM", "valueQuantity",
         {"value": 5, "unit": "mmol/L", "system": "http://unitsofmeasure.org", "code": "mmol/L", "comparator": "<"}),
        ("OBX|1|SN|A^A^LN||^1^-^5", "valueRange", {"low": {"value": 1}, "high": {"value": 5}}),
        ("OBX|1|SN|A^A^LN||^1^:^128", "valueRatio", {"numerator": {"value": 1}, "denominator": {"value": 128}}),
        ("OBX|1|ST|A^A^LN||Positive~Repeat advised", "valueString", "Positive\nRepeat advised"),
        ("OBX|1|CWE|A^A^LN||260373001^Detected^SCT", "valueCodeableConcept",
         {"coding": [{"system": "http://snomed.info/sct", "code": "260373001", "display": "Detected"}],
          "text": "Detected"}),
        ("OBX|1|DT|A^A^LN||20261101", "valueDateTime", "2026-11-01"),
        ("OBX|1|TM|A^A^LN||0830", "valueTime", "08:30:00"),
        ("OBX|1|NM|A^A^LN||see note", "valueString", "see note"),
        ("OBX|1|NM|A^A^LN||7.5|g/dL", "valueQuantity", {"value": 7.5, "unit": "g/dL"}),
    ],
)
def test_value_types(settings, of_type, obx, key, expected):
    (observation,) = of_type(convert(oru(obx), settings).bundle, "Observation")
    assert observation[key] == expected


def test_missing_value_gets_data_absent_reason(settings, of_type):
    (observation,) = of_type(convert(oru("OBX|1|NM|A^A^LN||"), settings).bundle, "Observation")
    assert observation["dataAbsentReason"]["coding"][0]["code"] == "unknown"


def test_cancelled_result_has_no_data_absent_reason(settings, of_type):
    (observation,) = of_type(convert(oru("OBX|1|NM|A^A^LN||||||||X"), settings).bundle, "Observation")
    assert observation["status"] == "cancelled"
    assert "dataAbsentReason" not in observation


def test_observation_status_falls_back_to_report_status(settings, of_type):
    text = "\r".join([msh("ORU^R01"), "PID|1||X1", "OBR|1||F1|CODE^Panel^LN" + "|" * 21 + "P", "OBX|1|NM|A^A^LN||1"])
    bundle = convert(text, settings).bundle
    assert of_type(bundle, "DiagnosticReport")[0]["status"] == "preliminary"
    assert of_type(bundle, "Observation")[0]["status"] == "preliminary"


def test_unparseable_reference_range_kept_as_text(settings, of_type):
    (observation,) = of_type(convert(oru("OBX|1|NM|A^A^LN||3||negative"), settings).bundle, "Observation")
    assert observation["referenceRange"] == [{"text": "negative"}]


def test_obx_before_obr_is_rejected(settings):
    text = "\r".join([msh("ORU^R01"), "PID|1||X1", "OBX|1|NM|A^A^LN||1"])
    with pytest.raises(MappingError, match="before any OBR"):
        convert(text, settings)


def test_oru_without_obr_is_rejected(settings):
    with pytest.raises(MappingError, match="OBR"):
        convert("\r".join([msh("ORU^R01"), "PID|1||X1"]), settings)


def test_obx_without_code_is_rejected(settings):
    with pytest.raises(MappingError, match="OBX-3"):
        convert(oru("OBX|1|NM|||5"), settings)
