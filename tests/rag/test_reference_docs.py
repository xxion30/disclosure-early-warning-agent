"""참고 문서(rag_docs/reference) 검사: 이 문서는 search_reference로 검색되어 설명문 생성에 쓰이므로,
내부 점수·배점이 새어 나가거나 매수·매도 권유 표현이 들어가면 안 된다 (R2가 설명문에서 점수 숫자 언급 금지를 요구)."""
import re

import pytest

from harness.guardrails import find_forbidden, find_score_disclosure
from mcp_servers.rag import ingest, store

DOCS = sorted((ingest.RAG_DIR / "reference").glob("*.md"))

# 설계서(docs/dartch_phase1_mvp.md STEP 02)의 위험 항목. 공시 해설에 항목마다 소제목(###)이 있어야 한다
RED = ["부도발생", "회생절차 개시신청", "채권은행 등의 관리절차 개시", "해산사유 발생", "영업정지", "거래정지", "관리종목 지정",
       "국내 상장폐지 사유 발생", "횡령·배임 혐의 발생"]
ORANGE = ["소송 등의 제기", "감자 결정", "유상증자·유무상증자", "CB·BW·EB 발행", "영업·자산·타법인 지분 양수도", "자산양수도·풋백옵션",
          "합병·분할·주식교환·이전", "자기주식 처분·취득 신탁계약 해지", "채권은행 관리절차 중단", "해외 상장폐지"]
YELLOW = ["감사의견", "자본잠식", "영업현금흐름", "이자 부담", "단기채무"]


def body(path):
    return ingest.parse_doc(path)[1]


def test_all_four_categories_are_present_and_indexable():
    categories = {ingest.parse_doc(p)[0]["category"] for p in DOCS}
    assert categories == set(ingest.CATEGORIES)
    for p in DOCS:
        _, records, _ = ingest.build_records(p, store.REFERENCE)
        assert records, p.name


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.name)
def test_no_internal_score_or_weight_expressions(path):
    assert find_score_disclosure(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("path", [p for p in DOCS if p.name != "message_guide.md"], ids=lambda p: p.name)
def test_no_investment_advice_or_overstatement(path):
    # message_guide.md는 금지 표현을 "쓰지 않는 표현" 예시로 나열하는 문서라 제외한다
    assert find_forbidden(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.name)
def test_no_legacy_grade_or_impact_vocabulary(path):
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"\b(Critical|High|Medium)\b|기본 점수|영향도|rules\.yaml", text)


def test_disclosure_guide_covers_every_risk_item_in_the_design():
    headings = set(re.findall(r"^###\s+(.+)$", body(next(p for p in DOCS if p.name == "disclosure_guide.md")), re.M))
    # 소제목은 항목명 뒤에 설명을 붙일 수 있다 (예: "거래정지 (새로 걸린 정지)"). 항목명이 소제목 맨 앞에 있으면 된다
    covered = lambda item: any(h == item or h.startswith(item + " ") for h in headings)  # noqa: E731
    assert [item for item in RED + ORANGE + YELLOW if not covered(item)] == []


def test_numbers_in_listing_rules_are_flagged_as_unverified():
    """상장규정 수치는 시장·시점마다 다르다. 확인하지 못한 기준은 TODO(확인 필요)로 남겨 에이전트가 단정하지 않게 한다."""
    text = body(next(p for p in DOCS if p.name == "listing_rules.md"))
    assert text.count("TODO(확인 필요)") >= 4
    assert "시장" in text and "개정" in text  # 시장별 차이와 개정 가능성을 밝힌다
