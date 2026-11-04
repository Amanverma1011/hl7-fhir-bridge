# HL7 v2 → FHIR R4 Bridge

[![CI](https://github.com/Amanverma1011/hl7-fhir-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/Amanverma1011/hl7-fhir-bridge/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![FHIR](https://img.shields.io/badge/FHIR-R4-orange)
![License](https://img.shields.io/badge/license-MIT-green)

**A healthcare integration engine that turns the HL7 v2 messages hospitals send every day into FHIR R4 resources.**

Most hospital systems (EHRs, lab systems, radiology) still exchange data as **HL7 v2** messages over **MLLP**,
a format from the 1980s and 90s. Newer apps, analytics platforms and cloud health services (AWS HealthLake,
Azure Health Data Services, Google Cloud Healthcare API) expect **FHIR**. Integration engines such as Mirth
Connect, Rhapsody and InterSystems sit between the two. This project is a small, readable, tested version of
that layer.

```
 EHR / Lab system ──HL7 v2 over MLLP──▶  hl7-fhir-bridge  ──FHIR transaction──▶  FHIR server
   (ADT, ORU)      ◀──── ACK (AA/AE/AR) ─┘   parse · map · validate             (HAPI, Azure, GCP, AWS)
```

> All sample data in this repository is synthetic. No real patient information is used.

---

## Features

- **MLLP listener**: the TCP protocol hospitals use for HL7 v2. Handles several messages per connection and
  frames split across packets, and sends an HL7 **ACK** for every message.
- **REST API** (FastAPI) with interactive docs at `/docs`, plus `/health`, `/supported` and `/metrics`.
- **CLI** to convert files, run the servers and send test messages.
- **Dependency-free HL7 v2 parser**: custom delimiters, repetitions, components, subcomponents, escape
  sequences (`\F\ \S\ \T\ \R\ \E\ \X..\ \.br\`), HL7 null (`""`) and any line ending.
- **FHIR R4 transaction Bundles** with internal `urn:uuid` references between resources.
- **Safe to replay**: resources with business identifiers use conditional updates
  (`PUT Patient?identifier=system|value`), so re-sending a message updates records instead of duplicating them.
- **Correct ACK behaviour**:
  - `AA`: accepted.
  - `AR`: rejected (bad or unsupported message). The sender should not retry.
  - `AE`: error (missing required data, or the FHIR server is down). The sender should retry.
- **Audit trail**: every bundle includes a `Provenance` resource linking the created resources back to the
  source HL7 message (sender plus MSH-10 control ID).
- **PHI-safe logging**: logs and error messages contain only the message type, control ID and outcome, never
  patient data.
- **Production basics**: env-based config, message size limits, a Docker image running as non-root, a
  docker-compose stack with a HAPI FHIR server, and CI on Python 3.11–3.13.

## Supported messages

| HL7 v2 message | Meaning | FHIR resources produced |
|---|---|---|
| `ADT^A01` | Admit inpatient | Patient, Encounter (`in-progress`), Practitioner, AllergyIntolerance, Condition |
| `ADT^A02` | Transfer | Patient, Encounter (new location) |
| `ADT^A03` | Discharge | Patient, Encounter (`finished`, end time, discharge disposition), Condition |
| `ADT^A04` | Register outpatient | Patient, Encounter (`arrived`) |
| `ADT^A05` | Pre-admit | Patient, Encounter (`planned`) |
| `ADT^A06` `A07` `A08` `A13` | Change class / update / cancel discharge | Patient, Encounter |
| `ADT^A11` | Cancel admit | Patient, Encounter (`cancelled`) |
| `ADT^A28` `A31` | Add / update person | Patient |
| `ORU^R01` | Lab results | Patient, Encounter, DiagnosticReport, Observation |

Every bundle also contains a **Provenance** resource.

### Field mapping (main fields)

| HL7 v2 | FHIR R4 |
|---|---|
| PID-3 patient identifiers (CX) | `Patient.identifier` (OID → `urn:oid:`, otherwise a stable system URI) |
| PID-5 name, PID-7 birth date, PID-8 sex | `Patient.name`, `birthDate`, `gender` |
| PID-11 address, PID-13/14 phone & email | `Patient.address`, `Patient.telecom` |
| PID-16 marital status, PID-29/30 death | `maritalStatus`, `deceasedDateTime` / `deceasedBoolean` |
| PV1-2 patient class | `Encounter.class` (v3 ActCode: IMP, EMER, AMB, PRENC) |
| PV1-3 location, PV1-19 visit number | `Encounter.location`, `Encounter.identifier` |
| PV1-7/8/9/17 attending, referring, consulting, admitting | `Practitioner` + `Encounter.participant` |
| PV1-14 admit source, PV1-36 discharge disposition | `Encounter.hospitalization` |
| PV1-44/45 admit / discharge time | `Encounter.period` |
| AL1 allergy (type, code, severity, reaction) | `AllergyIntolerance` (category, criticality, reaction) |
| DG1 diagnosis (ICD-10) | `Condition` (encounter-diagnosis, provisional / confirmed) |
| OBR order, OBR-25 result status | `DiagnosticReport` (status, category, issued) |
| OBX-2/5 value (NM, SN, ST/TX/FT, CE/CWE, DT/TS, TM) | `valueQuantity`, `valueRange`, `valueRatio`, `valueString`, `valueCodeableConcept`, `valueDateTime`, `valueTime` |
| OBX-6 units (UCUM), OBX-7 reference range, OBX-8 flags | `Quantity.unit/code`, `referenceRange`, `interpretation` |
| NTE after OBR / after OBX | `DiagnosticReport.conclusion` / `Observation.note` |

Coding systems are mapped to FHIR URIs: `LN` → LOINC, `SCT` → SNOMED CT, `I10` → ICD-10, `RXNORM`, `NDC`,
`CPT`, `CVX`, `UCUM` and `HL7nnnn` → `http://terminology.hl7.org/CodeSystem/v2-nnnn`.

---

## Quick start

Requires **Python 3.11+**.

```bash
git clone https://github.com/Amanverma1011/hl7-fhir-bridge.git
cd hl7-fhir-bridge
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

### 1. Convert a file

```bash
hl7fhir convert samples/oru_r01.hl7
```

<details>
<summary>Example output (one Observation from the bundle, slightly trimmed)</summary>

```json
{
  "resourceType": "Observation",
  "identifier": [{ "system": "http://hospital.example.org/identifiers/observation", "value": "FIL9982-10839-9" }],
  "status": "final",
  "category": [{ "coding": [{ "system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "laboratory" }] }],
  "code": { "coding": [{ "system": "http://loinc.org", "code": "10839-9", "display": "Troponin I.cardiac [Mass/volume] in Serum or Plasma" }] },
  "subject": { "reference": "urn:uuid:429b10b3-d954-564d-8122-1f047db305a0" },
  "effectiveDateTime": "2026-11-05T09:00:00+05:30",
  "valueQuantity": { "value": 2.31, "unit": "ng/mL", "system": "http://unitsofmeasure.org", "code": "ng/mL" },
  "interpretation": [{ "coding": [{ "system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation", "code": "HH", "display": "Critical high" }] }],
  "note": [{ "text": "Critical value phoned to Dr. Rao at 10:12 and read back." }],
  "referenceRange": [{ "low": { "value": 0.0, "unit": "ng/mL" }, "high": { "value": 0.04, "unit": "ng/mL" }, "text": "0.00-0.04" }]
}
```
</details>

### 2. Run the MLLP listener and send a message

```bash
hl7fhir serve-mllp                         # terminal 1: listens on 127.0.0.1:2575
hl7fhir send samples/adt_a01.hl7           # terminal 2
```

```
MSH|^~\&|HL7FHIRBRIDGE|CITYHOSP|EPIC|CITYHOSP|20261105...||ACK^A01^ACK|90F49F5F2AC1427ABA6F|P|2.5.1
MSA|AA|MSG00001|Message accepted
```

### 3. Run the REST API

```bash
hl7fhir serve-api                          # http://127.0.0.1:8000/docs
curl -X POST --data-binary @samples/adt_a01.hl7 -H "Content-Type: text/plain" http://127.0.0.1:8000/convert
```

| Endpoint | Description |
|---|---|
| `POST /convert` | Body: raw HL7 text. Returns a FHIR `Bundle` (`application/fhir+json`). |
| `POST /convert?forward=true` | Also posts the bundle to `FHIR_SERVER_URL` and returns the server's transaction-response. |
| `GET /health` | Liveness and version. |
| `GET /supported` | Supported message types and trigger events. |
| `GET /metrics` | Counts of converted, rejected and forwarded messages. |

Errors are returned as FHIR `OperationOutcome` resources: `422` invalid or unsupported message, `413` too
large, `400` forwarding not configured, `502` FHIR server error.

### 4. Full stack with a real FHIR server (Docker)

```bash
docker compose up --build
hl7fhir send samples/adt_a01.hl7           # goes MLLP → bridge → HAPI FHIR
curl "http://localhost:8080/fhir/Patient?identifier=MRN100234"
```

This starts a [HAPI FHIR](https://hapifhir.io/) server (port 8080), the bridge API (port 8000) and the MLLP
listener (port 2575).

---

## Configuration

Everything is set with environment variables. Copy [.env.example](.env.example) to `.env`. No secrets are
needed to run locally.

| Variable | Default | Purpose |
|---|---|---|
| `IDENTIFIER_SYSTEM_BASE` | `http://hospital.example.org/identifiers` | Base URI for identifier systems the sender doesn't give as an OID/URL |
| `DEFAULT_TIMEZONE` | `+00:00` | Offset for HL7 times without a timezone (FHIR requires one) |
| `FHIR_SERVER_URL` | (empty) | FHIR R4 base URL to forward bundles to. Empty means convert only |
| `FHIR_AUTH_TOKEN` | (empty) | Bearer token for the FHIR server |
| `FHIR_TIMEOUT_SECONDS` | `15` | Timeout for FHIR requests |
| `MLLP_HOST` / `MLLP_PORT` | `127.0.0.1` / `2575` | MLLP listener address |
| `MAX_MESSAGE_BYTES` | `1048576` | Size limit for one message |
| `RECEIVING_APPLICATION` / `RECEIVING_FACILITY` | `HL7FHIRBRIDGE` / (empty) | MSH-3/4 of ACKs when the inbound message names no receiver |
| `LOG_LEVEL` | `INFO` | Logging level |

---

## Project structure

```
hl7fhir/
├── parser.py       HL7 v2 parser: delimiters, escapes, segments, field access
├── datatypes.py    HL7 data types → FHIR (TS, CE/CWE, CX, XPN, XAD, XTN, NM)
├── mappers.py      ADT / ORU → FHIR resources and the transaction Bundle
├── service.py      Pipeline: parse → map → forward → ACK
├── ack.py          HL7 ACK builder (AA / AE / AR with ERR segment)
├── mllp.py         Async MLLP server and client
├── fhir_client.py  Async FHIR client (bearer auth, error handling)
├── api.py          FastAPI app
├── config.py       Settings from environment variables
└── __main__.py     CLI
samples/            Synthetic ADT^A01, ADT^A03 and ORU^R01 messages
tests/              109 tests: parser, data types, ADT, ORU, ACK, API, MLLP
```

## Testing

```bash
pytest          # 109 tests
ruff check .    # lint
```

The tests cover delimiter and escape handling, timestamp precision and time zones, every supported value
type, encounter status per trigger event, checks that every reference resolves inside the bundle,
deterministic output, ACK structure, FHIR forwarding success and failure (using a mocked server), and a real
MLLP socket test with split frames and several messages per connection.

## Design decisions

- **Conditional updates over blind creates.** Hospitals often resend HL7 messages. Matching on business
  identifiers (MRN, visit number, lab order number) makes every message safe to process twice.
- **Deterministic `fullUrl`s.** UUIDv5 of the identifier gives the same IDs for the same input, which makes
  bundles easy to diff and test.
- **Never invent data.** Units are tagged as UCUM only when the sender says so. Unknown codes keep their text
  but get no made-up system. Values that can't be parsed are kept as strings instead of being dropped.
- **ACK codes tell the sender what to do next.** Structurally bad messages get `AR` so they don't loop
  forever. Temporary failures get `AE` so the sender retries.

## Limitations and roadmap

- [ ] Batch files (FHS/BHS) and more message types (`SIU` scheduling, `ORM`/`OML` orders, `MDM` documents, `VXU` immunizations)
- [ ] Location and Organization resources (currently `Encounter.location` is display text only)
- [ ] A dead-letter queue and retry policy for messages that fail to forward
- [ ] TLS for MLLP and SMART Backend Services (OAuth2) auth for cloud FHIR servers
- [ ] Validation against the FHIR R4 base profiles in CI

## Standards referenced

- [HL7 v2.5.1](https://www.hl7.org/implement/standards/product_brief.cfm?product_id=144) messaging standard
- [HL7 FHIR R4](https://hl7.org/fhir/R4/)
- [HL7 v2-to-FHIR mapping guide](https://build.fhir.org/ig/HL7/v2-to-fhir/)
- [MLLP transport](https://www.hl7.org/implement/standards/product_brief.cfm?product_id=55)

## License

[MIT](LICENSE) © 2026 Aman Verma ([@Amanverma1011](https://github.com/Amanverma1011))
