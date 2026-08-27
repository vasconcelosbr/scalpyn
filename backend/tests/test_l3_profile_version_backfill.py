from uuid import UUID

from scripts.backfill_l3_profile_execution_versions import summarize_contracts


def _snapshot(profile_id: UUID, *, status: str):
    return {
        "name": "L3_TEST",
        "contract": {
            "profile_version_id": None if status == "MISMATCH" else "version-1",
            "profile_projection_hash": "a" * 64,
            "version_config_hash": None if status == "MISMATCH" else "a" * 64,
            "status": status,
            "reason_codes": (
                ["PROFILE_VERSION_MISSING"] if status == "MISMATCH" else []
            ),
        },
    }


def test_backfill_dry_run_report_is_read_only_and_explicit():
    profile_id = UUID("11111111-1111-1111-1111-111111111111")
    report = summarize_contracts(
        {profile_id: _snapshot(profile_id, status="MISMATCH")},
        mode="dry-run",
    )

    assert report["mode"] == "dry-run"
    assert report["status"] == "MISMATCH"
    assert report["mismatches"] == 1
    assert report["items"][0]["reason_codes"] == ["PROFILE_VERSION_MISSING"]


def test_backfill_verification_report_passes_only_when_every_profile_matches():
    first = UUID("11111111-1111-1111-1111-111111111111")
    second = UUID("22222222-2222-2222-2222-222222222222")
    report = summarize_contracts(
        {
            first: _snapshot(first, status="MATCH"),
            second: _snapshot(second, status="MATCH"),
        },
        mode="verification",
    )

    assert report["status"] == "MATCH"
    assert report["mismatches"] == 0
