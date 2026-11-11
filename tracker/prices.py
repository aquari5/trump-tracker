"""Stock/ETF prices (latest and past closes): Nasdaq API first, Yahoo Finance as a fallback."""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from urllib.parse import quote

from .sources import session

log = logging.getLogger(__name__)

NASDAQ = "https://api.nasdaq.com/api/quote/{sym}/info?assetclass={cls}"
YAHOO = "https://query2.finance.yahoo.com/v8/finance/chart/{sym}?range=1d&interval=1d"
NASDAQ_LOOKUP = "https://api.nasdaq.com/api/autocomplete/slookup/5?search={q}"
NASDAQ_HIST = ("https://api.nasdaq.com/api/quote/{sym}/historical?assetclass={cls}"
               "&fromdate={start}&todate={end}&limit=5000")
YAHOO_HIST = "https://query2.finance.yahoo.com/v8/finance/chart/{sym}?period1={p1}&period2={p2}&interval=1d"

_cache: dict[str, Optional[float]] = {}
ETFS = {"SPY"}


def _classes(ticker: str) -> tuple:
    return ("etf", "stocks") if ticker in ETFS else ("stocks", "etf")


def _nasdaq(ticker: str) -> Optional[float]:
    for cls in _classes(ticker):
        resp = session.get(NASDAQ.format(sym=ticker, cls=cls), timeout=15,
                           headers={"Accept": "application/json"})
        data = resp.json().get("data") if resp.ok else None
        price = ((data or {}).get("primaryData") or {}).get("lastSalePrice")
        if price:
            return float(price.replace("$", "").replace(",", ""))
    return None


def _yahoo(ticker: str) -> Optional[float]:
    resp = session.get(YAHOO.format(sym=ticker.replace(".", "-")), timeout=15)
    if not resp.ok:
        return None
    result = (resp.json().get("chart") or {}).get("result") or []
    return result[0]["meta"].get("regularMarketPrice") if result else None


def get_price(ticker: str) -> Optional[float]:
    ticker = ticker.strip().upper()
    if not ticker:
        return None
    if ticker not in _cache:
        price = None
        for fetch in (_nasdaq, _yahoo):
            try:
                price = fetch(ticker)
            except Exception as e:
                log.info("Price lookup %s via %s failed: %s", ticker, fetch.__name__, e)
            if price:
                break
        _cache[ticker] = price
    return _cache[ticker]


def lookup_ticker(name: str) -> str:
    """Best-effort ticker for a company name via Nasdaq's symbol search."""
    if not name.strip():
        return ""
    short = re.sub(r"[,.]?\s+(Inc|Corp|Corporation|Company|Co|Ltd|plc|Holdings|Group|N\.?V|S\.?A)\.?$", "",
                   name.strip(), flags=re.I)
    for q in dict.fromkeys([name.strip(), short]):
        try:
            resp = session.get(NASDAQ_LOOKUP.format(q=quote(q)), timeout=15, headers={"Accept": "application/json"})
            hits = (resp.json().get("data") or []) if resp.ok else []
        except Exception as e:
            log.info("Ticker lookup %r failed: %s", q, e)
            continue
        for h in hits:
            # Whole-word prefix only: "Intel" must not match "Intelligent ...".
            if h.get("asset") in ("STOCKS", "ETF") and re.match(rf"{re.escape(short.lower())}\b", h.get("name", "").lower()):
                return h["symbol"]
    return ""


# --- Closing price on a past day (e.g. the day a politician traded) -------------------

_close_cache: dict = {}


def _to_float(value: str) -> float:
    return float(value.replace("$", "").replace(",", ""))


def _nasdaq_close(ticker: str, day: date) -> Optional[float]:
    # Ask for everything from `day` until today: with a narrow date window the API silently
    # drops rows older than about a month, which would return a later day's price.
    for cls in _classes(ticker):
        resp = session.get(NASDAQ_HIST.format(sym=ticker, cls=cls, start=day, end=date.today()),
                           timeout=20, headers={"Accept": "application/json"})
        data = resp.json().get("data") if resp.ok else None
        rows = ((data or {}).get("tradesTable") or {}).get("rows") or []
        if rows:
            # Rows are newest first; the last one is the first trading day on/after `day`.
            first = datetime.strptime(rows[-1]["date"], "%m/%d/%Y").date()
            if first - day <= timedelta(days=7):
                return _to_float(rows[-1]["close"])
    return None


def _yahoo_close(ticker: str, day: date) -> Optional[float]:
    start = datetime.combine(day, time(), tzinfo=timezone.utc)
    resp = session.get(YAHOO_HIST.format(sym=ticker.replace(".", "-"), p1=int(start.timestamp()),
                                         p2=int((start + timedelta(days=7)).timestamp())), timeout=15)
    if not resp.ok:
        return None
    result = (resp.json().get("chart") or {}).get("result") or []
    closes = (result[0].get("indicators", {}).get("quote") or [{}])[0].get("close") if result else None
    return next((c for c in closes or [] if c), None)


def get_close_on(ticker: str, day: date) -> Optional[float]:
    """Close on `day`, or on the next trading day if markets were shut."""
    ticker = ticker.strip().upper()
    key = (ticker, day)
    if ticker and key not in _close_cache:
        price = None
        for fetch in (_nasdaq_close, _yahoo_close):
            try:
                price = fetch(ticker, day)
            except Exception as e:
                log.info("Close lookup %s %s via %s failed: %s", ticker, day, fetch.__name__, e)
            if price:
                break
        _close_cache[key] = price
    return _close_cache.get(key)
