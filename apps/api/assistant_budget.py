"""Durable, shared reservations for a non-renewing assistant pilot allowance.

Amounts are integer nanodollars (1 USD = 1_000_000_000). A reservation is
never automatically released: an interrupted paid request may still be billed.
Only a confirmed bounded usage result can settle a reservation.
"""
from __future__ import annotations

import re
from uuid import UUID


MAX_NANO_USD = 1_000_000_000_000


class AssistantBudgetError(RuntimeError):
    """Text-free budget failure; safe for the provider's public error mapping."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class PersistentAssistantBudget:
    def __init__(self, repository, budget_id: str):
        if not isinstance(budget_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,99}", budget_id):
            raise AssistantBudgetError("invalid_budget")
        self.repository = repository
        self.budget_id = budget_id

    def _call(self, action: str, request_id: str, amount_nano_usd: int) -> None:
        try:
            if not isinstance(request_id, str):
                raise ValueError
            request_id = str(UUID(request_id))
        except (ValueError, TypeError, AttributeError):
            raise AssistantBudgetError("invalid_budget") from None
        minimum = 1 if action == "RESERVE" else 0
        if type(amount_nano_usd) is not int or not minimum <= amount_nano_usd <= MAX_NANO_USD:
            raise AssistantBudgetError("invalid_budget")
        try:
            value = self.repository._json(self.repository._request(
                "POST", "/rpc/onflows_assistant_budget", json={
                    "p_action": action,
                    "p_budget_id": self.budget_id,
                    "p_request_id": request_id,
                    "p_amount_nano_usd": amount_nano_usd,
                },
            ))
        except Exception:
            # Never infer admission from a transport error or an unknown result.
            raise AssistantBudgetError("budget_unavailable") from None
        if not isinstance(value, dict):
            raise AssistantBudgetError("budget_unavailable")
        if value.get("error"):
            code = value["error"]
            raise AssistantBudgetError(code if code in {
                "budget_exhausted", "budget_unavailable", "invalid_budget",
            } else "budget_unavailable")
        expected = "reserved" if action == "RESERVE" else "settled"
        if value.get(expected) is not True:
            raise AssistantBudgetError("budget_unavailable")

    def reserve(self, request_id: str, amount_nano_usd: int) -> None:
        """Admit one new paid request; a reused UUID never grants admission."""
        self._call("RESERVE", request_id, amount_nano_usd)

    def settle(self, request_id: str, amount_nano_usd: int) -> None:
        """Confirm known usage, releasing only the unspent part of its hold."""
        self._call("SETTLE", request_id, amount_nano_usd)
