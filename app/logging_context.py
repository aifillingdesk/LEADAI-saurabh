"""
LeadAI Request Tracing & Sentry-Compatible Error Reporting (Phase 4).

Provides:
- ContextVar-based request ID tracing across async tasks and log messages.
- RequestIdFilter for logging formatters.
- Sentry-compatible error capture hook.
"""
import contextvars
import logging
import os
import time
import uuid
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Context variable storing the current request ID
_request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


def get_current_request_id() -> str:
    """Return the active request ID or '-' if outside an HTTP request."""
    return _request_id_ctx.get()


def set_current_request_id(req_id: str) -> contextvars.Token:
    """Set the active request ID in context."""
    return _request_id_ctx.set(req_id)


class RequestIdFilter(logging.Filter):
    """Logging filter that attaches request_id to each log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_current_request_id()
        return True


# Requests slower than this are logged (SLOW_REQUEST_MS, default 1000 ms) so a
# slow page can be traced to its endpoint in the host's logs.
try:
    _SLOW_REQUEST_MS = float(os.getenv("SLOW_REQUEST_MS", "1000"))
except ValueError:
    _SLOW_REQUEST_MS = 1000.0
_slow_log = logging.getLogger("app.slow_requests")


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Middleware that assigns a unique request ID to each HTTP request.

    - Reads existing incoming 'X-Request-ID' header or generates a 32-character hex UUID.
    - Sets context variable for logging and sets request.state.request_id.
    - Echoes 'X-Request-ID' in the outgoing HTTP response headers.
    """

    async def dispatch(self, request: Request, call_next):
        req_id = request.headers.get("x-request-id")
        if not req_id or not req_id.strip():
            req_id = uuid.uuid4().hex
        else:
            # Sanitize client-supplied ID (alphanumeric and hyphens only, max 64 chars)
            req_id = "".join(c for c in req_id if c.isalnum() or c in "-_")[:64]
            if not req_id:
                req_id = uuid.uuid4().hex

        token = set_current_request_id(req_id)
        request.state.request_id = req_id
        started = time.perf_counter()

        try:
            response: Response = await call_next(request)
            response.headers["X-Request-ID"] = req_id
            ms = (time.perf_counter() - started) * 1000
            # visible in the browser's Network tab (Timing) for every request
            response.headers["Server-Timing"] = f"app;dur={ms:.0f}"
            if ms >= _SLOW_REQUEST_MS and request.url.path != "/health":
                _slow_log.warning("slow request %s %s -> %s in %.0f ms", request.method, request.url.path,
                                  response.status_code, ms)
            return response
        finally:
            _request_id_ctx.reset(token)


def capture_exception(exc: Exception, *, tags: dict[str, Any] | None = None, extra: dict[str, Any] | None = None) -> str | None:
    """Capture an exception to Sentry if configured, otherwise log it cleanly.

    Returns the Sentry event ID if captured, or None.
    """
    logger = logging.getLogger("app.errors")
    req_id = get_current_request_id()

    sentry_dsn = os.environ.get("SENTRY_DSN")
    if sentry_dsn:
        try:
            import sentry_sdk
            with sentry_sdk.push_scope() as scope:
                if req_id and req_id != "-":
                    scope.set_tag("request_id", req_id)
                if tags:
                    for k, v in tags.items():
                        scope.set_tag(k, str(v))
                if extra:
                    for k, v in extra.items():
                        scope.set_extra(k, v)
                return sentry_sdk.capture_exception(exc)
        except ImportError:
            logger.debug("Sentry SDK not installed; skipping external reporting.")
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to report error to Sentry: %s", e)

    return None
