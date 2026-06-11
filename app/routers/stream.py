"""
Server-Sent Events endpoint.

Clients connect once and receive a live stream of new-article notifications
as the periodic Celery refresh task processes feeds.

Event format (JSON):
  {"type": "new_articles", "feed_id": 42, "count": 7}
  {"type": "ping"}           — keepalive every 25 s
"""
import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from ..auth import _decode_token
from ..database import get_db
from ..models import User
from ..services.events import event_stream

router = APIRouter(prefix="/stream", tags=["Stream"])
_bearer = HTTPBearer(auto_error=False)

_PING_INTERVAL = 25  # seconds


async def _generate(request: Request, user_id: int):
    """Merge the user's Redis event stream with periodic keepalive pings."""
    q: asyncio.Queue = asyncio.Queue()

    async def _feed_events():
        async for ev in event_stream(user_id):
            await q.put(ev)

    async def _pings():
        while True:
            await asyncio.sleep(_PING_INTERVAL)
            await q.put({"type": "ping"})

    feed_task = asyncio.create_task(_feed_events())
    ping_task = asyncio.create_task(_pings())
    try:
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(q.get(), timeout=1.0)
                yield {"data": json.dumps(event)}
            except asyncio.TimeoutError:
                continue
    finally:
        feed_task.cancel()
        ping_task.cancel()


def _get_sse_user(
    request: Request,
    token: str | None,
    credentials: HTTPAuthorizationCredentials | None,
    db: Session,
) -> User:
    """Authenticate for SSE — accepts Bearer header, X-API-Key header, or ?token= query param
    (EventSource browser API cannot set custom headers)."""
    api_key = request.headers.get("X-API-Key")
    if api_key:
        user = db.query(User).filter(User.api_token == api_key).first()
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API token")
        return user

    jwt_str = credentials.credentials if credentials else token
    if not jwt_str:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    payload = _decode_token(jwt_str)
    user_id = int(payload["sub"])
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if payload.get("token_version", 0) != user.token_version:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token revoked")
    return user


@router.get(
    "/articles",
    summary="SSE stream of new-article events",
    description=(
        "Opens a Server-Sent Events connection. "
        "The server pushes a JSON event whenever new articles arrive for any of your feeds:\n\n"
        "```\n"
        'data: {"type": "new_articles", "feed_id": 42, "count": 7}\n'
        'data: {"type": "search_alert", "alert_id": 1, "query": "rust async", "count": 2}\n'
        "```\n\n"
        "A `ping` event is sent every 25 seconds to keep the connection alive. "
        "Pass the JWT as `?token=<jwt>` when using the browser EventSource API (which cannot set headers)."
    ),
    response_class=EventSourceResponse,
)
async def article_stream(
    request: Request,
    token: str | None = Query(None),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
):
    current_user = _get_sse_user(request, token, credentials, db)
    return EventSourceResponse(_generate(request, current_user.id))
