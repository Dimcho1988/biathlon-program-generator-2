"""The budget adapter admits only explicit durable reservation acknowledgements."""
from uuid import uuid4

import pytest

from apps.api.assistant_budget import AssistantBudgetError, PersistentAssistantBudget


BUDGET = "onflows-staging-luna-pilot-v1"


class Repository:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def _request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs["json"]))
        if self.error:
            raise self.error
        return self.result

    def _json(self, response):
        return response


def test_reserve_and_settle_pass_integer_amounts_and_scoped_uuid():
    request_id = str(uuid4())
    repo = Repository({"reserved": True})
    budget = PersistentAssistantBudget(repo, BUDGET)
    assert budget.reserve(request_id, 25_000_000) is None
    assert repo.calls == [("POST", "/rpc/onflows_assistant_budget", {
        "p_action": "RESERVE", "p_budget_id": BUDGET, "p_request_id": request_id,
        "p_amount_nano_usd": 25_000_000,
    })]
    repo.result = {"settled": True}
    assert budget.settle(request_id, 0) is None
    assert repo.calls[-1][2]["p_action"] == "SETTLE"
    assert repo.calls[-1][2]["p_amount_nano_usd"] == 0


@pytest.mark.parametrize("result", [None, [], True, {}, {"reserved": False}, {"reserved": 1},
                                   {"settled": True}, {"error": "unrecognized_failure"}])
def test_unknown_reservation_result_fails_closed(result):
    budget = PersistentAssistantBudget(Repository(result), BUDGET)
    with pytest.raises(AssistantBudgetError) as caught:
        budget.reserve(str(uuid4()), 25_000_000)
    assert caught.value.code == "budget_unavailable"


@pytest.mark.parametrize("code", ["budget_exhausted", "budget_unavailable", "invalid_budget"])
def test_explicit_budget_rejection_is_not_admission(code):
    budget = PersistentAssistantBudget(Repository({"error": code}), BUDGET)
    with pytest.raises(AssistantBudgetError) as caught:
        budget.reserve(str(uuid4()), 25_000_000)
    assert caught.value.code == code


def test_transport_failure_does_not_leak_details_or_retry():
    repo = Repository(error=RuntimeError("private connection detail"))
    with pytest.raises(AssistantBudgetError) as caught:
        PersistentAssistantBudget(repo, BUDGET).reserve(str(uuid4()), 25_000_000)
    assert str(caught.value) == "budget_unavailable"
    assert len(repo.calls) == 1


@pytest.mark.parametrize("amount", [True, False, 0, -1, 1.0, "1", None, 1_000_000_000_001])
def test_invalid_reservations_do_not_touch_the_ledger(amount):
    repo = Repository({"reserved": True})
    with pytest.raises(AssistantBudgetError) as caught:
        PersistentAssistantBudget(repo, BUDGET).reserve(str(uuid4()), amount)
    assert caught.value.code == "invalid_budget"
    assert not repo.calls


@pytest.mark.parametrize("request_id", [None, "", "not-a-uuid", 123])
def test_invalid_request_identity_is_rejected_before_rpc(request_id):
    repo = Repository({"reserved": True})
    with pytest.raises(AssistantBudgetError):
        PersistentAssistantBudget(repo, BUDGET).reserve(request_id, 25_000_000)
    assert not repo.calls


@pytest.mark.parametrize("budget_id", [None, "", "../pilot", "a" * 101, "wrong scope"])
def test_budget_scope_is_only_a_server_identifier(budget_id):
    with pytest.raises(AssistantBudgetError) as caught:
        PersistentAssistantBudget(Repository(), budget_id)
    assert caught.value.code == "invalid_budget"


def test_ambiguous_settlement_fails_without_followup_or_refund():
    repo = Repository({"settled": False})
    with pytest.raises(AssistantBudgetError) as caught:
        PersistentAssistantBudget(repo, BUDGET).settle(str(uuid4()), 1000)
    assert caught.value.code == "budget_unavailable"
    assert len(repo.calls) == 1

