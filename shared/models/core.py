"""공개 데이터 테이블: 회사·공시·신호. 모든 회사(테넌트)가 공유한다. (담당: R4)

날짜는 ISO 문자열로 저장한다. 테이블 정의는 docs/contracts/db.md가 설명한다.
"""
from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from shared.models.base import Base


class Company(Base):
    __tablename__ = "companies"

    corp_code: Mapped[str] = mapped_column(String, primary_key=True)
    stock_code: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String)
    market: Mapped[str | None] = mapped_column(String, nullable=True)
    base_score: Mapped[int] = mapped_column(Integer, default=0)
    base_reasons: Mapped[list] = mapped_column(JSON, default=list)


class Disclosure(Base):
    __tablename__ = "disclosures"

    rcept_no: Mapped[str] = mapped_column(String, primary_key=True)
    corp_code: Mapped[str] = mapped_column(ForeignKey("companies.corp_code"), index=True)
    title: Mapped[str] = mapped_column(String)
    disclosure_type: Mapped[str | None] = mapped_column(String, nullable=True)
    filed_at: Mapped[str] = mapped_column(String, index=True)
    url: Mapped[str | None] = mapped_column(String, nullable=True)
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    event_started_at: Mapped[str | None] = mapped_column(String, nullable=True)
    original_rcept_no: Mapped[str | None] = mapped_column(String, nullable=True)
    structured_facts: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    analysis_ready_at: Mapped[str | None] = mapped_column(String, nullable=True)
    input_version: Mapped[str | None] = mapped_column(String, nullable=True)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    # 운영(RAG_INDEX_MODE=ephemeral): 처리가 끝나면 본문과 색인을 지우고 아래 기록만 남긴다.
    content_hash: Mapped[str | None] = mapped_column(String, nullable=True)  # 원문 sha256 (DART에서 다시 받았을 때 같은 문서인지 확인)
    processed_at: Mapped[str | None] = mapped_column(String, nullable=True)
    purged_at: Mapped[str | None] = mapped_column(String, nullable=True)  # 본문을 비운 시각


class Signal(Base):
    __tablename__ = "signals"

    signal_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    corp_code: Mapped[str] = mapped_column(ForeignKey("companies.corp_code"), index=True)
    rcept_no: Mapped[str] = mapped_column(String)
    event_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    event_started_at: Mapped[str | None] = mapped_column(String, nullable=True)
    signal_type: Mapped[str] = mapped_column(String)
    points: Mapped[float] = mapped_column(Float)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    filed_at: Mapped[str] = mapped_column(String)
