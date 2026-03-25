from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass
class ExternalAccount:
    id: str
    name: str
    type: str
    currency: str


@dataclass
class ExternalTransaction:
    account_id: str
    posted_at: str
    amount: float
    currency: str
    description: str
    counterparty: str | None = None


class BankingConnector:
    def list_accounts(self, user_id: str) -> Iterable[ExternalAccount]:  # pragma: no cover
        raise NotImplementedError

    def list_transactions(self, user_id: str) -> Iterable[ExternalTransaction]:  # pragma: no cover
        raise NotImplementedError


