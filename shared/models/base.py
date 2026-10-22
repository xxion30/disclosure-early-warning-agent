"""모든 테이블이 공유하는 선언 베이스. (담당: R4)"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
