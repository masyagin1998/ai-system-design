"""Внешние HTTP-вызовы с ретраями (tenacity).

resp = http.post_json("https://partner/webhook", {"event": "paid"}, {"Idempotency-Key": key})

Повторяются только сетевые ошибки, 429 и 5xx; 4xx — сразу исключение. После таймаута партнёр
мог уже обработать запрос, поэтому для неидемпотентных вызовов передавай Idempotency-Key.
"""

from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

client = httpx.Client(timeout=5)


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


@retry(
    retry=retry_if_exception(_retryable),
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=0.5, max=5),
    reraise=True,
)
def post_json(url: str, data: Any, headers: dict[str, str] | None = None) -> httpx.Response:
    resp = client.post(url, json=data, headers=headers)
    resp.raise_for_status()
    return resp
