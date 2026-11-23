from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass

from app.core.error_codes import ErrorCode
from app.core.errors import AppError


class SecretError(AppError):
    default_code = ErrorCode.SECRET_PROVIDER


class SecretNotFoundError(SecretError):
    default_code = ErrorCode.SECRET_NOT_FOUND

    def __init__(self, secret_name: str) -> None:
        self.secret_name = secret_name
        super().__init__("必需密钥缺失")


class SecretValue:
    """Opaque wrapper that never prints or serializes its plaintext.

    Call ``reveal()`` only at the boundary where the third-party SDK needs the
    credential. Never store the result.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str):
            raise TypeError("secret value must be a string")
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __bool__(self) -> bool:
        return bool(self._value)

    def __len__(self) -> int:
        return len(self._value)

    def __repr__(self) -> str:
        return "SecretValue('***')"

    def __str__(self) -> str:
        return "***"


@dataclass(frozen=True)
class SecretRef:
    path: str
    raw: str

    @classmethod
    def parse(cls, value: str) -> SecretRef:
        if not value.startswith("secret://"):
            raise ValueError("secret reference must start with secret://")
        path = value.removeprefix("secret://").strip("/")
        if not path:
            raise ValueError("secret reference path must not be empty")
        return cls(path=path, raw=value)


def parse_secret_ref(value: str) -> SecretRef:
    return SecretRef.parse(value)


class SecretProvider(ABC):
    """Pluggable secret backend."""

    @abstractmethod
    async def get(self, name: str) -> SecretValue | None:
        """Return a secret or None when it does not exist."""

    @abstractmethod
    async def get_many(self, names: Sequence[str]) -> dict[str, SecretValue]:
        """Return only the secrets that exist."""

    async def refresh(self) -> None:
        """Invalidate provider-side caches if needed."""

        return None
