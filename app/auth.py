import hmac
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from .models import User

_bearer = HTTPBearer(auto_error=False)


# ── JWT ───────────────────────────────────────────────────────────────────────

def create_access_token(user_id: int, email: str, token_version: int = 0) -> tuple[str, int]:
    """Returns (encoded_jwt, expires_in_seconds)."""
    expire_seconds = settings.jwt_expire_minutes * 60
    expire = datetime.now(timezone.utc) + timedelta(seconds=expire_seconds)
    payload = {"sub": str(user_id), "email": email, "token_version": token_version, "exp": expire}
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, expire_seconds


def _decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── Dependencies ──────────────────────────────────────────────────────────────

def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    # Accept X-API-Key header as an alternative to Bearer JWT
    api_key = request.headers.get("X-API-Key")
    if api_key:
        user = db.query(User).filter(User.api_token == api_key).first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return user

    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = _decode_token(credentials.credentials)
    user_id = int(payload["sub"])
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    token_ver = payload.get("token_version", 0)
    if token_ver != user.token_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked — please sign in again",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def admin_emails() -> set[str]:
    return {e.strip().lower() for e in settings.admin_emails.split(",") if e.strip()}


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.email.lower() not in admin_emails():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user


# ── OAuth state (CSRF protection + optional frontend-origin embedding) ──────────
#
# Format: "{nonce}|{frontend_origin}.{hmac_sig}"
# The frontend_origin is embedded so the callback can redirect back to wherever
# the user started the flow (supports multiple Vercel deployments / localhost).

def generate_oauth_state(frontend_origin: str = "") -> str:
    nonce = secrets.token_urlsafe(24)
    payload = f"{nonce}|{frontend_origin}"
    sig = hmac.new(settings.jwt_secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_oauth_state(state: str) -> bool:
    try:
        payload, sig = state.rsplit(".", 1)
        expected = hmac.new(settings.jwt_secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected)
    except Exception as exc:
        logger.debug("OAuth state verification failed: %s", exc)
        return False


def extract_frontend_origin_from_state(state: str) -> str | None:
    """Return the frontend origin embedded in the state, or None if not present."""
    try:
        payload, _ = state.rsplit(".", 1)
        _, origin = payload.split("|", 1)
        return origin if origin else None
    except Exception:
        return None
