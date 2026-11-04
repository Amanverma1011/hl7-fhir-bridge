import httpx
from fastapi.testclient import TestClient

from hl7fhir.api import create_app
from hl7fhir.config import Settings
from hl7fhir.fhir_client import FhirClient


def client_for(settings, fhir_client=None):
    return TestClient(create_app(settings, fhir_client))


def test_health_and_supported(settings):
    with client_for(settings) as client:
        assert client.get("/health").json()["status"] == "ok"
        supported = client.get("/supported").json()
        assert "A01" in supported["ADT"]
        assert supported["ORU"] == ["R01"]


def test_convert_returns_fhir_bundle(sample, settings):
    with client_for(settings) as client:
        response = client.post("/convert", content=sample("adt_a01.hl7"), headers={"content-type": "text/plain"})
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/fhir+json"
        assert response.json()["resourceType"] == "Bundle"
        assert client.get("/metrics").json() == {"converted.ADT_A01": 1}


def test_invalid_message_returns_operation_outcome(settings):
    with client_for(settings) as client:
        response = client.post("/convert", content="not hl7")
        assert response.status_code == 422
        body = response.json()
        assert body["resourceType"] == "OperationOutcome"
        assert body["issue"][0]["code"] == "structure"


def test_oversized_message_is_rejected(sample):
    with client_for(Settings(max_message_bytes=100)) as client:
        assert client.post("/convert", content=sample("adt_a01.hl7")).status_code == 413


def test_forward_without_server_configured(sample, settings):
    with client_for(settings) as client:
        response = client.post("/convert?forward=true", content=sample("adt_a01.hl7"))
        assert response.status_code == 400


def test_forward_returns_server_response(sample, settings):
    transaction_response = {"resourceType": "Bundle", "type": "transaction-response", "entry": []}
    fhir = FhirClient("http://fhir.test/fhir",
                      transport=httpx.MockTransport(lambda r: httpx.Response(200, json=transaction_response)))
    with client_for(settings, fhir) as client:
        response = client.post("/convert?forward=true", content=sample("oru_r01.hl7"))
        assert response.status_code == 200
        assert response.json() == transaction_response


def test_forward_failure_is_502(sample, settings):
    fhir = FhirClient("http://fhir.test/fhir", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with client_for(settings, fhir) as client:
        response = client.post("/convert?forward=true", content=sample("oru_r01.hl7"))
        assert response.status_code == 502
        assert response.json()["issue"][0]["code"] == "transient"
