"""replay_all.py의 결과 비교 검증. 서버·DB·모델 호출 없이 실행한다."""

import sys
from pathlib import Path

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[2] / "scripts" / "demo"),
)

import replay_all as ra  # noqa: E402


def res(
    expected,
    event_date="2025-03-21",
    first=None,
    event=None,
    customers=None,
    assessments=(),
    errors=0,
):
    return {
        "company": {
            "stock_code": "000000",
            "expected": expected,
            "event": {"date": event_date},
        },
        "first_alert": first,
        "event": event,
        "customers": customers or {},
        "assessments": list(assessments),
        "errors": errors,
    }


def verdicts(result):
    return [ok for ok, _ in ra.judge(result)]


def test_first_alert_before_event():
    expected = {
        "first_alert_before_event": True,
        "first_alert_level_min": "High",
    }
    good = res(
        expected,
        first={"date": "2024-05-03", "level": "High"},
    )

    assert verdicts(good) == [True, True]
    assert "322일" in ra.judge(good)[0][1]
    assert verdicts(res(expected, first=None))[0] is False

    same_day = res(
        expected,
        first={"date": "2025-03-21", "level": "High"},
    )
    assert verdicts(same_day)[0] is False


def test_no_alert_expected_before_event():
    expected = {"first_alert_before_event": False}

    assert verdicts(res(expected, first=None)) == [True]
    assert verdicts(
        res(expected, first={"date": "2021-09-01", "level": "High"})
    ) == [False]


def test_event_level_and_holders():
    expected = {
        "event_level": "Critical",
        "holders": ["C005", "C011", "C012"],
    }

    good = res(
        expected,
        event={"level": "Critical"},
        customers={"holders": ["C005", "C011", "C012"]},
    )
    assert verdicts(good) == [True, True]

    bad = res(
        expected,
        event={"level": "High"},
        customers={"holders": ["C005"]},
    )
    assert verdicts(bad) == [False, False]


def test_legacy_gold_compares_holder_ids_without_priorities():
    # 예전 정답에서 고객 ID만 가져오고 P1/P2 등급은 검증하지 않는다.
    expected = {
        "p1": ["C001", "C003"],
        "p2": ["C002"],
    }

    assert verdicts(
        res(expected, customers={"holders": ["C003", "C002", "C001"]})
    ) == [True]

    # 고객 누락은 실패한다.
    assert verdicts(
        res(expected, customers={"holders": ["C001", "C002"]})
    ) == [False]

    # 대상이 아닌 고객이 포함돼도 실패한다.
    assert verdicts(
        res(
            expected,
            customers={"holders": ["C001", "C002", "C003", "C004"]},
        )
    ) == [False]


def test_holder_comparison_ignores_order_and_duplicates():
    expected = {"holders": ["C001", "C002"]}

    assert verdicts(
        res(expected, customers={"holders": ["C002", "C001", "C002"]})
    ) == [True]


def test_explicit_holders_override_legacy_gold():
    expected = {
        "holders": ["C003"],
        "p1": ["C001"],
        "p2": ["C002"],
    }

    assert verdicts(
        res(expected, customers={"holders": ["C003"]})
    ) == [True]
    assert verdicts(
        res(expected, customers={"holders": ["C001", "C002"]})
    ) == [False]


def test_empty_holders_are_checked():
    expected = {"holders": []}

    assert verdicts(
        res(expected, customers={"holders": []})
    ) == [True]
    assert verdicts(
        res(expected, customers={"holders": ["C001"]})
    ) == [False]


def test_max_level_and_no_alert():
    expected = {"max_level": "Medium", "alert": False}
    low = [{"level": "Low"}, {"level": "Medium"}]

    assert verdicts(res(expected, assessments=low)) == [True, True]
    assert verdicts(
        res(expected, assessments=low + [{"level": "High"}])
    ) == [False, False]


def test_errors_fail_the_company():
    assert verdicts(res({}, errors=2)) == [False]