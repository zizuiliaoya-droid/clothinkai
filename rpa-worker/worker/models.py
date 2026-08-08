"""Minimal validated models for the worker protocol."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID


class ProtocolError(RuntimeError):
    """The API returned a payload that does not match the contract."""


@dataclass(frozen=True, slots=True, repr=False)
class TaskAssignment:
    task_id: UUID
    platform: str
    target_date: date
    cred_token: str

    @classmethod
    def from_json(cls, payload: object) -> "TaskAssignment":
        if not isinstance(payload, dict):
            raise ProtocolError("poll response must be an object")
        try:
            raw = [payload[key] for key in ("task_id", "platform", "target_date", "cred_token")]
            if not all(isinstance(value, str) and value for value in raw):
                raise ValueError
            return cls(UUID(raw[0]), raw[1], date.fromisoformat(raw[2]), raw[3])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProtocolError("poll response has invalid fields") from exc


@dataclass(frozen=True, slots=True, repr=False)
class Credential:
    username: str
    password: str

    @classmethod
    def from_json(cls, payload: object) -> "Credential":
        if not isinstance(payload, dict):
            raise ProtocolError("exchange response must be an object")
        username, password = payload.get("username"), payload.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            raise ProtocolError("exchange response has invalid fields")
        return cls(username, password)
