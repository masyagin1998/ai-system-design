"""JWT для MVP: регистрация и вход по email + пароль.

POST /api/v1/auth/register {"email", "password"} → 201 {id, email}; email занят → 409
POST /api/v1/auth/token    {"email", "password"} → access_token; неверно → 401
Защитить ручку: добавить параметр `user_id: CurrentUser` (заголовок Authorization: Bearer <token>).
"""

import hashlib
import hmac
import os
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db import SessionDep
from app.models import User
from app.settings import settings

router = APIRouter(prefix="/auth", tags=["auth"])


class Credentials(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+$", max_length=255)
    password: str = Field(min_length=6, max_length=100)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"{salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    salt, digest = stored.split("$")
    new = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
    return hmac.compare_digest(new.hex(), digest)


@router.post("/register", status_code=201)
def register(data: Credentials, session: SessionDep) -> dict[str, int | str]:
    user = User(email=data.email.lower(), password_hash=hash_password(data.password))
    session.add(user)
    session.commit()  # email занят → IntegrityError → 409 (обработчик в app/main.py)
    return {"id": user.id, "email": user.email}


@router.post("/token")
def issue_token(data: Credentials, session: SessionDep) -> dict[str, str]:
    user = session.scalar(select(User).where(User.email == data.email.lower()))
    if user is None or not check_password(data.password, user.password_hash):
        raise HTTPException(401, "invalid email or password")
    token = jwt.encode({"sub": str(user.id)}, settings.jwt_secret, algorithm="HS256")
    return {"access_token": token, "token_type": "bearer"}


def current_user(creds: Annotated[HTTPAuthorizationCredentials, Depends(HTTPBearer())]) -> int:
    try:
        return int(jwt.decode(creds.credentials, settings.jwt_secret, algorithms=["HS256"])["sub"])
    except jwt.PyJWTError:
        raise HTTPException(401, "invalid token") from None


CurrentUser = Annotated[int, Depends(current_user)]


@router.get("/me")
def me(user_id: CurrentUser) -> dict[str, int]:
    return {"user_id": user_id}
