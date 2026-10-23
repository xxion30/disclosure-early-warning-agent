"""R5 평가 사례(eval/cases/r5_cases.json)와 채점(scripts/demo/eval_cases.py) 테스트 — DART·AI 호출 없음."""
import copy
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "demo"))

import eval_cases as ev  # noqa: E402
import build_r5_cases as builder  # noqa: E402

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
        for filing in c.get("data_check_filings", []):
            assert any(s["rcept_no"] == filing["rcept_no"] and s["filed_at"] == filing["date"] for s in c["steps"])


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
    assert r["first_event_alert"] == "2023-10-05" and r["first_event_alert_stage"] == "RED"
    assert r["first_event_alert_lead_days"] == 26
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
    assert "금양 최초 전체 경보(재생)" in text and "금양 최초 사건 경보(🔴·🟠)" in text
    assert "🟡 359일 전 · R3 검증 대기" in text and "🟠 141일 전" in text
    harder_yellow = dict((name, m) for name, _, m in rows)["🟡 기준 6"]
    assert rows[0][2]["lead_days"]["r5-timeline-001"] == 359
    assert harder_yellow["lead_days"]["r5-timeline-001"] == 141
    assert harder_yellow["event_lead_days"]["r5-timeline-001"] == 141
    harder_row = next(line for line in text.splitlines() if line.startswith("| **🟡 기준 6**"))
    assert "🟠 141일 전 · 기대값과 다름" in harder_row
    assert "R3 검증 대기" not in harder_row


def test_r1_gold_answers_for_r5_cases():
    """R1 3-3~3-5: 계산은 검사하고 원본 데이터 검증 대기는 그대로 유지한다."""
    cases = {c["case_id"]: c for c in R5["cases"]}
    kumyang, cgv, hyundai = cases["r5-timeline-001"], cases["r5-timeline-002"], cases["r5-normal-001"]
    assert kumyang["expected_final_stage"] == "ORANGE" and kumyang["expected_final_stages"] == ["ORANGE", "YELLOW"]
    assert "expected_first_alert_stage" not in kumyang
    assert kumyang["expected_first_alert"] == {
        "stage": "YELLOW", "date": "2024-03-27", "lead_days": 359, "status": "pending_data_check"}
    assert kumyang["expected_first_event_alert"] == {
        "stage": "ORANGE", "date": "2024-10-31", "lead_days": 141, "status": "confirmed"}
    assert cgv["expected_final_stage"] == "ORANGE" and cgv["expected_alert"] is True and "ambiguity" not in cgv
    assert hyundai["expected_alert"] is False and hyundai["false_alarm_policy"]["category"] == "PB 판단 영역"
    res = ev.evaluate(ev.load_cases())
    by_id = {t["case"]["case_id"]: t for t in res["timelines"]}
    assert by_id["r5-timeline-001"]["exp_ok"] is True and by_id["r5-timeline-002"]["exp_ok"] is True
    k = by_id["r5-timeline-001"]
    assert (k["first_alert"], k["first_alert_stage"], k["first_alert_lead_days"]) == ("2024-03-27", "YELLOW", 359)
    assert (k["first_event_alert"], k["first_event_alert_stage"], k["first_event_alert_lead_days"]) == ("2024-10-31", "ORANGE", 141)
    assert k["final"] == "ORANGE" and k["active"] == ["ORANGE", "YELLOW"]
    assert k["first_alert_check"] is True and k["first_event_alert_check"] is True
    assert k["first_alert_status"] == "pending_data_check"  # 계산 통과로 검증 완료 처리하지 않는다
    m = ev.summarize(res)
    assert m["lead_days"]["r5-timeline-001"] == 359
    assert m["event_lead_days"]["r5-timeline-001"] == 141
    assert m["lead_days"]["r5-timeline-003"] is None
    assert m["event_lead_days"]["r5-normal-001"] is None  # 복합만으로는 최초 사건 경보로 세지 않는다
    assert m["false_alarms_pb"] == m["false_alarms"][0]  # 지금 오탐은 모두 PB 판단 영역
    text = ev.render(res)
    assert text.index("적중 (1순위)") < text.index("| 오탐 |")


def test_timeline_regeneration_from_saved_inputs_matches_committed_cases():
    """저장된 공시·facts로 CLI와 같은 조립 경로를 재생한다. DART 재수집·원본 검증과 별개다."""
    saved = {c["case_id"]: c for c in R5["cases"]}
    for spec in builder.TIMELINES:
        c = saved[spec["case_id"]]
        regenerated = builder.build_timeline_case(spec, c["corp_code"], c["corp_name"],
            copy.deepcopy(c["steps"]), c["window_disclosure_count"])
        assert regenerated == c
        assert json.dumps(regenerated, ensure_ascii=False) == json.dumps(c, ensure_ascii=False)


def test_normal_regeneration_preserves_saved_cases_and_pb_policy():
    saved = {c["case_id"]: c for c in R5["cases"]}
    for spec in builder.NORMALS:
        c = saved[spec["case_id"]]
        regenerated = builder.build_normal_case(spec, c["corp_code"], c["corp_name"],
            copy.deepcopy(c["steps"]), c["window_disclosure_count"], c["window"]["from"], c["window"]["to"])
        assert json.dumps(regenerated, ensure_ascii=False) == json.dumps(c, ensure_ascii=False)
    assert saved["r5-normal-001"]["false_alarm_policy"]["category"] == "PB 판단 영역"


