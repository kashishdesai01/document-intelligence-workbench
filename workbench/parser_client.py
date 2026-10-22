import json
import os
import struct

import httpx

from workbench.models import Page
from workbench.parsing import ERROR_CODES, ParseFailure, invoke

MAX_RESPONSE = 8 * 1024 * 1024


def parser_healthy() -> bool:
    endpoint = os.getenv("PARSER_URL")
    if not endpoint:
        return True
    try:
        with httpx.Client(base_url=endpoint, timeout=2, trust_env=False) as client:
            return client.get("/healthz").status_code == 200
    except httpx.HTTPError:
        return False


def request(source: bytes, page: int) -> tuple[dict, bytes | None]:
    endpoint = os.getenv("PARSER_URL")
    if not endpoint:
        return invoke(source, page or None)
    try:
        with httpx.Client(base_url=endpoint, timeout=105, trust_env=False) as client:
            with client.stream("POST", f"/parse/{page}", content=source) as response:
                body = bytearray()
                for chunk in response.iter_bytes():
                    if len(body) + len(chunk) > MAX_RESPONSE:
                        raise ParseFailure("parser_failed")
                    body.extend(chunk)
                if response.status_code == 422:
                    code = json.loads(body).get("error")
                    raise ParseFailure(code if code in ERROR_CODES else "parser_failed")
                response.raise_for_status()
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        raise ParseFailure("parser_failed") from exc
    if not page:
        return json.loads(body), None
    if len(body) < 4:
        raise ParseFailure("parser_failed")
    (length,) = struct.unpack(">I", body[:4])
    if not 4 + length < len(body):
        raise ParseFailure("parser_failed")
    return json.loads(body[4 : 4 + length]), bytes(body[4 + length :])


def preflight(source: bytes) -> int:
    data, _ = request(source, 0)
    return data["pages"]


def parse_page(source: bytes, number: int) -> tuple[Page, bytes]:
    data, image = request(source, number)
    if image is None:
        raise ParseFailure("parser_failed")
    return Page.model_validate(data), image
