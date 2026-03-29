"""Driver event sink implementations."""

from __future__ import annotations

import json
import os
import time
from io import UnsupportedOperation
from pathlib import Path
from typing import IO, TYPE_CHECKING, Protocol

from .registry import get_event_record, validate_event_payload
from .types import Event, JSONValue

if TYPE_CHECKING:
    from ..config import ObservabilityConfig


class Observer(Protocol):
    def emit(self, event_type: str, /, **data: object) -> None: ...

    def close(self) -> None: ...


class FileObserver:
    def __init__(self, config: ObservabilityConfig, *, file_handle: IO[str] | None = None) -> None:
        self._config = config
        self._file: IO[str] | None = file_handle
        self._owns_file = file_handle is None

    def _ensure_file(self) -> IO[str]:
        if self._file is None:
            path = Path(self._config.events_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._file = open(path, "a", encoding="utf-8")
        return self._file

    def emit(self, event_type: str, /, **data: object) -> None:
        record = get_event_record(event_type)
        validate_event_payload(record, data)
        event = Event(
            ts=time.time(),
            event=record.event,
            version=record.version,
            data={k: _serialize_value(v) for k, v in data.items()},
        )
        line = json.dumps(
            {"ts": event.ts, "event": event.event, "version": event.version, "data": event.data}
        )
        sink = self._ensure_file()
        sink.write(line + "\n")
        sink.flush()
        if hasattr(sink, "fileno"):
            fd: int | None
            try:
                fd = sink.fileno()
            except (OSError, UnsupportedOperation):
                fd = None
            if fd is not None and fd >= 0:
                os.fsync(fd)

    def close(self) -> None:
        if self._file is not None and self._owns_file:
            self._file.flush()
            self._file.close()
            self._file = None


class NullObserver:
    def emit(self, event_type: str, /, **data: object) -> None:
        record = get_event_record(event_type)
        validate_event_payload(record, data)

    def close(self) -> None:
        return None


def create_observer(config: ObservabilityConfig) -> Observer:
    return FileObserver(config)


def _serialize_value(value: object) -> JSONValue:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _serialize_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize_value(v) for v in value]
    return str(value)
