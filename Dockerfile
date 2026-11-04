FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY hl7fhir ./hl7fhir
RUN pip install .

# Run as an unprivileged user
RUN useradd --create-home --uid 10001 bridge
USER bridge

EXPOSE 8000 2575
CMD ["hl7fhir", "serve-api", "--host", "0.0.0.0", "--port", "8000"]
