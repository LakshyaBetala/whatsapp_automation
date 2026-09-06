"""The DB session must survive a connection dying under it.

postgrest-py hardcodes http2=True, so every database call in the process shares
ONE HTTP/2 connection. Once route handlers moved into FastAPI's threadpool, that
single connection carried ~40 threads and Supabase's GOAWAY started killing
in-flight requests - which reached the shop as "Server said 500".
"""
import httpx
import pytest

from app.db import _ResilientTransport


class _Flaky(_ResilientTransport):
    """Fails `fail_times` then succeeds, counting every attempt."""

    def __init__(self, fail_times, exc=httpx.RemoteProtocolError("terminated")):
        self.calls = 0
        self.fail_times = fail_times
        self.exc = exc

    def handle_request(self, request):
        return _ResilientTransport.handle_request(self, request)

    def _send(self, request):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc
        return httpx.Response(200)


@pytest.fixture(autouse=True)
def _no_super(monkeypatch):
    # Route the parent's real network call into the fake above.
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request",
                        lambda self, request: self._send(request))


def _req(method):
    return httpx.Request(method, "https://example.supabase.co/rest/v1/businesses")


def test_read_is_retried_until_it_succeeds():
    t = _Flaky(fail_times=2)
    assert t.handle_request(_req("GET")).status_code == 200
    assert t.calls == 3           # two failures, then the good one


def test_read_gives_up_after_the_retry_budget():
    t = _Flaky(fail_times=99)
    with pytest.raises(httpx.RemoteProtocolError):
        t.handle_request(_req("GET"))
    assert t.calls == 3           # bounded, never an infinite loop


def test_writes_are_never_replayed():
    # A retried INSERT can double-post a payment. Writes must fail loudly.
    for method in ("POST", "PATCH", "DELETE", "PUT"):
        t = _Flaky(fail_times=1)
        with pytest.raises(httpx.RemoteProtocolError):
            t.handle_request(_req(method))
        assert t.calls == 1, method


def test_a_healthy_read_is_not_retried():
    t = _Flaky(fail_times=0)
    assert t.handle_request(_req("GET")).status_code == 200
    assert t.calls == 1
