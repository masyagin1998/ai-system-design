"""Тупейший JWT для MVP: POST /api/auth/token {"user_id": 1} → access_token.

Защитить ручку: добавить параметр `user_id: CurrentUser` (заголовок Authorization: Bearer <token>).
"""

from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.settings import settings

router = APIRouter(prefix="/auth", tags=["auth"])


class TokenIn(BaseModel):
    user_id: int


@router.post("/token")
def issue_token(data: TokenIn) -> dict[str, str]:
    token = jwt.encode({"sub": str(data.user_id)}, settings.jwt_secret, algorithm="HS256")
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
