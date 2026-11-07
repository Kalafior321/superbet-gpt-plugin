"""Private API for the Underdogs FUZJA collector.

The service accepts only public HTTPS pages, extracts visible text with
Playwright, asks OpenAI for structured data, and returns JSON or CSV.
"""
import asyncio
import csv
import io
import ipaddress
import json
import os
import socket
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import Response
from openai import OpenAI
from playwright.async_api import async_playwright
from pydantic import BaseModel, Field, HttpUrl

APP_TOKEN = os.environ.get("COLLECTOR_API_TOKEN", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
MAX_PAGES = int(os.environ.get("MAX_PAGES_PER_RUN", "12"))
client = OpenAI()
app = FastAPI(title="Underdogs FUZJA Collector", version="0.1.0")


class CollectRequest(BaseModel):
    base_url: HttpUrl
    user_goal: str = Field(min_length=5, max_length=500)
    max_depth: int = Field(default=1, ge=0, le=2)


def require_token(x_collector_token: str = Header(default="")):
    if not APP_TOKEN or x_collector_token != APP_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


def public_https_url(raw_url: str) -> str:
    parsed = urlparse(raw_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise HTTPException(status_code=400, detail="Only public HTTPS URLs are allowed")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, 443)}
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global:
                raise HTTPException(status_code=400, detail="Private network targets are blocked")
    except socket.gaierror as exc:
        raise HTTPException(status_code=400, detail="Host cannot be resolved") from exc
    return raw_url


def visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style", "nav", "footer", "header", "form", "noscript"]):
        node.decompose()
    return " ".join(soup.get_text(" ").split())[:12000]


def extract(text: str, url: str, goal: str) -> dict:
    if not text:
        return {}
    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": "Extract only factual data relevant to the goal. Return one flat JSON object; use {} when nothing is relevant."},
            {"role": "user", "content": f"Goal: {goal}\nURL: {url}\nPage text:\n{text}"},
        ],
        temperature=0,
    )
    return json.loads(response.choices[0].message.content or "{}")


@app.get("/health")
def health():
    return {"status": "ok", "model": OPENAI_MODEL, "time": datetime.now(timezone.utc).isoformat()}


@app.post("/collect", dependencies=[Depends(require_token)])
async def collect(request: CollectRequest):
    base_url = public_https_url(str(request.base_url))
    base_host = urlparse(base_url).hostname
    queue, visited, records = [(base_url, 0)], set(), []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 800})
        try:
            while queue and len(visited) < MAX_PAGES:
                current, depth = queue.pop(0)
                if current in visited or depth > request.max_depth:
                    continue
                current = public_https_url(current)
                visited.add(current)
                try:
                    await page.goto(current, wait_until="domcontentloaded", timeout=20000)
                    html = await page.content()
                    data = await asyncio.to_thread(extract, visible_text(html), current, request.user_goal)
                    if data:
                        data.update(source_url=current, scraped_at=datetime.now(timezone.utc).isoformat())
                        records.append(data)
                    if depth < request.max_depth:
                        soup = BeautifulSoup(html, "html.parser")
                        for tag in soup.find_all("a", href=True):
                            target = urljoin(current, tag["href"])
                            parsed = urlparse(target)
                            if parsed.scheme == "https" and parsed.hostname == base_host and target not in visited:
                                queue.append((target, depth + 1))
                except Exception:
                    continue
        finally:
            await browser.close()
    return {"visited": len(visited), "records": records}


@app.post("/collect.csv", dependencies=[Depends(require_token)])
async def collect_csv(request: CollectRequest):
    result = await collect(request)
    rows = result["records"]
    fields = sorted({key for row in rows for key in row})
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return Response(stream.getvalue(), media_type="text/csv; charset=utf-8")
