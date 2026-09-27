"""Lightweight in-process analysis progress registry.

Callers obtain a token, report named stages, and expose the current stage
via a polling endpoint.  The registry is a plain dict protected by a lock;
it holds at most MAX_ENTRIES entries and evicts the oldest on overflow.

Stage names are free-form strings intended to be human-readable in the UI.
A terminal stage of 'done' or 'error' signals completion.

Usage (route layer)::

    token = new_token()
    # pass token to the background worker so it can call report(token, stage)
    ...
    report(token, 'done')

Usage (service layer)::

    from services.progress import report
    report(token, 'Retrieving elevation…')

If token is None or unknown, report() is a silent no-op.
"""

import secrets
import threading
import time

_LOCK   = threading.Lock()
_STATE  = {}          # token → {'stage': str, 'updated': float}
_ORDER  = []          # insertion order for eviction
MAX_ENTRIES = 256
TTL     = 600         # seconds; stale entries are ignored by the client


def new_token() -> str:
    """Return a fresh URL-safe token and register it as 'queued'."""
    token = secrets.token_urlsafe(16)
    with _LOCK:
        _register(token, 'queued')
    return token


def report(token: str | None, stage: str) -> None:
    """Update the stage for *token*.  Silent no-op if token is None."""
    if token is None:
        return
    with _LOCK:
        if token in _STATE:
            _STATE[token] = {'stage': stage, 'updated': time.monotonic()}
        else:
            _register(token, stage)


def get_stage(token: str) -> dict | None:
    """Return {'stage': str, 'updated': float} or None if unknown/expired."""
    with _LOCK:
        entry = _STATE.get(token)
        if entry is None:
            return None
        if time.monotonic() - entry['updated'] > TTL:
            _STATE.pop(token, None)
            try:
                _ORDER.remove(token)
            except ValueError:
                pass
            return None
        return dict(entry)


def _register(token: str, stage: str) -> None:
    """Insert a new token; evict oldest if at capacity.  Must hold _LOCK."""
    while len(_ORDER) >= MAX_ENTRIES:
        oldest = _ORDER.pop(0)
        _STATE.pop(oldest, None)
    _STATE[token] = {'stage': stage, 'updated': time.monotonic()}
    _ORDER.append(token)
