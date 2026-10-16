"""Documentation-only contract. No SDK, credentials, network or enabled implementation."""

from decimal import Decimal
from typing import Protocol


class BrokerContract(Protocol):
    def submit_long(self, symbol: str, quantity: int, limit: Decimal) -> str: ...


class AlpacaDisabled:
    def submit_long(self, symbol: str, quantity: int, limit: Decimal) -> str:
        raise RuntimeError("Alpaca désactivé : contrat seulement, aucune connexion ni ordre")