def test_pending_first_alert_still_checks_stage_date_and_lead_days():
    original = next(c for c in R5["cases"] if c["case_id"] == "r5-timeline-001")
    for key, wrong in (("stage", "ORANGE"), ("date", "2024-10-31"), ("lead_days", 141)):
        c = copy.deepcopy(original)
        c["expected_first_alert"][key] = wrong
        [t] = ev.evaluate([c])["timelines"]
        assert t["first_alert_check"] is False and t["exp_ok"] is False
        assert t["first_alert_status"] == "pending_data_check"
        description = ev.describe_first_alert(t)
        assert "기대값과 다름" in description and "R3 검증 대기" not in description
    for key, wrong in (("stage", "RED"), ("date", "2024-03-27"), ("lead_days", 359)):
        c = copy.deepcopy(original)
        c["expected_first_event_alert"][key] = wrong
        [t] = ev.evaluate([c])["timelines"]
        assert t["first_event_alert_check"] is False and t["exp_ok"] is False


def test_report_separates_event_claim_from_pending_financial_replay():
    res = ev.evaluate(ev.load_cases())
    text = ev.render(res)
    assert "감사의견 거절 공시보다 141일 앞서 사건 경보가 발생했다." in text
    assert "2024-03-27 · 🟡 재무 경보 (기준일 359일 전) · R3 검증 대기" in text
    assert "2024-10-31 · 🟠 사건 경보 (기준일 141일 전)" in text
    assert "pending_data_check" in text and "실제 운영에서 당시 알림을 보냈다는 뜻이 아니다" in text
    assert "사건 359일 전에 조기경보가 울렸다" not in text
    assert "2024-03-27에 🟠" not in text and "🟠 사건 경보 먼저" not in text
    # 고정된 종류 대신 전달받은 재생 결과의 경보 종류를 표시한다.
    changed = copy.deepcopy(res)
    k = next(t for t in changed["timelines"] if t["case"]["case_id"] == "r5-timeline-001")
    k["first_alert_stage"] = "ORANGE"
    assert "2024-03-27 · 🟠 사건 경보" in ev.render(changed)


def test_r3_timeline_end_is_not_presented_as_event_lead_time():
    res = ev.evaluate(ev.load_cases())
    metrics = ev.summarize(res)
    for cid in ("r3-timeline-001", "r3-timeline-002"):
        t = next(t for t in res["timelines"] if t["case"]["case_id"] == cid)
        for event in (False, True):
            description = ev.describe_first_alert(t, event=event)
            assert t["first_alert"] in description and "🔴" in description
            assert "일 전" not in description
        assert metrics["lead_days"][cid] is None and metrics["event_lead_days"][cid] is None
    rows = [line for line in ev.render(res).splitlines() if line.startswith("| r3-timeline-")]
    assert len(rows) == 2 and all("일 전" not in row for row in rows)


def test_same_day_red_and_orange_choose_red_regardless_of_order(monkeypatch):
    # 단계 선택 단위 시험: 공시·재무 사실을 추가하지 않고 판정기 응답만 대체한다.
    for active in (["ORANGE", "RED"], ["RED", "ORANGE"]):
        monkeypatch.setattr(ev, "stage_of", lambda *args, _active=active: {
            "stage": "RED", "active_stages": _active, "manual_check": False})
        c = next(c for c in R5["cases"] if c["case_id"] == "r5-timeline-001")
        [step] = [s for s in c["steps"] if s["rcept_no"] == "20241031000530"]
        r = ev.replay(ev.Engine(), [step], step["filed_at"])
        assert r["first_event_alert_stage"] == "RED" and r["first_event_alert_lead_days"] == 0


def test_evaluation_excludes_steps_outside_window_and_after_as_of():
    original = next(c for c in R5["cases"] if c["case_id"] == "r5-timeline-001")
    variants = [copy.deepcopy(original) for _ in range(3)]
    variants[0]["window"]["from"] = "2024-10-31"
    variants[1]["as_of"] = "2024-10-31"
    variants[2]["window"]["to"] = "2024-10-31"
    for c in variants:
        clean = copy.deepcopy(c)
        end = min(c["as_of"], c["window"]["to"])
        clean["steps"] = [s for s in c["steps"] if c["window"]["from"] <= s["filed_at"] <= end]
        [actual] = ev.evaluate([c])["timelines"]
        [expected] = ev.evaluate([clean])["timelines"]
        assert {k: v for k, v in actual.items() if k != "case"} == {k: v for k, v in expected.items() if k != "case"}
        assert all(c["window"]["from"] <= d <= end for d, _ in actual["path"])


def test_pending_report_reads_filing_dates_from_case_metadata():
    c = copy.deepcopy(next(c for c in R5["cases"] if c["case_id"] == "r5-timeline-001"))
    c["data_check_filings"] = [c["data_check_filings"][1]]
    text = ev.render(ev.evaluate([c]))
    assert "확인 대상: 2024-04-17 정정본(20240417000287)" in text
    assert "2024-03-27 원본" not in text
