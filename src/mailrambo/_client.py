"""Sync and async clients. Both share request building and error handling."""

import asyncio
import os
import time
import uuid
from typing import Any, Dict, List, Optional

import httpx

DEFAULT_BASE_URL = "https://www.mailrambo.com/v1"
RETRYABLE = {429, 502, 503, 504}
USER_AGENT = "mailrambo-python/0.1.0"


class MailRamboError(Exception):
    """Raised for every API error. `code` is machine-readable, e.g. "insufficient_credits"."""

    def __init__(self, status: int, code: str, message: str, body: Optional[dict] = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.body = body or {}

    def __repr__(self) -> str:
        return f"MailRamboError(status={self.status}, code={self.code!r}, message={self.message!r})"


class _Base:
    def __init__(self, api_key: Optional[str], base_url: str, timeout: float, max_retries: int):
        api_key = api_key or os.environ.get("MAILRAMBO_API_KEY")
        if not api_key:
            raise MailRamboError(0, "missing_api_key", "Pass api_key= or set MAILRAMBO_API_KEY.")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries

    @property
    def is_test_mode(self) -> bool:
        return self.api_key.startswith("mr_test_")

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json", "User-Agent": USER_AGENT}

    @staticmethod
    def _backoff(attempt: int, response: Optional[httpx.Response]) -> float:
        if response is not None:
            try:
                retry_after = float(response.headers.get("retry-after", ""))
                if retry_after > 0:
                    return retry_after
            except ValueError:
                pass
        return 0.5 * 2 ** attempt

    @staticmethod
    def _parse(response: httpx.Response) -> Any:
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.is_error:
            raise MailRamboError(response.status_code, data.get("error", "http_error"),
                                 data.get("message", f"HTTP {response.status_code}"), data)
        return data

    @staticmethod
    def _batch_request(emails: List[str], name: Optional[str], idempotency_key: Optional[str]):
        body: Dict[str, Any] = {"emails": list(emails)}
        if name:
            body["name"] = name
        return body, {"Idempotency-Key": idempotency_key or str(uuid.uuid4())}


class MailRambo(_Base):
    """Synchronous client.

    Args:
        api_key: defaults to the MAILRAMBO_API_KEY environment variable.
        timeout: seconds per request (default 30).
        max_retries: retries on 429, 5xx and network errors (default 2).
    """

    def __init__(self, api_key: Optional[str] = None, *, base_url: str = DEFAULT_BASE_URL,
                 timeout: float = 30.0, max_retries: int = 2, http_client: Optional[httpx.Client] = None):
        super().__init__(api_key, base_url, timeout, max_retries)
        self._http = http_client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _request(self, method: str, path: str, *, json=None, params=None, headers=None) -> Any:
        hdrs = {**self._headers(), **(headers or {})}
        for attempt in range(self.max_retries + 1):
            try:
                r = self._http.request(method, self.base_url + path, json=json, params=params, headers=hdrs)
            except httpx.TransportError as e:
                if attempt < self.max_retries:
                    time.sleep(self._backoff(attempt, None))
                    continue
                raise MailRamboError(0, "network_error", str(e)) from e
            if r.status_code in RETRYABLE and attempt < self.max_retries:
                time.sleep(self._backoff(attempt, r))
                continue
            return self._parse(r)

    def verify(self, email: str, *, detail: bool = False) -> dict:
        """Verify one address (1 credit; invalid syntax is free).

        Returns {"email", "deliverable", "reason", "credits_remaining"} and,
        with detail=True, a "detail" dict with grade, flags and DNS auth data.
        """
        return self._request("POST", "/verify", json={"email": email},
                             params={"detail": "full"} if detail else None)

    def create_batch(self, emails: List[str], *, name: Optional[str] = None,
                     idempotency_key: Optional[str] = None) -> dict:
        """Start a batch of up to 200 addresses. Retries never charge twice."""
        body, headers = self._batch_request(emails, name, idempotency_key)
        return self._request("POST", "/verify/batch", json=body, headers=headers)

    def get_batch(self, batch_id: str) -> dict:
        return self._request("GET", f"/verify/batch/{batch_id}")

    def wait_for_batch(self, batch_id: str, *, interval: float = 3.0, timeout: float = 600.0) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            batch = self.get_batch(batch_id)
            if batch.get("status") == "completed":
                return batch
            if time.monotonic() + interval > deadline:
                raise MailRamboError(0, "timeout", f"Batch {batch_id} did not complete in time.", batch)
            time.sleep(interval)

    def verify_many(self, emails: List[str], *, name: Optional[str] = None,
                    interval: float = 3.0, timeout: float = 600.0) -> List[dict]:
        """Verify any number of addresses in batches of 200; results keep input order."""
        emails = list(emails)
        results: List[dict] = []
        for i in range(0, len(emails), 200):
            created = self.create_batch(emails[i:i + 200], name=name)
            results.extend(self.wait_for_batch(created["batch_id"], interval=interval, timeout=timeout)["results"])
        return results

    def account(self) -> dict:
        """Plan, remaining credits and period end. Free."""
        return self._request("GET", "/account")


class AsyncMailRambo(_Base):
    """Asyncio client with the same methods as MailRambo."""

    def __init__(self, api_key: Optional[str] = None, *, base_url: str = DEFAULT_BASE_URL,
                 timeout: float = 30.0, max_retries: int = 2, http_client: Optional[httpx.AsyncClient] = None):
        super().__init__(api_key, base_url, timeout, max_retries)
        self._http = http_client or httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()

    async def _request(self, method: str, path: str, *, json=None, params=None, headers=None) -> Any:
        hdrs = {**self._headers(), **(headers or {})}
        for attempt in range(self.max_retries + 1):
            try:
                r = await self._http.request(method, self.base_url + path, json=json, params=params, headers=hdrs)
            except httpx.TransportError as e:
                if attempt < self.max_retries:
                    await asyncio.sleep(self._backoff(attempt, None))
                    continue
                raise MailRamboError(0, "network_error", str(e)) from e
            if r.status_code in RETRYABLE and attempt < self.max_retries:
                await asyncio.sleep(self._backoff(attempt, r))
                continue
            return self._parse(r)

    async def verify(self, email: str, *, detail: bool = False) -> dict:
        return await self._request("POST", "/verify", json={"email": email},
                                   params={"detail": "full"} if detail else None)

    async def create_batch(self, emails: List[str], *, name: Optional[str] = None,
                           idempotency_key: Optional[str] = None) -> dict:
        body, headers = self._batch_request(emails, name, idempotency_key)
        return await self._request("POST", "/verify/batch", json=body, headers=headers)

    async def get_batch(self, batch_id: str) -> dict:
        return await self._request("GET", f"/verify/batch/{batch_id}")

    async def wait_for_batch(self, batch_id: str, *, interval: float = 3.0, timeout: float = 600.0) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            batch = await self.get_batch(batch_id)
            if batch.get("status") == "completed":
                return batch
            if time.monotonic() + interval > deadline:
                raise MailRamboError(0, "timeout", f"Batch {batch_id} did not complete in time.", batch)
            await asyncio.sleep(interval)

    async def verify_many(self, emails: List[str], *, name: Optional[str] = None,
                          interval: float = 3.0, timeout: float = 600.0) -> List[dict]:
        emails = list(emails)
        results: List[dict] = []
        for i in range(0, len(emails), 200):
            created = await self.create_batch(emails[i:i + 200], name=name)
            batch = await self.wait_for_batch(created["batch_id"], interval=interval, timeout=timeout)
            results.extend(batch["results"])
        return results

    async def account(self) -> dict:
        return await self._request("GET", "/account")
