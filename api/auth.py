"""로그인, JWT 발급·검증 (CLAUDE.md 1장 인증, 8장 /auth/login)."""
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.deps import get_db
from shared.config import get_settings
from shared.models import PB

JWT_ALGORITHM = "HS256"
TOKEN_TTL = timedelta(hours=12)  # 데모 하루 동안 다시 로그인하지 않게

router = APIRouter(prefix="/auth", tags=["auth"])
_bearer = HTTPBearer(auto_error=False)


# ---- 비밀번호 ----
def hash_password(password: str) -> str:
    """seed_db.py도 이 함수를 쓰면 해시 방식이 하나로 맞는다."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:  # 72바이트 초과 비밀번호, 깨진 해시
        return False


# ---- 토큰 ----
class CurrentPB(BaseModel):
    pb_id: str
    name: str


def create_access_token(pb: PB) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": pb.pb_id, "name": pb.name, "iat": now, "exp": now + TOKEN_TTL}
    return jwt.encode(payload, get_settings().JWT_SECRET, algorithm=JWT_ALGORITHM)


def _unauthorized(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=message,
        headers={"WWW-Authenticate": "Bearer"},
    )


def decode_token(token: str, db: Session) -> CurrentPB:
    """토큰을 검사하고 DB에 있는 PB인지 확인한다. SSE처럼 헤더를 못 쓰는 경로에서도 부른다."""
    try:
        payload = jwt.decode(token, get_settings().JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise _unauthorized("토큰이 만료되었습니다. 다시 로그인하세요.")
    except jwt.InvalidTokenError:
        raise _unauthorized("유효하지 않은 토큰입니다.")

    pb = db.get(PB, payload.get("sub"))
    if pb is None:
        raise _unauthorized("존재하지 않는 PB입니다.")
    return CurrentPB(pb_id=pb.pb_id, name=pb.name)


def get_current_pb(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> CurrentPB:
    """보호할 경로에 `pb: CurrentPB = Depends(get_current_pb)`로 붙인다."""
    if credentials is None:
        raise _unauthorized("로그인이 필요합니다.")
    return decode_token(credentials.credentials, db)


def get_current_pb_sse(
    token: str | None = None,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> CurrentPB:
    """SSE용. 브라우저 EventSource는 헤더를 못 보내므로 `?token=`도 받는다."""
    raw = credentials.credentials if credentials is not None else token
    if not raw:
        raise _unauthorized("로그인이 필요합니다.")
    return decode_token(raw, db)


# ---- 경로 ----
class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    pb: CurrentPB


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    pb = db.scalar(select(PB).where(PB.email == body.email.strip().lower()))
    # 이메일이 없을 때와 비밀번호가 틀릴 때 같은 메시지 (계정 존재 여부를 알려주지 않음)
    if pb is None or not verify_password(body.password, pb.password_hash):
        raise _unauthorized("이메일 또는 비밀번호가 올바르지 않습니다.")
    return LoginResponse(
        access_token=create_access_token(pb),
        pb=CurrentPB(pb_id=pb.pb_id, name=pb.name),
    )


@router.get("/me", response_model=CurrentPB)
def me(pb: CurrentPB = Depends(get_current_pb)) -> CurrentPB:
    """화면 새로고침 때 저장된 토큰이 아직 유효한지 확인하는 용도."""
    return pb
