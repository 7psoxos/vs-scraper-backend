"""
VapeStation Scraper Backend
============================
FastAPI + Scrapling για undetectable web scraping.
Συμβατό με το VS Admin panel.
"""
import os
import re
import logging
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl

# ── Logging ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
log = logging.getLogger("vs-scraper")

# ── Config ───────────────────────────────────────────────────────
API_SECRET = os.getenv("API_SECRET", "vs-scraper-2026-thanasis")
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")
TIMEOUT_SECONDS = int(os.getenv("TIMEOUT_SECONDS", "30"))

# ── App ──────────────────────────────────────────────────────────
app = FastAPI(
    title="VapeStation Scraper",
    version="2.0.0",
    description="Adaptive web scraping powered by Scrapling"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# ── Auth ─────────────────────────────────────────────────────────
def verify_token(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")
    token = authorization.replace("Bearer ", "").strip()
    if token != API_SECRET:
        raise HTTPException(401, "Invalid token")
    return True

# ── Models ───────────────────────────────────────────────────────
class ScrapeRequest(BaseModel):
    url: str
    selector: Optional[str] = None
    label: Optional[str] = "manual"

class Competitor(BaseModel):
    name: str
    url: str
    price_selector: Optional[str] = None

class CompetitorsRequest(BaseModel):
    competitors: List[Competitor]

# ── Scraping core ────────────────────────────────────────────────
# Lazy import — Scrapling βαρύ, φορτώνει on-demand
_fetcher = None

def get_fetcher():
    """Φορτώνει το Scrapling fetcher με lazy init."""
    global _fetcher
    if _fetcher is None:
        try:
            from scrapling.fetchers import StealthyFetcher
            StealthyFetcher.adaptive = True
            _fetcher = StealthyFetcher
            log.info("✓ Scrapling StealthyFetcher loaded")
        except Exception as e:
            log.error(f"✗ Scrapling load failed: {e}")
            # Fallback: requests + BeautifulSoup
            _fetcher = "fallback"
    return _fetcher


def scrape_with_fallback(url: str):
    """Fallback scraper με requests + BeautifulSoup αν δεν φορτώσει το Scrapling."""
    import requests
    from bs4 import BeautifulSoup

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
        "Accept-Language": "el-GR,el;q=0.9,en;q=0.8",
    }
    r = requests.get(url, headers=headers, timeout=TIMEOUT_SECONDS)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def auto_detect_price(html_text: str) -> Optional[str]:
    """Αυτόματη ανίχνευση τιμής με regex (€, EUR, etc)."""
    patterns = [
        r'(\d+[.,]\d{2})\s*€',
        r'€\s*(\d+[.,]\d{2})',
        r'(\d+[.,]\d{2})\s*EUR',
        r'"price"\s*:\s*"?(\d+[.,]?\d*)"?',
        r'price[\'"]\s*:\s*[\'"]?(\d+[.,]?\d*)',
    ]
    for pat in patterns:
        m = re.search(pat, html_text, re.IGNORECASE)
        if m:
            return m.group(1).replace(",", ".")
    return None


def auto_detect_title(page) -> Optional[str]:
    """Auto-detect product title από common selectors."""
    selectors = [
        'h1.product_title',
        'h1.product-title',
        'h1[itemprop="name"]',
        '.product_title',
        '.product-name',
        'h1',
        'title',
    ]
    for sel in selectors:
        try:
            if hasattr(page, 'css_first'):
                el = page.css_first(sel)
                if el:
                    text = el.text.strip() if hasattr(el, 'text') else str(el).strip()
                    if text:
                        return text[:200]
            else:
                el = page.select_one(sel)
                if el:
                    text = el.get_text(strip=True)
                    if text:
                        return text[:200]
        except Exception:
            continue
    return None


def do_scrape(url: str, selector: Optional[str] = None) -> dict:
    """Main scrape logic — επιστρέφει dict με αποτέλεσμα."""
    started = datetime.now(timezone.utc)
    fetcher = get_fetcher()

    try:
        if fetcher == "fallback":
            log.warning("Using fallback scraper (requests+bs4)")
            page = scrape_with_fallback(url)
            html_text = str(page)

            data = {}
            if selector:
                els = page.select(selector)
                data[selector] = [el.get_text(strip=True) for el in els[:10]]
            else:
                price = auto_detect_price(html_text)
                if price:
                    data["price"] = price

            title = auto_detect_title(page)

        else:
            # Scrapling StealthyFetcher
            log.info(f"Scraping (stealthy): {url}")
            page = fetcher.fetch(
                url,
                headless=True,
                network_idle=True,
                timeout=TIMEOUT_SECONDS * 1000,
            )
            html_text = page.html_content if hasattr(page, 'html_content') else str(page)

            data = {}
            if selector:
                els = page.css(selector)
                data[selector] = [el.text.strip() for el in els[:10] if el.text]
            else:
                price = auto_detect_price(html_text)
                if price:
                    data["price"] = price

            title = auto_detect_title(page)

        duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        return {
            "status": "success",
            "url": url,
            "title": title,
            "data": data,
            "selector": selector,
            "duration_ms": duration_ms,
            "timestamp": started.isoformat(),
        }

    except Exception as e:
        log.error(f"Scrape failed for {url}: {e}")
        return {
            "status": "error",
            "url": url,
            "error": str(e)[:500],
            "timestamp": started.isoformat(),
        }


# ── Routes ───────────────────────────────────────────────────────
@app.get("/")
def root():
    return {
        "service": "VapeStation Scraper",
        "version": "2.0.0",
        "status": "online",
        "endpoints": ["/health", "/scrape", "/scrape/competitors"],
    }


@app.get("/health")
def health():
    """Public health check — δεν χρειάζεται auth."""
    fetcher = get_fetcher()
    return {
        "status": "ok",
        "engine": "scrapling" if fetcher != "fallback" else "fallback",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/scrape")
def scrape_endpoint(req: ScrapeRequest, _auth: bool = Depends(verify_token)):
    if not req.url.startswith(("http://", "https://")):
        raise HTTPException(400, "URL must start with http:// or https://")
    result = do_scrape(req.url, req.selector)
    result["label"] = req.label
    return result


@app.post("/scrape/competitors")
def scrape_competitors(req: CompetitorsRequest, _auth: bool = Depends(verify_token)):
    results = []
    for c in req.competitors:
        r = do_scrape(c.url, c.price_selector)
        results.append({
            "competitor_name": c.name,
            "url": c.url,
            "status": r.get("status"),
            "extracted_price": r.get("data", {}).get("price")
                              or (r.get("data", {}).get(c.price_selector, [None])[0]
                                  if c.price_selector else None),
            "title": r.get("title"),
            "error": r.get("error"),
        })
    return {
        "count": len(results),
        "results": results,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── Local dev ────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
