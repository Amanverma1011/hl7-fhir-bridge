import pytest

from hl7fhir.errors import HL7ParseError
from hl7fhir.parser import Delimiters, escape, parse, unescape

MSG = (
    "MSH|^~\\&|SENDER|FAC|RECV|FAC|20261105083000||ADT^A01^ADT_A01|CTRL1|P|2.5.1\r"
    "PID|1||A1^^^HOSP&1.2.3&ISO^MR~B2^^^INS^MB||O\\T\\Brien^Sean||\"\"|M"
)


def test_msh_numbering_follows_the_standard():
    msg = parse(MSG)
    assert msg.msh.get(1) == "|"
    assert msg.msh.get(2) == "^~\\&"
    assert msg.msh.get(3) == "SENDER"
    assert msg.message_type == "ADT^A01"
    assert msg.control_id == "CTRL1"
    assert msg.version == "2.5.1"


def test_components_repetitions_and_subcomponents():
    pid = parse(MSG).first("PID")
    assert pid.get(3) == "A1"
    assert pid.get(3, 4, 2) == "1.2.3"
    assert pid.get(3, 4, 3) == "ISO"
    assert pid.get(3, 1, repetition=2) == "B2"
    assert pid.get(3, 5, repetition=2) == "MB"
    assert len(pid.repetitions(3)) == 2
    assert pid.get(99) == ""


def test_escaped_delimiters_do_not_split_values():
    pid = parse(MSG).first("PID")
    assert pid.get(5, 1) == "O&Brien"
    assert pid.get(5, 2) == "Sean"


def test_hl7_null_reads_as_empty():
    assert parse(MSG).first("PID").get(7) == ""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("A\\F\\B", "A|B"),
        ("A\\S\\B", "A^B"),
        ("A\\R\\B", "A~B"),
        ("A\\E\\B", "A\\B"),
        ("\\X41\\", "A"),
        ("\\H\\bold\\N\\", "bold"),
        ("line1\\.br\\line2", "line1\nline2"),
        ("unterminated\\F", "unterminated\\F"),
    ],
)
def test_unescape(raw, expected):
    assert unescape(raw, Delimiters()) == expected


def test_escape_round_trip():
    d = Delimiters()
    text = "a|b^c~d&e\\f"
    assert unescape(escape(text, d), d) == text


@pytest.mark.parametrize("sep", ["\r", "\n", "\r\n"])
def test_any_segment_terminator(sep):
    msg = parse(MSG.replace("\r", sep))
    assert [s.name for s in msg.segments] == ["MSH", "PID"]


def test_strips_mllp_framing():
    assert parse("\x0b" + MSG + "\x1c\r").control_id == "CTRL1"


def test_custom_delimiters():
    msg = parse("MSH#*!@%#APP#FAC#####ORU*R01#ID9#P#2.5\rPID#1##X1*!X2")
    assert msg.message_type == "ORU^R01"
    assert msg.first("PID").get(3, 1, repetition=2) == "X2"


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("PID|1||A1", "must start with an MSH"),
        ("MSH|^~", "too short"),
        ("MSH|^^\\&|A", "Invalid delimiter"),
        ("MSH|^~\\&|A|B|C|D|2026||ADT^A01||P|2.5", "control ID"),
        ("MSH|^~\\&|A|B|C|D|2026|||X|P|2.5", "message type"),
        (MSG + "\rpid|lowercase", "Invalid segment"),
        (MSG + "\r" + MSG.split("\r")[0], "Batch"),
    ],
)
def test_rejects_malformed_messages(text, fragment):
    with pytest.raises(HL7ParseError, match=fragment):
        parse(text)
