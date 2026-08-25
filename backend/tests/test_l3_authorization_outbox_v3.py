from types import SimpleNamespace

import pytest

from app.services.l3_authorization_contract_v3 import canonical_hash
from app.services.l3_authorization_outbox_service import (
    _authorized_for_shadow,
    _contract,
    _validate_lineage,
)


def _objects():
    body = {
        "contract_version": "l3_authorization_contract_v3",
        "mode": "SHADOW",
        "valid": True,
        "authorization_status": "ALLOW",
        "contract_technical_decision": "ALLOW",
        "technical_decision": "ALLOW",
        "final_decision": "ALLOW",
        "profile_lineage": {
            "profile_id": "11111111-1111-1111-1111-111111111111",
            "profile_name": "Test",
            "profile_version": "2026-08-25T04:44:44Z",
            "rules_snapshot": {"block_rules": {"blocks": []}},
        },
        "watchlist_lineage": {
            "required": False,
            "status": "NOT_APPLICABLE",
            "watchlist_id": None,
        },
    }
    body["authorization_contract_hash"] = canonical_hash(body)
    decision = SimpleNamespace(
        id=1,
        metrics={"l3_authorization_contract_v3": body},
        profile_id="11111111-1111-1111-1111-111111111111",
        user_id="22222222-2222-2222-2222-222222222222",
        decision="ALLOW",
    )
    event = SimpleNamespace(
        authorization_contract_hash=body["authorization_contract_hash"],
        payload={"user_id": decision.user_id},
    )
    return decision, event, body


def test_outbox_recomputes_contract_hash_instead_of_trusting_stored_value():
    decision, event, contract = _objects()
    assert _contract(decision, event) is contract
    contract["authorization_status"] = "CONTRACT_REJECT"
    with pytest.raises(ValueError, match="AUTHORIZATION_CONTRACT_CONTENT_HASH_MISMATCH"):
        _contract(decision, event)


def test_outbox_rejects_caller_and_persisted_lineage_divergence():
    decision, event, contract = _objects()
    _validate_lineage(decision, event, contract)
    event.payload["user_id"] = "33333333-3333-3333-3333-333333333333"
    with pytest.raises(ValueError, match="CALLER_USER_LINEAGE_DIVERGENT"):
        _validate_lineage(decision, event, contract)


def test_shadow_requires_valid_contract_and_both_technical_authorities_allow():
    decision, _event, contract = _objects()
    assert _authorized_for_shadow(decision, contract) is True
    contract["valid"] = False
    assert _authorized_for_shadow(decision, contract) is False
    contract["valid"] = True
    contract["contract_technical_decision"] = "BLOCK"
    assert _authorized_for_shadow(decision, contract) is False


def test_outbox_idempotency_identity_is_decision_plus_contract_hash():
    from app.models.backoffice import L3AuthorizationOutbox

    constraints = {
        constraint.name
        for constraint in L3AuthorizationOutbox.__table__.constraints
    }
    assert "uq_l3_authorization_outbox_decision_contract" in constraints
