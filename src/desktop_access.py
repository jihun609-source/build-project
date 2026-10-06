"""설치본이 여는 로컬 브라우저에 한 번만 로그인시키는 짧은 유효기간의 티켓."""
import hmac
import secrets
import threading
import time

_lock = threading.Lock()
_tickets = {}


def issue() -> str:
    ticket = secrets.token_urlsafe(32)
    with _lock:
        _tickets.clear()
        _tickets[ticket] = time.monotonic() + 60
    return ticket


def consume(ticket: str, peer: str) -> bool:
    if peer not in ("127.0.0.1", "::1") or not ticket.isascii():
        return False
    with _lock:
        for stored, deadline in list(_tickets.items()):
            if hmac.compare_digest(stored, ticket):
                del _tickets[stored]
                return time.monotonic() < deadline
    return False
