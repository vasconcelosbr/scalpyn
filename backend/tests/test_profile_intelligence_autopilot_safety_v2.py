from pathlib import Path


SERVICE = Path(__file__).parents[1] / "app" / "services" / "profile_intelligence_autopilot_service.py"


def test_automatic_cycle_does_not_auto_approve_or_mutate_incumbent():
    source = SERVICE.read_text(encoding="utf-8")
    assert 'approval_source="AUTOPILOT_AUTO_APPLY"' not in source
    assert 'event_type="PROFILE_MUTATED_IN_PLACE"' not in source
    assert "profile.config = config" not in source
    assert "activation remains an explicit authenticated operator action" in source
