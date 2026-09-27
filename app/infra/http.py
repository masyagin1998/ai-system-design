"""Внешние HTTP-вызовы с ретраями (tenacity).

resp = http.post_json("https://partner/webhook", {"event": "paid"})
"""

from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

client = httpx.Client(timeout=5)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=0.5, max=5), reraise=True)
def post_json(url: str, data: Any) -> httpx.Response:
    resp = client.post(url, json=data)
    resp.raise_for_status()  # 4xx/5xx → исключение → ретрай
    return resp
