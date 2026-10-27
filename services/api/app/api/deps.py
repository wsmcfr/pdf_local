import os

from fastapi import Header, HTTPException


def require_token(x_app_token: str | None = Header(default=None, alias="X-App-Token")) -> None:
    expected = os.environ.get("KB_TOKEN")
    if not expected or x_app_token != expected:
        raise HTTPException(status_code=401, detail="UNAUTHORIZED")
