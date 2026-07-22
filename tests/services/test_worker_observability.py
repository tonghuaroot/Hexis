from __future__ import annotations

from services.worker_service import _result_has_work


def test_result_has_work_filters_idle_poll_results():
    assert _result_has_work(None) is False
    assert _result_has_work(0) is False
    assert _result_has_work([]) is False
    assert _result_has_work({"skipped": True, "reason": "idle"}) is False

    assert _result_has_work(1) is True
    assert _result_has_work(["claimed"]) is True
    assert _result_has_work({"claimed": 1}) is True
    assert _result_has_work({"skipped": False, "processed": 0}) is True
