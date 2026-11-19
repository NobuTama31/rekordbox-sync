"""Adapters are exclusively owned by their monitor worker; close must release resources."""

from typing import Protocol

from ..models import DeckObservation, MixerObservation


class DeckSource(Protocol):
    def read(self) -> DeckObservation: ...
    def close(self) -> None: ...


class MixerSource(Protocol):
    def read(self) -> MixerObservation: ...
    def close(self) -> None: ...
