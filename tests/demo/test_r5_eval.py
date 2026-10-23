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
