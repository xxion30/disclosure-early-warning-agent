"""회사(증권사)별 데이터: PB·고객·보유 종목. 회사 단위 분리(tenant_id)는 이후 단계에서 여기에 추가한다. (담당: R4)"""
from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from shared.models.base import Base


class PB(Base):
    __tablename__ = "pbs"

    tenant_id: Mapped[str] = mapped_column(String, default='default', server_default='default', index=True)
    pb_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    email: Mapped[str] = mapped_column(String, unique=True)
    role: Mapped[str] = mapped_column(String, default='PB', server_default='PB')
    password_hash: Mapped[str] = mapped_column(String)


class Customer(Base):
    __tablename__ = "customers"

    tenant_id: Mapped[str] = mapped_column(String, default='default', server_default='default', index=True)
    customer_id: Mapped[str] = mapped_column(String, primary_key=True)
    pb_id: Mapped[str] = mapped_column(ForeignKey("pbs.pb_id"), index=True)
    name: Mapped[str] = mapped_column(String)
    risk_profile: Mapped[str] = mapped_column(String)
    total_assets: Mapped[int] = mapped_column(Integer)


class Holding(Base):
    __tablename__ = "holdings"

    tenant_id: Mapped[str] = mapped_column(String, default='default', server_default='default', index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.customer_id"), primary_key=True)
    stock_code: Mapped[str] = mapped_column(String, primary_key=True)
    eval_amount: Mapped[int] = mapped_column(Integer)
    on_credit: Mapped[bool] = mapped_column(Boolean, default=False)
