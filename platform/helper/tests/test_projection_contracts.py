from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from course_helper.domain.projection import ProjectionCommand


FIXTURE = (
    Path(__file__).parents[2]
    / "contracts"
    / "projection"
    / "v1"
    / "fixtures"
    / "detect-displays.json"
)


def test_detect_display_fixture_is_strict_and_round_trips() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    command = ProjectionCommand.model_validate(payload)

    assert command.command == "detect_displays"
    assert command.model_dump(mode="json", by_alias=True) == payload

    for unsafe_field in (
        "sourcePath",
        "url",
        "token",
        "hwnd",
        "executablePath",
        "courseBody",
    ):
        with pytest.raises(ValidationError):
            ProjectionCommand.model_validate({**payload, unsafe_field: "unsafe"})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schemaVersion", 2),
        ("commandId", "not-a-uuid"),
        ("expectedGeneration", -1),
        ("command", "run_shell"),
    ],
)
def test_projection_command_rejects_invalid_envelope_fields(
    field: str,
    value: object,
) -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    with pytest.raises(ValidationError):
        ProjectionCommand.model_validate({**payload, field: value})
