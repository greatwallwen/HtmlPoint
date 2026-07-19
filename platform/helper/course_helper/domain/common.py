"""Shared primitives for immutable, versioned helper artifacts."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime
from pathlib import PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from typing_extensions import TypeAliasType


KeyT = TypeVar("KeyT")
ValueT = TypeVar("ValueT")
JsonAtom = str | bool | int | float | None
ImmutableJsonValue = TypeAliasType(
    "ImmutableJsonValue",
    JsonAtom | Mapping[str, "ImmutableJsonValue"] | Sequence["ImmutableJsonValue"],
)

OpaqueId = Annotated[
    str,
    StringConstraints(
        strict=True,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
    ),
]


class FrozenDict(Mapping[KeyT, ValueT], Generic[KeyT, ValueT]):
    """Read-only mapping used after Pydantic has validated JSON-shaped data."""

    __slots__ = ("_data",)

    _data: Mapping[KeyT, ValueT]

    def __init__(self, values: Mapping[KeyT, ValueT]) -> None:
        object.__setattr__(self, "_data", MappingProxyType(dict(values)))

    def __getitem__(self, key: KeyT) -> ValueT:
        return self._data[key]

    def __iter__(self) -> Iterator[KeyT]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __setattr__(self, name: str, value: object) -> None:
        raise TypeError("FrozenDict is immutable")

    def __delattr__(self, name: str) -> None:
        raise TypeError("FrozenDict is immutable")

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Mapping):
            return dict(self.items()) == dict(other.items())
        return NotImplemented

    def __repr__(self) -> str:
        return f"FrozenDict({dict(self.items())!r})"

    def __copy__(self) -> FrozenDict[KeyT, ValueT]:
        return self

    def __deepcopy__(self, memo: dict[int, Any]) -> FrozenDict[KeyT, ValueT]:
        return self


def freeze_json(value: Any) -> Any:
    """Recursively detach and freeze already-validated JSON-shaped data."""

    if isinstance(value, FrozenDict):
        return value
    if isinstance(value, Mapping):
        return FrozenDict({key: freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze_json(item) for item in value)
    return value


def thaw_json(value: Any) -> Any:
    """Return plain JSON-compatible containers for Pydantic serialization."""

    if isinstance(value, Mapping):
        return {key: thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(item) for item in value]
    return value


class ActorRef(BaseModel):
    """Stable reference to the actor that created an artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    actor_type: Literal["human", "service", "model", "system"]
    actor_id: str = Field(min_length=1)
    display_name: str | None = None


class SourceLocator(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    root_id: str = Field(min_length=1)
    relative_path: str = Field(min_length=1)

    @field_validator("relative_path")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("relative_path cannot contain NUL")
        windows_path = PureWindowsPath(value)
        normalized = value.replace("\\", "/")
        path = PurePosixPath(normalized)
        if windows_path.drive or windows_path.is_absolute() or path.is_absolute() or ".." in path.parts:
            raise ValueError("relative_path must stay inside its source root")
        return path.as_posix()


class VersionMeta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    logical_id: str = Field(min_length=1)
    version_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    supersedes_version_id: str | None = None
    created_at: datetime
    created_by: ActorRef
