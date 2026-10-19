"""인증 테스트 (client 준비는 conftest.py)."""
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from api.auth import JWT_ALGORITHM
from shared.config import get_settings
from tests.api.helpers import login


def test_login_success_returns_token(client):
    res = login(client)
    assert res.status_code == 200
    body = res.json()
    assert body["token_type"] == "bearer"
    assert body["pb"] == {"pb_id": "P01", "name": "김하늘"}
    payload = jwt.decode(body["access_token"], get_settings().JWT_SECRET, algorithms=[JWT_ALGORITHM])
    assert payload["sub"] == "P01"


def test_login_email_is_case_insensitive(client):
    assert login(client, email="  P01@Demo.Local ").status_code == 200


@pytest.mark.parametrize("email,password", [
    ("p01@demo.local", "wrong"),
    ("nobody@demo.local", "demo1234"),
])
def test_login_failure_same_message(client, email, password):
    res = login(client, email, password)
    assert res.status_code == 401
    assert res.json()["detail"] == "이메일 또는 비밀번호가 올바르지 않습니다."


def test_me_with_token(client):
    token = login(client, "p02@demo.local").json()["access_token"]
    res = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.json()["pb_id"] == "P02"


def test_me_without_token(client):
    assert client.get("/auth/me").status_code == 401


def test_me_with_bad_token(client):
    res = client.get("/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert res.status_code == 401


def test_me_with_expired_token(client):
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    token = jwt.encode({"sub": "P01", "exp": past}, get_settings().JWT_SECRET, algorithm=JWT_ALGORITHM)
    res = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401
    assert "만료" in res.json()["detail"]


def test_me_with_unknown_pb(client):
    token = jwt.encode(
        {"sub": "P99", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        get_settings().JWT_SECRET, algorithm=JWT_ALGORITHM,
    )
    res = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401
