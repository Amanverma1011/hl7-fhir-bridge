import pytest

from hl7fhir import datatypes as dt


def comps(text: str) -> list[list[str]]:
    return [c.split("&") for c in text.split("^")]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026", "2026"),
        ("202611", "2026-11"),
        ("20261105", "2026-11-05"),
        ("202611050830", "2026-11-05T08:30:00+00:00"),
        ("20261105083045+0530", "2026-11-05T08:30:45+05:30"),
        ("20261105083045.123-0500", "2026-11-05T08:30:45.123-05:00"),
        ("20261345", None),
        ("20260230", None),
        ("2026110", None),
        ("abc", None),
        ("", None),
    ],
)
def test_datetime(raw, expected):
    assert dt.to_fhir_datetime(raw) == expected


def test_datetime_uses_default_timezone_only_when_missing():
    assert dt.to_fhir_datetime("202611050830", "+05:30") == "2026-11-05T08:30:00+05:30"
    assert dt.to_fhir_datetime("202611050830-0400", "+05:30") == "2026-11-05T08:30:00-04:00"


def test_date_and_instant():
    assert dt.to_fhir_date("19900214123000") == "1990-02-14"
    assert dt.to_fhir_instant("20261105") is None
    assert dt.to_fhir_instant("20261105101000+0530") == "2026-11-05T10:10:00+05:30"


@pytest.mark.parametrize(("raw", "expected"), [("245", 245), ("2.31", 2.31), ("0.00", 0.0), ("-4", -4),
                                               ("abc", None), ("", None), ("NaN", None)])
def test_parse_number(raw, expected):
    assert dt.parse_number(raw) == expected


def test_codeable_concept_with_alternate_coding():
    cc = dt.codeable_concept(comps("2093-3^Cholesterol^LN^CHOL^Chol local^L"))
    assert cc["coding"][0] == {"system": "http://loinc.org", "code": "2093-3", "display": "Cholesterol"}
    assert cc["coding"][1] == {"code": "CHOL", "display": "Chol local"}
    assert cc["text"] == "Cholesterol"
    assert dt.codeable_concept(comps("")) is None


def test_code_system_lookup():
    assert dt.code_system_uri("ln") == "http://loinc.org"
    assert dt.code_system_uri("HL70203") == "http://terminology.hl7.org/CodeSystem/v2-0203"
    assert dt.code_system_uri("99LOCAL") is None


def test_identifier_systems():
    base = "http://h.example/ids"
    assert dt.identifier(comps("A1^^^CITY HOSP^MR"), base)["system"] == "http://h.example/ids/city-hosp/mr"
    assert dt.identifier(comps("A1^^^HOSP&2.16.840.1&ISO^MR"), base)["system"] == "urn:oid:2.16.840.1"
    assert dt.identifier(comps("A1"), base, "patient")["system"] == "http://h.example/ids/patient"
    assert dt.identifier(comps("^^^HOSP^MR"), base) is None


def test_human_name():
    name = dt.human_name(comps("Sharma^Priya^K^Jr^Ms.^MD^L"))
    assert name == {"use": "official", "family": "Sharma", "given": ["Priya", "K"], "prefix": ["Ms."],
                    "suffix": ["Jr", "MD"]}
    assert dt.human_name(comps("")) is None


def test_address():
    addr = dt.address(comps("12 MG Road^Apt 4B^Bengaluru^KA^560001^IND^H"))
    assert addr == {"use": "home", "line": ["12 MG Road", "Apt 4B"], "city": "Bengaluru", "state": "KA",
                    "postalCode": "560001", "country": "IND"}


def test_telecom():
    assert dt.telecom(comps("^PRN^PH^^91^80^41234567")) == {"system": "phone", "value": "+91 80 41234567",
                                                             "use": "home"}
    assert dt.telecom(comps("^NET^Internet^a@b.org")) == {"system": "email", "value": "a@b.org"}
    assert dt.telecom(comps("555-1234^^CP"))["use"] == "mobile"
    assert dt.telecom(comps("555-1234"), default_use="work") == {"system": "phone", "value": "555-1234",
                                                                  "use": "work"}
    assert dt.telecom(comps("")) is None
