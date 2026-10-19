"""Access control: one bearer token per installation.

The token comes from QUANTLAB_API_TOKEN, or is generated once and kept in data_dir/api_token. The web page
receives it through the start-up link (?token=...) and sends it as an Authorization header."""
import hmac
import os
import secrets
from functools import lru_cache

from fastapi import Header, HTTPException, Request

from ..config import get_settings


@lru_cache
def api_token():
    s = get_settings()
    if s.api_token:
        return s.api_token
    path = s.data_dir / "api_token"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # owner-only where the OS supports it
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(token)
    return token


def require_token(request: Request, authorization: str | None = Header(default=None)):
    """Every API call except /api/health. Token mode checks the bearer token; proxy mode trusts the user header
    that nginx sets after the ledger confirmed the session, and asks state-changing calls for a same-site header."""
    s = get_settings()
    if s.auth_mode == "proxy":
        if not request.headers.get(s.user_header, "").strip():
            raise HTTPException(401, "请先登录美股账本")
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("X-Requested-With") != "quantlab":
            raise HTTPException(403, "请求来源不对")
        return
    got = authorization[7:].strip() if authorization and authorization[:7].lower() == "bearer " else ""
    if not hmac.compare_digest(got.encode(), api_token().encode()):
        raise HTTPException(401, "需要有效的访问令牌，请用启动时显示的链接打开", headers={"WWW-Authenticate": "Bearer"})
