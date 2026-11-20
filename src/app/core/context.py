from __future__ import annotations

from contextvars import ContextVar

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_subject_id: ContextVar[str | None] = ContextVar("subject_id", default=None)


def set_request_id(request_id: str | None) -> None:
    _request_id.set(request_id)


def get_request_id() -> str | None:
    return _request_id.get()


def set_subject_id(subject_id: str | None) -> None:
    _subject_id.set(subject_id)


def get_subject_id() -> str | None:
    return _subject_id.get()
