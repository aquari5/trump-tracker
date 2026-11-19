"""Entry point: python -m tracker {alerts,trades,weekly,test-email} [--dry-run]"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import emails, store
from .analyze import TransientError, analyze
from .config import ROOT, load_config
from .prices import get_close_on, get_price
from .sources import fetch_feed, fetch_house_filings, load_filing

log = logging.getLogger("tracker")

MAX_SEEN = 5000
BENCHMARK = "SPY"


def run_alerts(cfg: dict, dry_run: bool) -> None:
    state = store.load("state", {})
    first_run = "seen_posts" not in state
    seen = state.get("seen_posts", [])
    seen_set = set(seen)

    posts, failed = [], []
    for src in cfg["sources"]:
        try:
            posts += fetch_feed(src)
        except Exception as e:
            log.error("Source %s failed: %s", src.get("name"), e)
            failed.append(src.get("name"))
    if failed and not posts:
        sys.exit("All sources failed")

    new = [p for p in posts if p.id not in seen_set]
    new.sort(key=lambda p: p.published, reverse=True)
    if first_run:
        # Don't flood the inbox with the whole feed; analyse a handful for the welcome email.
        to_analyze = new[:cfg["analysis"].get("welcome_items", 15)]
    else:
        to_analyze = new
    # Bare video/image posts have nothing to classify.
    to_analyze = [p for p in to_analyze if len((p.title + p.text).strip()) >= 20]
    log.info("%d posts fetched, %d new, %d to analyse", len(posts), len(new), len(to_analyze))

    try:
        analyses = analyze(to_analyze, cfg)
    except TransientError as e:
        log.warning("Analysis unavailable (%s); will retry these posts next run", e)
        return

    min_importance = cfg["analysis"].get("min_importance", 4)

    def worth_email(a) -> bool:
        # Sector-only items (no listed company) need the top score to be worth an email.
        has_ticker = any(c.ticker for c in a.companies)
        return a.relevant and a.importance >= min_importance and (has_ticker or a.importance == 5)

    hits = [(p, analyses[p.id]) for p in to_analyze if p.id in analyses and worth_email(analyses[p.id])]

    prices, new_mentions = {}, []
    if hits:
        tickers = {c.ticker for _, a in hits for c in a.companies if c.ticker}
        prices = {t: get_price(t) for t in sorted(tickers | {BENCHMARK})}
        now = datetime.now(timezone.utc).isoformat(timespec="minutes")
        new_mentions = [{
            "recorded": now, "posted": p.published.isoformat(), "post_id": p.id,
            "source": p.source, "url": p.url, "ticker": c.ticker, "name": c.name,
            "effect": c.effect, "importance": a.importance,
            "price": prices.get(c.ticker), "spy": prices.get(BENCHMARK),
        } for p, a in hits for c in a.companies if c.ticker]

    # Email first: if sending fails nothing is saved and the posts are retried next run.
    if hits or first_run:
        subject, html = emails.render_alerts(hits, prices, cfg, welcome=first_run)
        emails.send(subject, html, cfg, dry_run)

    if not dry_run:
        if new_mentions:
            store.save("mentions", store.load("mentions", []) + new_mentions)
        seen += [p.id for p in posts if p.id not in seen_set]
        state["seen_posts"] = seen[-MAX_SEEN:]
        state["last_alerts_run"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
        store.save("state", state)


def run_trades(cfg: dict, dry_run: bool) -> None:
    tcfg = cfg["trades"]
    if not tcfg.get("enabled", True):
        log.info("Trades disabled in config")
        return
    watchlist = tcfg.get("watchlist") or []
    only_watchlist = tcfg.get("only_watchlist", True)
    min_amount = tcfg.get("min_amount", 0)

    state = store.load("state", {})
    first_run = "seen_filings" not in state
    seen = state.get("seen_filings", [])
    seen_set = set(seen)

    today = date.today()
    years = [today.year - 1, today.year] if today.month == 1 else [today.year]
    filings = [f for y in years for f in fetch_house_filings(y)]
    new = [f for f in filings if f.doc_id not in seen_set]
    if only_watchlist:
        # Other members' filings are never downloaded.
        seen += [f.doc_id for f in new if not emails.is_watched(f.name, watchlist)]
        new = [f for f in new if emails.is_watched(f.name, watchlist)]
    if first_run:
        # Start with recent filings only; everything older counts as already seen.
        cutoff = today - timedelta(days=30 if only_watchlist else 3)
        seen += [f.doc_id for f in new if f.filed < cutoff]
        new = [f for f in new if f.filed >= cutoff]
    new.sort(key=lambda f: f.filed)
    new = new[:tcfg.get("max_filings_per_run", 60)]
    log.info("%d House filings in index, %d to process", len(filings), len(new))

    # Transactions are only recorded here; they reach the inbox in the Sunday summary.
    processed, recorded = [], []
    for f in new:
        try:
            load_filing(f)
        except Exception as e:
            log.warning("Skipping %s for now: %s", f.url, e)
            continue
        processed.append(f)
        watched = emails.is_watched(f.name, watchlist)
        # Dividend reinvestments happen automatically; they say nothing about the politician's view.
        for t in f.transactions:
            if "reinvest" in t["description"].lower() or not (watched or t["amount_floor"] >= min_amount):
                continue
            day = date.fromisoformat(t["date"])
            t["price_then"] = get_close_on(t["ticker"], day)
            t["spy_then"] = get_close_on(BENCHMARK, day)
            recorded.append({"filed": f.filed.isoformat(), "name": f.name, "district": f.district,
                             "doc_id": f.doc_id, "url": f.url, **t})
        log.info("%s: %d transactions", f.name, len(f.transactions))

    if not dry_run:
        trades = store.load("trades", []) + recorded
        keep_after = (today - timedelta(days=400)).isoformat()
        store.save("trades", [t for t in trades if t["filed"] >= keep_after])
        seen += [f.doc_id for f in processed]
        state["seen_filings"] = seen[-MAX_SEEN:]
        state["last_trades_run"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
        store.save("state", state)


def pct(now, then):
    return (now / then - 1) * 100 if now and then else None


def excess_over(change, spy_change):
    return change - spy_change if change is not None and spy_change is not None else None


def run_weekly(cfg: dict, dry_run: bool) -> None:
    lookback = cfg["weekly"].get("lookback_days", 365)
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=lookback)).isoformat()
    week_ago = (now - timedelta(days=7)).isoformat()
    spy_now = get_price(BENCHMARK)

    # Statements: performance since the first mention of each company.
    mentions = [m for m in store.load("mentions", []) if m["recorded"] >= since and m.get("price")]
    by_ticker: dict = {}
    for m in sorted(mentions, key=lambda m: m["recorded"]):
        r = by_ticker.setdefault(m["ticker"], {"ticker": m["ticker"], "name": m["name"], "first": m, "posts": set()})
        r["posts"].add(m["post_id"])
    mention_rows = []
    for r in by_ticker.values():
        first = r["first"]
        price_now = get_price(r["ticker"])
        change = pct(price_now, first["price"])
        mention_rows.append({
            "ticker": r["ticker"], "name": r["name"], "effect": first["effect"], "count": len(r["posts"]),
            "date": datetime.fromisoformat(first["recorded"]).strftime("%d.%m.%Y"), "sort": first["recorded"],
            "price_then": first["price"], "price_now": price_now, "change": change,
            "excess": excess_over(change, pct(spy_now, first.get("spy"))),
        })
    mention_rows.sort(key=lambda r: r["sort"], reverse=True)
    mention_rows = mention_rows[:30]

    # Trades: performance since the transaction date.
    all_trades = store.load("trades", [])
    # A price lookup can fail on the day a trade is recorded; try those again here.
    refilled = False
    for t in all_trades:
        if not t.get("price_then"):
            day = date.fromisoformat(t["date"])
            t["price_then"], t["spy_then"] = get_close_on(t["ticker"], day), get_close_on(BENCHMARK, day)
            refilled = refilled or bool(t["price_then"])
    if refilled and not dry_run:
        store.save("trades", all_trades)
    trades = [t for t in all_trades if t["filed"] >= since[:10] and t.get("price_then")]
    trade_rows = []
    for t in trades:
        price_now = get_price(t["ticker"])
        change = pct(price_now, t["price_then"])
        trade_rows.append({**t, "price_now": price_now, "change": change,
                           "excess": excess_over(change, pct(spy_now, t.get("spy_then")))})
    trade_rows.sort(key=lambda t: t["date"], reverse=True)
    trade_rows = trade_rows[:30]

    # Only positive mentions / purchases answer "did it pay to follow them?".
    praised = [r["excess"] for r in mention_rows if r["excess"] is not None and r["effect"] == "positive"]
    bought = [t["excess"] for t in trade_rows if t["excess"] is not None and t["type"] == "Kupno"]
    stats = {
        "week_mentions": len({m["post_id"] for m in mentions if m["recorded"] >= week_ago}),
        "week_trades": sum(t["filed"] >= week_ago[:10] for t in trades),
        "praised_excess": sum(praised) / len(praised) if praised else None,
        "praised_beat": 100 * sum(x > 0 for x in praised) / len(praised) if praised else None,
        "bought_excess": sum(bought) / len(bought) if bought else None,
    }
    subject, html = emails.render_weekly(mention_rows, trade_rows, stats, cfg)
    emails.send(subject, html, cfg, dry_run)
    if not dry_run:
        state = store.load("state", {})
        state["last_weekly"] = now.isoformat(timespec="minutes")
        store.save("state", state)


def run_auto(cfg: dict, dry_run: bool) -> None:
    """Every run checks statements, then catches up on whatever is due. GitHub's scheduler
    skips many runs, so jobs are not tied to exact cron times."""
    run_alerts(cfg, dry_run)
    state = store.load("state", {})
    now = datetime.now(timezone.utc)

    def older_than(key: str, hours: float) -> bool:
        last = state.get(key)
        return not last or now - datetime.fromisoformat(last) > timedelta(hours=hours)

    if older_than("last_trades_run", 20):
        run_trades(cfg, dry_run)
    # Sunday summary: first run on Sunday after 08:00 Warsaw time, once a week.
    local = now.astimezone(ZoneInfo(cfg["email"]["timezone"]))
    if local.weekday() == 6 and local.hour >= 8 and older_than("last_weekly", 6 * 24):
        run_weekly(cfg, dry_run)


def commit_state() -> None:
    """Commit data/ and push, so state survives even if the long run is cut short.
    Pulls first, which also brings in any config.yaml edits made on github.com."""
    def git(*args) -> bool:
        return subprocess.run(["git", *args], cwd=ROOT).returncode == 0

    git("add", "data")
    if not git("diff", "--cached", "--quiet"):
        git("commit", "-q", "-m", "Update tracker state")
    for attempt in range(1, 6):
        if git("pull", "-q", "--rebase") and git("push", "-q"):
            return
        log.warning("Saving state failed (attempt %d), retrying", attempt)
        time.sleep(10 * attempt)
    raise RuntimeError("could not push tracker state")


def run_loop(dry_run: bool, hours: float, every_minutes: int) -> None:
    """Stay up for `hours`, running `auto` every few minutes. GitHub's scheduler skips most
    cron slots, so instead of many short runs the workflow keeps one long run going."""
    end = time.monotonic() + hours * 3600
    failures = 0
    while True:
        started = time.monotonic()
        try:
            run_auto(load_config(), dry_run)
            failures = 0
        except (Exception, SystemExit) as e:
            failures += 1
            log.exception("Run failed (%d in a row): %s", failures, e)
            if failures >= 6:
                raise RuntimeError("tracker keeps failing") from e
        if not dry_run:
            commit_state()
        wait = every_minutes * 60 - (time.monotonic() - started)
        if time.monotonic() + max(wait, 0) > end:
            return
        time.sleep(max(wait, 0))


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m tracker")
    parser.add_argument("mode", choices=["loop", "auto", "alerts", "trades", "weekly", "test-email"])
    parser.add_argument("--hours", type=float, default=5.5, help="loop: how long to stay up")
    parser.add_argument("--every", type=int, default=10, help="loop: minutes between checks")
    parser.add_argument("--dry-run", action="store_true",
                        help="don't send email or save state; write the email to out/ instead")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.mode == "loop":
        run_loop(args.dry_run, args.hours, args.every)
        return
    cfg = load_config()
    if args.mode == "auto":
        run_auto(cfg, args.dry_run)
    elif args.mode == "alerts":
        run_alerts(cfg, args.dry_run)
    elif args.mode == "trades":
        run_trades(cfg, args.dry_run)
    elif args.mode == "weekly":
        run_weekly(cfg, args.dry_run)
    else:
        emails.send(*emails.render_test(cfg), cfg, args.dry_run)


if __name__ == "__main__":
    main()
