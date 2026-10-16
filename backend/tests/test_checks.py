from app.ai.numcheck import unsupported_numbers
from app.ai.validate import code_check, normalise
from app.sim.engine import SCENARIOS
from app.analytics.evidence import build_snapshot
from app.ai import fake


def _snap():
    return build_snapshot(SCENARIOS["power-water"], {}, 42)


def test_numcheck_rejects_altered_number():
    assert unsupported_numbers("about 14.9 hours to critical", allowed=[13.846, 18.462]) == [14.9]
    assert unsupported_numbers("about 13.8 hours to critical", allowed=[13.846, 18.462]) == []


def test_clean_fake_assessment_passes_code_checks():
    s = _snap()
    a = normalise(fake._assess(s))
    assert code_check(a, s)["ok"], code_check(a, s)


def test_invented_ids_and_numbers_rejected():
    s = _snap()
    a = normalise(fake._assess(s, corrupt=True))
    cc = code_check(a, s)
    assert not cc["ok"]
    assert any("ev-99" in m for ms in cc["items"].values() for m in ms)
    assert any("14.9" in m for ms in cc["items"].values() for m in ms)


def test_safety_invariants_forced():
    s = _snap()
    a = fake._assess(s)
    a["actions"][0]["requires_approval"] = False
    a["hypotheses"][0]["status"] = "confirmed"
    a = normalise(a)
    assert a["actions"][0]["requires_approval"] is True and a["hypotheses"][0]["status"] == "unverified"


def test_snapshot_deterministic_and_action_sensitive():
    sc = SCENARIOS["power-water"]
    assert build_snapshot(sc, {}, 42)["snapshot_hash"] == build_snapshot(sc, {}, 42)["snapshot_hash"]
    assert build_snapshot(sc, {}, 42)["snapshot_hash"] != build_snapshot(sc, {"restore_power_feed": 40}, 42)["snapshot_hash"]
