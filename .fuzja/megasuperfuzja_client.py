"""MEGASUPERFUZJA 1.1: authenticated read-only Cloudflare D1 gateway client.

This module does not fetch Superbet prices, place bets, or write to D1.
Never set MEGAFUZJA_READ_TOKEN in browser code or a public repository.
"""
from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ORIGIN = "https://megasuperfuzja-api.max-gollan.workers.dev"
ALLOWED_PATHS = frozenset(("/v1/overview", "/v1/projects", "/v1/catalog", "/v1/rules"))


class MegaFuzjaUnavailable(RuntimeError):
    """No approved credentials or the authenticated gateway cannot be read."""


class MegaFuzjaClient:
    def __init__(self, token: str | None = None, base_url: str | None = None, timeout: float = 8.0):
        self.base_url = (base_url or os.environ.get("MEGAFUZJA_API_URL") or ORIGIN).rstrip("/")
        if self.base_url != ORIGIN:
            raise ValueError("Gateway origin not allowlisted")
        self._token = token if token is not None else os.environ.get("MEGAFUZJA_READ_TOKEN", "")
        if not (0.1 <= float(timeout) <= 30):
            raise ValueError("Timeout outside safe range")
        self.timeout = float(timeout)

    def get(self, path: str, *, limit: int | None = None, category: str | None = None) -> dict[str, Any]:
        if path not in ALLOWED_PATHS:
            raise ValueError("Unsupported read-only API path")
        if len(self._token) < 32:
            raise MegaFuzjaUnavailable("MEGAFUZJA_READ_TOKEN is not configured")
        params: dict[str, str] = {}
        if path == "/v1/catalog":
            if limit is not None:
                if not (1 <= limit <= 500):
                    raise ValueError("limit must be 1..500")
                params["limit"] = str(limit)
            if category is not None:
                if category not in {"sports_project", "other_project", "site", "repository", "plugin", "skill", "skill_kody", "source", "competition_scope"}:
                    raise ValueError("Unsupported category")
                params["type"] = category
        elif limit is not None or category is not None:
            raise ValueError("Filtering only supported for /v1/catalog")
        url = self.base_url + path + (("?" + urlencode(params)) if params else "")
        request = Request(url, method="GET", headers={
            "Authorization": "Bearer " + self._token,
            "Accept": "application/json",
        })
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.load(response)
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            # Deliberately do not include credentials or raw upstream headers.
            raise MegaFuzjaUnavailable("Read-only gateway unavailable or denied") from exc

    def overview(self) -> dict[str, Any]:
        return self.get("/v1/overview")

    def projects(self) -> dict[str, Any]:
        return self.get("/v1/projects")

    def catalog(self, category: str | None = None, limit: int = 100) -> dict[str, Any]:
        return self.get("/v1/catalog", category=category, limit=limit)

    def rules(self) -> dict[str, Any]:
        return self.get("/v1/rules")
