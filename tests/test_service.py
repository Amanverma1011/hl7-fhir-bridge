import asyncio
import json

import httpx
from conftest import msh

from hl7fhir.ack import build_ack
from hl7fhir.fhir_client import FhirClient
from hl7fhir.parser import parse
from hl7fhir.service import process_message


def segments(ack: str) -> dict[str, list[str]]:
    return {line[:3]: line.split("|") for line in ack.split("\r")}


def run(text, settings, client=None):
    return asyncio.run(process_message(text, settings, client))


def test_ack_swaps_sender_and_receiver(sample, settings):
    message = parse(sample("adt_a01.hl7"))
    ack = segments(build_ack(message, "AA", settings, "ok"))
    assert ack["MSH"][2:6] == ["HL7FHIRBRIDGE", "CITYHOSP", "EPIC", "CITYHOSP"]
    assert ack["MSH"][8] == "ACK^A01^ACK"
    assert ack["MSH"][11] == "2.5.1"
    assert ack["MSA"] == ["MSA", "AA", "MSG00001", "ok"]
    assert "ERR" not in ack


def test_error_ack_has_err_segment_and_escapes_text(sample, settings):
    message = parse(sample("adt_a01.hl7"))
    ack = build_ack(message, "AE", settings, "bad|value", "101")
    err = segments(ack)["ERR"]
    assert err[3] == "101^Required field missing^HL70357"
    assert err[4] == "E"
    assert "bad\\F\\value" in ack


def test_accepted_message(sample, settings):
    outcome = run(sample("adt_a01.hl7"), settings)
    assert outcome.ack_code == "AA"
    assert outcome.bundle["type"] == "transaction"
    assert outcome.control_id == "MSG00001"


def test_garbage_is_rejected_with_ar(settings):
    outcome = run("hello world", settings)
    assert outcome.ack_code == "AR"
    assert segments(outcome.ack)["MSA"][1] == "AR"
    assert outcome.bundle is None


def test_unsupported_type_is_rejected_with_ar(settings):
    outcome = run("\r".join([msh("SIU^S12"), "PID|1||X1"]), settings)
    assert outcome.ack_code == "AR"
    assert segments(outcome.ack)["MSA"][2] == "CTRL1"
    assert segments(outcome.ack)["ERR"][3].startswith("200^")


def test_mapping_error_returns_ae(settings):
    outcome = run("\r".join([msh("ADT^A01"), "PID|1||"]), settings)
    assert outcome.ack_code == "AE"
    assert segments(outcome.ack)["ERR"][3].startswith("101^")


def mock_client(handler):
    return FhirClient("http://fhir.test/fhir", token="secret", transport=httpx.MockTransport(handler))


def test_forwards_transaction_to_fhir_server(sample, settings):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"resourceType": "Bundle", "type": "transaction-response"})

    outcome = run(sample("oru_r01.hl7"), settings, mock_client(handler))
    assert outcome.ack_code == "AA"
    assert seen["url"] == "http://fhir.test/fhir/"
    assert seen["headers"]["content-type"] == "application/fhir+json"
    assert seen["headers"]["authorization"] == "Bearer secret"
    assert seen["body"]["type"] == "transaction"


def test_fhir_server_error_returns_ae_so_sender_retries(sample, settings):
    outcome = run(sample("adt_a01.hl7"), settings, mock_client(lambda r: httpx.Response(503)))
    assert outcome.ack_code == "AE"
    assert "HTTP 503" in outcome.error
    assert outcome.bundle is not None


def test_fhir_server_unreachable_returns_ae(sample, settings):
    def handler(request):
        raise httpx.ConnectError("refused")

    outcome = run(sample("adt_a01.hl7"), settings, mock_client(handler))
    assert outcome.ack_code == "AE"
    assert "unreachable" in outcome.error
