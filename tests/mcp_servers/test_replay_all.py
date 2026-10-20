"""scripts/replay_all.py의 기대 결과 비교(judge) 로직 테스트 — 서버·DB 없이 합성 결과로."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import replay_all as ra  # noqa: E402


def res(expected, event_date="2025-03-21", first=None, event=None, customers=None, assessments=(), errors=0):
    return {
        "company": {"stock_code": "000000", "expected": expected, "event": {"date": event_date}},
        "first_alert": first, "event": event, "customers": customers or {},
        "assessments": list(assessments), "errors": errors,
    }


def verdicts(r):
    return [ok for ok, _ in ra.judge(r)]


def test_first_alert_before_event():
    exp = {"first_alert_before_event": True, "first_alert_level_min": "High"}
    ok = res(exp, first={"date": "2024-05-03", "level": "High"})
    assert verdicts(ok) == [True, True]
    assert "322일" in ra.judge(ok)[0][1]
    assert verdicts(res(exp, first=None))[0] is False                                     # 경보가 없었다
    assert verdicts(res(exp, first={"date": "2025-03-21", "level": "High"}))[0] is False  # 사건 당일은 "먼저"가 아니다


def test_no_alert_expected_before_event():
    exp = {"first_alert_before_event": False}
    assert verdicts(res(exp, first=None)) == [True]
    assert verdicts(res(exp, first={"date": "2021-09-01", "level": "High"})) == [False]


def test_event_level_and_holders():
    exp = {"event_level": "Critical", "holders": ["C005", "C011", "C012"]}
    good = res(exp, event={"level": "Critical"}, customers={"P1": ["C005"], "P2": ["C011", "C012"]})
    assert verdicts(good) == [True, True]
    assert verdicts(res(exp, event={"level": "High"}, customers={"P1": ["C005"]})) == [False, False]


def test_p1_p2_exact_match():
    exp = {"p1": ["C001", "C003"], "p2": ["C002"]}
    assert all(verdicts(res(exp, customers={"P1": ["C001", "C003"], "P2": ["C002"]})))
    assert not all(verdicts(res(exp, customers={"P1": ["C001"], "P2": ["C002", "C003"]})))


def test_max_level_and_no_alert():
    exp = {"max_level": "Medium", "alert": False}
    low = [{"level": "Low"}, {"level": "Medium"}]
    assert verdicts(res(exp, assessments=low)) == [True, True]
    assert verdicts(res(exp, assessments=low + [{"level": "High"}])) == [False, False]


def test_errors_fail_the_company():
    assert verdicts(res({}, errors=2)) == [False]
