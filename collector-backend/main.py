"""Private API for the Underdogs FUZJA collector.

The service accepts only public HTTPS pages, extracts visible text with
Playwright, parses explicit sports markup locally, and returns JSON or CSV.
"""
import asyncio
import csv
import io
import json
import ipaddress
import hmac
import os
import socket
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup
from free_parser import parse_public_html
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import Response
from playwright.async_api import async_playwright
from pydantic import BaseModel, Field, HttpUrl

APP_TOKEN = os.environ.get("COLLECTOR_API_TOKEN", "")
MAX_PAGES = max(1, min(4, int(os.environ.get("MAX_PAGES_PER_RUN", "4"))))
ALLOWED_HOSTS = {"superscore.live", "soccerstats.com", "www.soccerstats.com", "statmuse.com", "www.statmuse.com"}
USER_AGENT = "UnderdogsFUZJA/0.2"
collect_lock = asyncio.Lock()
app = FastAPI(title="Underdogs FUZJA Collector", version="0.2.0")


class CollectRequest(BaseModel):
    base_url: HttpUrl
    user_goal: str = Field(min_length=5, max_length=500)
    max_depth: int = Field(default=1, ge=0, le=2)


def require_token(x_collector_token: str = Header(default="")):
    if not APP_TOKEN or not hmac.compare_digest(x_collector_token, APP_TOKEN):
        raise HTTPException(status_code=401, detail="Unauthorized")


def public_https_url(raw_url: str) -> str:
    parsed = urlparse(raw_url)
    if (parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise HTTPException(status_code=400, detail="Only approved public HTTPS hosts are allowed")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, 443)}
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global:
                raise HTTPException(status_code=400, detail="Private network targets are blocked")
    except socket.gaierror as exc:
        raise HTTPException(status_code=503, detail="DNS resolution failed; retry later") from exc
    return raw_url


def visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style", "nav", "footer", "header", "form", "noscript"]):
        node.decompose()
    return " ".join(soup.get_text(" ").split())[:12000]


def extract(text: str, url: str, goal: str) -> dict:
    """Free deterministic extraction; raw text is not a verified sports record."""
    if not text.strip():
        return {}
    return {"visible_text": text[:12000], "extraction_status": "raw_text_only",
            "structured_data_verified": False, "paid_api_used": False}

@app.get("/health")
def health():
    return {"status": "ok", "model": None, "mode": "free_structured_markup",
            "paid_api_used": False, "time": datetime.now(timezone.utc).isoformat()}

@app.post("/collect", dependencies=[Depends(require_token)])
async def collect(request: CollectRequest):
    base_url = await asyncio.to_thread(public_https_url, str(request.base_url))
    base_host = urlparse(base_url).hostname
    queue, visited, records, errors = [(base_url, 0)], set(), [], []
    async with collect_lock:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                context = await browser.new_context(viewport={"width": 1280, "height": 800},
                    user_agent=USER_AGENT, service_workers="block")
                async def route_request(route):
                    target = urlparse(route.request.url)
                    if (target.scheme != "https" or target.hostname != base_host
                            or target.port not in (None, 443)
                            or route.request.resource_type in {"image", "media", "font"}):
                        await route.abort()
                    else:
                        await route.continue_()
                await context.route("**/*", route_request)
                page = await context.new_page()
                robots_url = f"https://{base_host}/robots.txt"
                robots = RobotFileParser(robots_url)
                try:
                    response = await page.goto(robots_url, wait_until="domcontentloaded", timeout=12000)
                    if response is None:
                        raise HTTPException(503, "Robots policy unavailable")
                    if response.status in (404, 410):
                        robots.parse(["User-agent: *", "Disallow:"])
                    elif response.ok:
                        robots.parse((await response.text()).splitlines())
                    else:
                        raise HTTPException(503, "Robots policy unavailable or access restricted")
                except HTTPException:
                    raise
                except Exception as exc:
                    raise HTTPException(503, "Robots policy unavailable") from exc
                while queue and len(visited) < MAX_PAGES:
                    current, depth = queue.pop(0)
                    if current in visited or depth > request.max_depth:
                        continue
                    current = await asyncio.to_thread(public_https_url, current)
                    visited.add(current)
                    if not robots.can_fetch(USER_AGENT, current):
                        errors.append({"url": current, "reason": "robots_disallowed"})
                        continue
                    try:
                        response = await page.goto(current, wait_until="domcontentloaded", timeout=20000)
                        if response is None or not response.ok or not robots.can_fetch(USER_AGENT, page.url):
                            errors.append({"url": current, "reason": "source_unavailable_or_restricted"})
                            continue
                        html = await page.content()
                        data = extract(visible_text(html), current, request.user_goal)
                        parsed = parse_public_html(html, page.url)
                        data.update(parsed)
                        if data.get("visible_text") or data["events"] or data["tables"]:
                            data.update(source_url=page.url, scraped_at=datetime.now(timezone.utc).isoformat())
                            records.append(data)
                        if depth < request.max_depth:
                            soup = BeautifulSoup(html, "html.parser")
                            for tag in soup.find_all("a", href=True, limit=100):
                                target = urljoin(page.url, tag["href"])
                                parsed = urlparse(target)
                                if parsed.scheme == "https" and parsed.hostname == base_host and target not in visited:
                                    if len(queue) < 20:
                                        queue.append((target, depth + 1))
                    except Exception:
                        errors.append({"url": current, "reason": "read_failed"})
            finally:
                await browser.close()
    return {"visited": len(visited), "records": records, "errors": errors,
            "mode": "free_raw_text", "structured_data_verified": False, "paid_api_used": False}

@app.post("/collect.csv", dependencies=[Depends(require_token)])
async def collect_csv(request: CollectRequest):
    result = await collect(request)
    rows = result["records"]
    fields = sorted({key for row in rows for key in row})
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows([{k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for k, v in row.items()} for row in rows])
    return Response(stream.getvalue(), media_type="text/csv; charset=utf-8")
