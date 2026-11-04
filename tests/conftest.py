from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from hl7fhir.config import Settings

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


@pytest.fixture
def settings() -> Settings:
    return Settings(default_timezone="+05:30")


@pytest.fixture
def sample() -> Callable[[str], str]:
    return lambda name: (SAMPLES / name).read_text(encoding="utf-8")


@pytest.fixture
def of_type() -> Callable[[dict[str, Any], str], list[dict[str, Any]]]:
    def pick(bundle: dict[str, Any], resource_type: str) -> list[dict[str, Any]]:
        return [e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == resource_type]

    return pick


def msh(message_type: str, control_id: str = "CTRL1") -> str:
    return f"MSH|^~\\&|SENDER|FAC|RECV|FAC|20261105083000||{message_type}|{control_id}|P|2.5.1"
