import json

import httpx
import pytest

from mailrambo import AsyncMailRambo, MailRambo, MailRamboError


def transport(responses, calls):
    def handler(request: httpx.Request):
        calls.append(request)
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        status, body = r[0], r[1]
        headers = r[2] if len(r) > 2 else {}
        return httpx.Response(status, json=body, headers=headers)
    return handler


def make(responses, **kw):
    calls = []
    client = MailRambo("mr_test_x", http_client=httpx.Client(transport=httpx.MockTransport(transport(responses, calls))), **kw)
    return client, calls


def test_requires_key(monkeypatch):
    monkeypatch.delenv("MAILRAMBO_API_KEY", raising=False)
    with pytest.raises(MailRamboError):
        MailRambo()


def test_verify_request_shape():
    mr, calls = make([(200, {"email": "a@b.co", "deliverable": True, "reason": "mailbox_exists", "credits_remaining": 9})])
    assert mr.verify("a@b.co")["deliverable"] is True
    req = calls[0]
    assert req.method == "POST" and str(req.url) == "https://www.mailrambo.com/v1/verify"
    assert req.headers["authorization"] == "Bearer mr_test_x"
    assert json.loads(req.content) == {"email": "a@b.co"}
    assert mr.is_test_mode


def test_detail_param():
    mr, calls = make([(200, {})])
    mr.verify("a@b.co", detail=True)
    assert calls[0].url.params["detail"] == "full"


def test_error_mapping():
    mr, _ = make([(402, {"error": "insufficient_credits", "message": "Out", "credits_remaining": 0})])
    with pytest.raises(MailRamboError) as e:
        mr.verify("a@b.co")
    assert e.value.status == 402 and e.value.code == "insufficient_credits" and e.value.body["credits_remaining"] == 0


def test_retry_keeps_idempotency_key(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    mr, calls = make([(503, {"error": "provider_unavailable"}), (202, {"batch_id": "b1"})])
    assert mr.create_batch(["a@b.co"], name="x")["batch_id"] == "b1"
    assert len(calls) == 2
    assert calls[0].headers["idempotency-key"] == calls[1].headers["idempotency-key"]
    assert json.loads(calls[0].content) == {"emails": ["a@b.co"], "name": "x"}


def test_no_retry_on_4xx():
    mr, calls = make([(401, {"error": "invalid_api_key", "message": "Unknown API key."})])
    with pytest.raises(MailRamboError):
        mr.account()
    assert len(calls) == 1


def test_network_error_retried_then_raised(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    boom = httpx.ConnectError("down")
    mr, calls = make([boom, boom, boom])
    with pytest.raises(MailRamboError) as e:
        mr.account()
    assert e.value.code == "network_error" and len(calls) == 3


def test_verify_many_chunks(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    emails = [f"u{i}@b.co" for i in range(250)]
    rows = lambda xs: [{"email": x, "deliverable": True, "reason": "mailbox_exists"} for x in xs]
    mr, calls = make([
        (202, {"batch_id": "b1"}), (200, {"status": "processing"}),
        (200, {"status": "completed", "results": rows(emails[:200])}),
        (202, {"batch_id": "b2"}), (200, {"status": "completed", "results": rows(emails[200:])}),
    ])
    out = mr.verify_many(emails, interval=0)
    assert [r["email"] for r in out] == emails
    assert len(json.loads(calls[0].content)["emails"]) == 200


@pytest.mark.asyncio
async def test_async_verify():
    calls = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(transport([(200, {"deliverable": False, "reason": "disposable"})], calls)))
    async with AsyncMailRambo("mr_test_x", http_client=client) as mr:
        r = await mr.verify("x@mailinator.com")
    assert r["reason"] == "disposable" and calls[0].method == "POST"
