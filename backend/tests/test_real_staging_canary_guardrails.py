from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from scripts.run_real_staging_intelligence_canary import _expire_model_approval


def test_expire_model_approval_preserves_immutable_status_and_clamps_expiry():
    now = datetime.now(timezone.utc)
    approval = SimpleNamespace(status="APPROVED", expires_at=now + timedelta(minutes=15))

    _expire_model_approval(approval, now)

    assert approval.status == "APPROVED"
    assert approval.expires_at == now


def test_expire_model_approval_does_not_extend_an_expired_record():
    now = datetime.now(timezone.utc)
    expired_at = now - timedelta(seconds=1)
    approval = SimpleNamespace(status="APPROVED", expires_at=expired_at)

    _expire_model_approval(approval, now)

    assert approval.status == "APPROVED"
    assert approval.expires_at == expired_at
