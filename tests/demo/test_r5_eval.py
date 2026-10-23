"""R5 평가 사례(eval/cases/r5_cases.json)와 채점(scripts/demo/eval_cases.py) 테스트 — DART·AI 호출 없음."""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "demo"))

import eval_cases as ev  # noqa: E402

R5 = json.loads((ROOT / "eval" / "cases" / "r5_cases.json").read_text(encoding="utf-8"))
STAGES = {"RED", "ORANGE", "YELLOW", "COMPOSITE", "WATCH", "NONE"}


def test_r5_case_shape_and_real_identifiers():
    ids = [c["case_id"] for c in R5["cases"]]
    assert len(ids) == len(set(ids)) and all(i.startswith("r5-") for i in ids)
    for c in R5["cases"]:
        assert c["scope"] in ("timeline", "company_year")
        assert c["expected_final_stage"] in STAGES | {None}
        assert c["expected_alert"] in (True, False, None)
        assert c["window"]["from"] <= c["window"]["to"] == c["as_of"]
        for s in c["steps"]:
            # 접수번호는 DART 14자리, 링크는 같은 접수번호, 날짜는 기간 안 (지어낸 값이면 대개 여기서 걸린다)
            assert re.fullmatch(r"\d{14}", s["rcept_no"]), s
            assert s["url"].endswith(s["rcept_no"])
            assert c["window"]["from"] <= s["filed_at"] <= c["as_of"]
            assert s["rcept_no"][:8] <= s["filed_at"].replace("-", "") or s["rcept_no"][:4] == s["filed_at"][:4]


def test_no_score_numbers_in_case_files():
    text = (ROOT / "eval" / "cases" / "r5_cases.json").read_text(encoding="utf-8")
    assert '"points"' not in text and '"total_score"' not in text and '"sort_value"' not in text


def test_red_title_alone_is_red():
    eng = ev.Engine()
    step = {"rcept_no": "20231005000522", "filed_at": "2023-10-05", "title": "주요사항보고서(부도발생)"}
    sigs, missing = eng.signals(step)
    assert [s["signal_type"] for s in sigs] == ["DEFAULT"] and missing == []
    assert ev.stage_of(eng, sigs, "2023-10-05")["stage"] == "RED"


def test_followups_and_corrections_are_left_to_ai():
    eng = ev.Engine()
    assert eng.skip_reason({"title": "매매거래정지및정지해제(불성실공시)"})
    assert eng.skip_reason({"title": "[기재정정]주요사항보고서(유상증자결정)"})
    assert eng.skip_reason({"title": "자기주식처분결과보고서"})
    assert eng.skip_reason({"title": "주요사항보고서(유상증자결정)"}) is None


def test_rights_offering_values_come_only_from_facts():
    eng = ev.Engine()
    step = {"rcept_no": "20241031000530", "filed_at": "2024-10-31", "title": "주요사항보고서(유상증자결정)",
            "facts": {"nstk_ostk_cnt": "1,000", "bfic_tisstk_ostk": "4,000", "ic_mthn": "제3자배정증자"},
            "facts_meta": {"api": "piicDecsn"}}
    [sig], missing = eng.signals(step)
    assert sig["detail"]["ratio_pct"] == 25.0 and sig["detail"]["third_party"] is True
    assert sig["evidence"] and sig["evidence"][0]["location"] == "piicDecsn.ic_mthn"
    assert missing == ["RIGHTS_OFFERING.payment_delayed_or_failed"]
    [bare], missing = eng.signals({**step, "facts": None})
    assert "ratio_pct" not in bare["detail"] and "RIGHTS_OFFERING.ratio_pct" in missing


def test_subsidiary_titles_do_not_alert():
    eng = ev.Engine()
    step = {"rcept_no": "20251022800554", "filed_at": "2025-10-22", "title": "영업정지(종속회사의주요경영사항)"}
    sigs, _ = eng.signals(step)
    assert ev.stage_of(eng, sigs, "2025-10-22")["stage"] == "NONE"


def test_replay_counts_first_alert_and_new_alerts():
    eng = ev.Engine()
    steps = [
        {"rcept_no": "20231004000001", "filed_at": "2023-10-04", "title": "주주총회소집결의"},
        {"rcept_no": "20231005000522", "filed_at": "2023-10-05", "title": "주요사항보고서(부도발생)"},
        {"rcept_no": "20231006000001", "filed_at": "2023-10-06", "title": "[기재정정]주요사항보고서(부도발생)"},
    ]
    r = ev.replay(eng, steps, "2023-10-31")
    assert r["final"] == "RED" and r["first_alert"] == "2023-10-05" and r["alerts"] == 1
    assert [x["reason"] for x in r["skipped"]] == ["정정(같은 사건)"]


def test_report_renders_from_real_case_files():
    text = ev.render(ev.evaluate(ev.load_cases()))
    assert "| 지표 | 결과 | 설명 |" in text and "r5-timeline-001" in text
    # 화면·문서용 결과표에도 점수 숫자는 넣지 않는다
    assert "pts=" not in text and "점수 " not in text


def test_threshold_compare_baseline_matches_scorer_and_leaves_rules_untouched():
    import compare_thresholds as ct
    from risk_engine.loader import DEFAULT_PATH
    before = DEFAULT_PATH.read_bytes()
    rows = ct.compare()
    assert DEFAULT_PATH.read_bytes() == before  # R4의 rules.yaml은 고치지 않는다
    names = [name for name, _, _ in rows]
    assert names[0] == "지금 기준" and "복합 규칙 끔" in names
    base = ev.summarize(ev.evaluate(ev.load_cases()))
    assert rows[0][2]["single"] == base["single"] and rows[0][2]["false_alarms"] == base["false_alarms"]
    off = dict((name, m) for name, _, m in rows)["복합 규칙 끔"]
    assert all("COMPOSITE" not in active for active in off["active"].values())
    text = ct.render(rows)
    assert "| 설정 |" in text and "pts=" not in text


def test_r1_gold_answers_for_r5_cases():
    """R1 위험 정책 3-3: 금양 🟠 먼저 + 🟡 추가(맨 위 🟠), CJ CGV 🟠, 현대차 오탐은 PB 판단 영역"""
    cases = {c["case_id"]: c for c in R5["cases"]}
    kumyang, cgv, hyundai = cases["r5-timeline-001"], cases["r5-timeline-002"], cases["r5-normal-001"]
    assert kumyang["expected_final_stage"] == "ORANGE" and kumyang["expected_final_stages"] == ["ORANGE", "YELLOW"]
    assert kumyang["expected_first_alert_stage"] == "ORANGE"
    assert cgv["expected_final_stage"] == "ORANGE" and cgv["expected_alert"] is True and "ambiguity" not in cgv
    assert hyundai["expected_alert"] is False and hyundai["false_alarm_policy"]["category"] == "PB 판단 영역"
    res = ev.evaluate(ev.load_cases())
    by_id = {t["case"]["case_id"]: t for t in res["timelines"]}
    assert by_id["r5-timeline-001"]["exp_ok"] is True and by_id["r5-timeline-002"]["exp_ok"] is True
    m = ev.summarize(res)
    assert m["lead_days"]["r5-timeline-001"] == 141
    assert m["false_alarms_pb"] == m["false_alarms"][0]  # 지금 오탐은 모두 PB 판단 영역
    text = ev.render(res)
    assert "사건 141일 전에 조기경보가 울렸다" in text
    assert text.index("적중 (1순위)") < text.index("| 오탐 |")
