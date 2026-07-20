"""Strict versioned contracts for native projection commands and evidence."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


def _lower_camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


class _ProjectionModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_lower_camel,
        extra="forbid",
        validate_default=True,
    )


ProjectionCommandName = Literal[
    "detect_displays",
    "open_projection_session",
    "assign_projection_window",
    "enter_projection_fullscreen",
    "verify_projection_assignment",
    "close_projection_session",
]

ProjectionStatus = Literal[
    "undetected",
    "candidate",
    "assigned",
    "fullscreen",
    "syncing",
    "witness_pending",
    "certified",
    "invalidated",
    "closed",
]

ProjectionRole = Literal["stage", "presenter"]
ProjectionEventType = Literal[
    "topology_detected",
    "session_opened",
    "window_assigned",
    "fullscreen_entered",
    "frame_committed",
    "assignment_verified",
    "witness_started",
    "witness_confirmed",
    "session_certified",
    "session_invalidated",
    "session_closed",
    "host_error",
]


class ProjectionCommand(_ProjectionModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    command_id: UUID = Field(alias="commandId")
    command: ProjectionCommandName
    session_id: UUID | None = Field(default=None, alias="sessionId")
    expected_generation: int = Field(ge=0, le=2_147_483_647, alias="expectedGeneration")
    payload: dict[str, JsonValue] = Field(max_length=32)


class ProjectionRectangle(_ProjectionModel):
    x: int = Field(ge=-1_000_000, le=1_000_000)
    y: int = Field(ge=-1_000_000, le=1_000_000)
    width: int = Field(ge=1, le=100_000)
    height: int = Field(ge=1, le=100_000)


class ProjectionDisplay(_ProjectionModel):
    display_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="displayId")
    bounds: ProjectionRectangle
    work_area: ProjectionRectangle = Field(alias="workArea")
    is_primary: bool = Field(alias="isPrimary")
    is_internal: bool = Field(alias="isInternal")
    scale_percent: int = Field(ge=50, le=500, alias="scalePercent")
    refresh_rate_milli_hertz: int = Field(
        ge=1_000,
        le=1_000_000,
        alias="refreshRateMilliHertz",
    )


class DisplayTopology(_ProjectionModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    topology_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="topologyId")
    captured_at: datetime = Field(alias="capturedAt")
    session_kind: Literal["interactive_local", "remote", "unknown"] = Field(
        alias="sessionKind"
    )
    mode: Literal["single", "extended", "duplicate", "unknown"]
    displays: tuple[ProjectionDisplay, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def unique_displays(self) -> DisplayTopology:
        display_ids = [display.display_id for display in self.displays]
        if len(display_ids) != len(set(display_ids)):
            raise ValueError("displayId values must be unique")
        if sum(display.is_primary for display in self.displays) != 1:
            raise ValueError("exactly one display must be primary")
        return self


class ProjectionAssignment(_ProjectionModel):
    role: ProjectionRole
    display_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="displayId")
    window_generation: int = Field(
        ge=0,
        le=2_147_483_647,
        alias="windowGeneration",
    )


class ProjectionReceipt(_ProjectionModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    command_id: UUID = Field(alias="commandId")
    session_id: UUID | None = Field(alias="sessionId")
    command: ProjectionCommandName
    accepted: bool
    status: ProjectionStatus
    generation: int = Field(ge=0, le=2_147_483_647)
    message: str = Field(max_length=500)
    assignments: tuple[ProjectionAssignment, ...] = Field(default=(), max_length=2)

    @model_validator(mode="after")
    def unique_assignments(self) -> ProjectionReceipt:
        roles = [assignment.role for assignment in self.assignments]
        displays = [assignment.display_id for assignment in self.assignments]
        if len(roles) != len(set(roles)):
            raise ValueError("roles must be unique")
        if len(displays) != len(set(displays)):
            raise ValueError("displayId values must be unique")
        return self


class ProjectionEvent(_ProjectionModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    event_id: UUID = Field(alias="eventId")
    session_id: UUID = Field(alias="sessionId")
    generation: int = Field(ge=0, le=2_147_483_647)
    sequence: int = Field(ge=0, le=2_147_483_647)
    occurred_at: datetime = Field(alias="occurredAt")
    event_type: ProjectionEventType = Field(alias="eventType")
    status: ProjectionStatus
    payload: dict[str, JsonValue] = Field(max_length=32)
