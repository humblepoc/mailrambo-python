# mailrambo

Official Python client for the [MailRambo](https://www.mailrambo.com) email verification API.

One call answers one question - **can I send to this address without bouncing?** `deliverable` is `True` only when the mailbox is confirmed; catch-all, disposable and unverifiable addresses are `False`, with a `reason` you can branch on.

- Sync and asyncio clients, typed, one dependency (`httpx`)
- Automatic retries on 429/5xx with backoff
- Retry-safe batches (idempotency keys added for you)
- Free test keys (`mr_test_...`) - build and run CI without spending credits

## Install

```bash
pip install mailrambo
```

Get a free API key (50 verifications/month, API on every plan) at [mailrambo.com](https://www.mailrambo.com/auth/signup?src=pypi).

## Quick start

```python
from mailrambo import MailRambo

mr = MailRambo()  # reads MAILRAMBO_API_KEY

result = mr.verify("jane@acme.com")
if not result["deliverable"]:
    print("Don't send:", result["reason"])  # mailbox_not_found, disposable, catch_all, ...
```

## Async

```python
from mailrambo import AsyncMailRambo

async with AsyncMailRambo() as mr:
    result = await mr.verify("jane@acme.com")
```

## Block fake signups (Django / FastAPI)

```python
BLOCK = {"disposable", "mailbox_not_found", "invalid_syntax", "spamtrap"}

def is_allowed_signup(email: str) -> bool:
    try:
        return mr.verify(email)["reason"] not in BLOCK
    except MailRamboError:
        return True  # fail open: never lose a real user to a timeout
```

## Full detail

```python
r = mr.verify("jane@acme.com", detail=True)
r["detail"]["grade"]                 # "A"
r["detail"]["inbox_provider"]        # "Google Workspace"
r["detail"]["dns"]["dmarc_policy"]   # "reject"
```

Same price (1 credit).

## Lists

```python
rows = mr.verify_many(emails, name="Q4 import")   # any size, batches of 200, input order kept
good = [r["email"] for r in rows if r["deliverable"]]

# or manage batches yourself
batch_id = mr.create_batch(emails[:200], idempotency_key="import-42")["batch_id"]
batch = mr.wait_for_batch(batch_id)
```

## Credits

```python
mr.account()["credits_remaining"]  # free call
```

## Errors

Every API error raises `MailRamboError` with `status`, `code` and `message`:

```python
from mailrambo import MailRamboError

try:
    mr.verify(email)
except MailRamboError as e:
    if e.code == "insufficient_credits":
        ...  # top up at https://www.mailrambo.com/pricing
```

| status | code | meaning |
|---|---|---|
| 401 | `invalid_api_key`, `api_key_revoked` | Check your key |
| 402 | `insufficient_credits`, `subscription_inactive` | Top up or reactivate |
| 429 | `rate_limited` | Retried automatically, then raised |
| 503 | `provider_unavailable` | Retried automatically; charged credits are refunded |

## Test mode

Create a `mr_test_...` key on the [API Keys page](https://www.mailrambo.com/api-keys). No credits are used, and the answer depends on the part before the `@`:

```python
mr.verify("deliverable@example.com")  # True,  "mailbox_exists"
mr.verify("disposable@example.com")   # False, "disposable"
mr.verify("catch_all@example.com")    # False, "catch_all"
mr.verify("anything@example.com")     # False, "mailbox_not_found"
```

## Links

- [API reference](https://www.mailrambo.com/developers) · [OpenAPI spec](https://www.mailrambo.com/v1/openapi.json)
- [Free email & DNS tools](https://www.mailrambo.com/tools)
- [Pricing](https://www.mailrambo.com/pricing)

MIT License
