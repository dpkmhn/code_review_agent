"""Shared workspace access control; the HTTPS gateway authenticates people."""

import secrets

from fastapi import HTTPException, Request

from app.config import settings


async def require_access(request: Request):
    if settings.api_access_token is None:
        return  # Local development only; production settings require a token.
    expected = "Bearer " + settings.api_access_token.get_secret_value()
    if not secrets.compare_digest(request.headers.get("authorization", "").encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="API authentication required",
                            headers={"WWW-Authenticate": "Bearer"})
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if (request.headers.get("sec-fetch-site") == "cross-site"
                or (origin is not None and origin != settings.public_origin)):
            raise HTTPException(status_code=403, detail="Cross-origin requests are not allowed")
