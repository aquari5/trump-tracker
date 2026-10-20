"""Renders the three email types as inline-styled HTML and sends them over SMTP."""
from __future__ import annotations

import logging
import re
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from html import escape, unescape
from typing import Optional
from zoneinfo import ZoneInfo

from .analyze import Company
from .config import OUT_DIR

log = logging.getLogger(__name__)

FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
INK, MUTED, LINE, CARD = "#1f2328", "#656d76", "#e5e7eb", "#f8f9fb"
EFFECTS = {
    "positive": ("pozytywny", "#e6f4ea", "#137333"),
    "negative": ("negatywny", "#fce8e6", "#c5221f"),
    "mixed": ("mieszany", "#fef7e0", "#a15c00"),
    "unclear": ("niejasny", "#f1f3f4", "#5f6368"),
}


# --- Small building blocks ------------------------------------------------------

def fmt_time(dt: datetime, tz: str) -> str:
    return dt.astimezone(ZoneInfo(tz)).strftime("%d.%m.%Y, %H:%M")


def fmt_price(p: Optional[float]) -> str:
    return f"${p:,.2f}" if p else "–"


def fmt_pct(x: Optional[float], unit: str = "%", invert: bool = False) -> str:
    """Green = good for the reader; `invert` for sales, where a falling price was the right call."""
    if x is None:
        return "–"
    good = -x if invert else x
    color = "#137333" if good > 0 else "#c5221f" if good < 0 else MUTED
    return f'<span style="color:{color};font-weight:600">{x:+.1f}{unit}</span>'


def badge(text: str, bg: str, fg: str) -> str:
    return (f'<span style="display:inline-block;padding:2px 8px;border-radius:10px;background:{bg};'
            f'color:{fg};font-size:12px;font-weight:600;white-space:nowrap">{escape(text)}</span>')


def effect_badge(effect: str) -> str:
    label, bg, fg = EFFECTS.get(effect, EFFECTS["unclear"])
    return badge(label, bg, fg)


def quote_link(ticker: str) -> str:
    return (f'<a href="https://finance.yahoo.com/quote/{escape(ticker.replace(".", "-"))}" '
            f'style="color:{INK};font-weight:700;text-decoration:none">{escape(ticker)}</a>')


def th(text: str, align: str = "left") -> str:
    return (f'<th style="text-align:{align};padding:6px 8px;font-size:12px;color:{MUTED};'
            f'font-weight:600;border-bottom:1px solid {LINE}">{text}</th>')


def td(content: str, align: str = "left", extra: str = "") -> str:
    return (f'<td style="text-align:{align};padding:7px 8px;font-size:14px;'
            f'border-bottom:1px solid {LINE};vertical-align:top;{extra}">{content}</td>')


def layout(title: str, subtitle: str, body: str, cfg: dict) -> str:
    settings = (f' · <a href="{cfg["repo_url"]}/blob/main/config.yaml" style="color:{MUTED}">ustawienia</a>'
                if cfg.get("repo_url") else "")
    return f"""<!doctype html><html><body style="margin:0;padding:0;background:#ffffff">
<div style="max-width:660px;margin:0 auto;padding:20px 16px;font-family:{FONT};color:{INK};line-height:1.45">
<div style="font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:{MUTED};font-weight:600">Tracker polityków</div>
<h1 style="font-size:22px;margin:4px 0 2px">{title}</h1>
<div style="color:{MUTED};font-size:14px;margin-bottom:18px">{subtitle}</div>
{body}
<div style="margin-top:28px;padding-top:12px;border-top:1px solid {LINE};font-size:12px;color:{MUTED}">
To zestawienie publicznych wypowiedzi i zgłoszeń, nie porada inwestycyjna. Ceny są opóźnione
i mogą być niedokładne.{settings}</div>
</div></body></html>"""


def card(inner: str) -> str:
    return (f'<div style="background:{CARD};border:1px solid {LINE};border-radius:10px;'
            f'padding:14px 16px;margin:0 0 14px">{inner}</div>')


# --- Statement alerts ---------------------------------------------------------------

def render_alerts(hits: list, prices: dict, cfg: dict, welcome: bool = False) -> tuple[str, str]:
    """hits: list of (Post, Analysis), newest first."""
    tz = cfg["email"]["timezone"]

    # One line per statement, then ONE table of companies merged across all statements.
    items = []
    for post, a in hits:
        stars = "★" * a.importance + "☆" * (5 - a.importance)
        items.append(
            f'<div style="padding:10px 0;border-bottom:1px solid {LINE}">'
            f'<div style="font-size:12px;color:{MUTED}">{escape(post.source)} · {fmt_time(post.published, tz)}'
            f' · <span style="color:#a15c00">{stars}</span></div>'
            f'<div style="font-size:15px;font-weight:600;margin-top:3px">{escape(a.summary)} '
            f'<a href="{escape(post.url)}" style="color:#0969da;font-weight:400;font-size:13px">oryginał →</a></div>'
            f"</div>")

    merged: dict = {}
    sectors: list = []
    for _, a in hits:
        for s_ in a.sectors:
            if s_ not in sectors:
                sectors.append(s_)
        for c in a.companies:
            key = c.ticker or c.name
            if key not in merged:
                merged[key] = Company(c.name, c.ticker, c.effect, c.reason)
            elif merged[key].effect != c.effect:
                merged[key].effect = "mixed"
    rows = []
    for c in merged.values():
        ticker = quote_link(c.ticker) if c.ticker else f'<span style="color:{MUTED}">nienotowana</span>'
        rows.append("<tr>" + td(f"<b>{escape(c.name)}</b> {ticker}<br>"
                                f'<span style="font-size:12px;color:{MUTED}">{escape(c.reason)}</span>')
                    + td(effect_badge(c.effect))
                    + td(fmt_price(prices.get(c.ticker)), "right") + "</tr>")
    table = ""
    if rows:
        table = ('<h2 style="font-size:15px;margin:18px 0 4px">Spółki</h2>'
                 '<table style="width:100%;border-collapse:collapse">'
                 f'<tr>{th("Spółka")}{th("Wpływ")}{th("Cena teraz", "right")}</tr>' + "".join(rows) + "</table>")
    sector_line = (f'<div style="font-size:13px;color:{MUTED};margin-top:10px">Sektory: '
                   f'{escape(", ".join(sectors))}</div>' if sectors else "")
    cards = [card("".join(items).replace(f"border-bottom:1px solid {LINE}", "", 1) + table + sector_line)]

    names = []
    for _, a in hits:
        for c in a.companies:
            label = f"{c.name} ({c.ticker})" if c.ticker else c.name
            if label not in names:
                names.append(label)
    topic = ", ".join(names[:3])
    if not topic and hits:
        topic = ", ".join(hits[0][1].sectors[:2])
    if len(names) > 3:
        topic += f" +{len(names) - 3}"

    if welcome:
        subject = "✅ Tracker działa – pierwsze wyniki"
        title = "Tracker działa"
        subtitle = ("Od teraz dostaniesz maila, gdy Trump albo Biały Dom wspomni o konkretnej spółce "
                    "lub branży. Poniżej to, co znalazłem w ostatnich wpisach.")
        if not hits:
            cards.append(card("W ostatnich wpisach nie było nic, co dotyczyłoby konkretnych spółek."))
    else:
        who = "Trump" if any(p.id.startswith("truth:") for p, _ in hits) else hits[0][0].source.split(" – ")[0]
        subject = f"🔔 {who}: {topic}" if topic else f"🔔 {who}: nowa wypowiedź o spółkach"
        title = "Nowa wypowiedź dotycząca spółek" if len(hits) == 1 else f"{len(hits)} nowe wypowiedzi dotyczące spółek"
        subtitle = "Ceny to ostatni kurs w chwili wysłania maila. Zapisuję je, żeby w niedzielę policzyć zmianę."
    return subject, layout(escape(title), escape(subtitle), "".join(cards), cfg)


# --- Congressional trades -------------------------------------------------------------

def plural_pl(n: int, one: str, few: str, many: str) -> str:
    if n == 1:
        return one
    return few if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else many


def is_sale(t: dict) -> bool:
    return t["type"].startswith("Sprzedaż")


def type_label(t: dict) -> str:
    color = "#137333" if t["type"] == "Kupno" else "#c5221f" if t["type"].startswith("Sprzedaż") else MUTED
    return f'<span style="color:{color};font-weight:600">{escape(t["type"])}</span>'


def asset_cell(t: dict) -> str:
    option = " (opcje)" if t["asset_type"] == "OP" else ""
    desc = (f'<br><span style="font-size:12px;color:{MUTED}">{escape(t["description"])}</span>'
            if t.get("description") else "")
    return (f'{quote_link(t["ticker"])} <span style="color:{MUTED};font-size:13px">'
            f'{escape(t["asset"])}{option}</span>{desc}')


def price_move_cell(then: Optional[float], now: Optional[float], change: Optional[float],
                    invert: bool = False) -> str:
    return (f'{fmt_pct(change, invert=invert)}<br><span style="font-size:12px;color:{MUTED};white-space:nowrap">'
            f'{fmt_price(then)} → {fmt_price(now)}</span>')


def render_trades(filings: list, watchlist: list, cfg: dict) -> tuple[str, str]:
    blocks = []
    total, tickers, people = 0, [], []
    for f in filings:
        last = f.name.split()[-1]
        if last not in people:
            people.append(last)
        star = "★ " if is_watched(f.name, watchlist) else ""
        head = (f'<div style="font-size:16px;font-weight:700">{star}{escape(f.name)} '
                f'<span style="font-weight:400;color:{MUTED};font-size:13px">{escape(f.district)} · '
                f'zgłoszono {f.filed.strftime("%d.%m.%Y")} · <a href="{escape(f.url)}" style="color:#0969da">PDF</a>'
                f"</span></div>")
        if f.scanned:
            blocks.append(card(head + f'<div style="font-size:14px;margin-top:6px;color:{MUTED}">'
                                      "Zgłoszenie papierowe (skan) – szczegóły tylko w PDF.</div>"))
            continue
        rows = []
        for t in f.transactions:
            total += 1
            if t["ticker"] not in tickers:
                tickers.append(t["ticker"])
            change = (t["price_now"] / t["price_then"] - 1) * 100 if t.get("price_now") and t.get("price_then") else None
            rows.append("<tr>"
                        + td(datetime.fromisoformat(t["date"]).strftime("%d.%m"))
                        + td(type_label(t) + f'<br><span style="font-size:12px;color:{MUTED}">{escape(t["owner"])}</span>')
                        + td(asset_cell(t))
                        + td(escape(t["amount"].replace(" - ", "–")), extra="white-space:nowrap;font-size:13px")
                        + td(price_move_cell(t.get("price_then"), t.get("price_now"), change, is_sale(t)), "right")
                        + "</tr>")
        blocks.append(card(head + '<table style="width:100%;border-collapse:collapse;margin-top:8px">'
                           f'<tr>{th("Data")}{th("Typ")}{th("Walor")}{th("Kwota")}{th("Od transakcji", "right")}</tr>'
                           + "".join(rows) + "</table>"))

    what = plural_pl(total, "nowa transakcja", "nowe transakcje", "nowych transakcji")
    subject = f"🏛️ {', '.join(people[:3])}: {total} {what}" if total else f"🏛️ {', '.join(people[:3])}: nowe zgłoszenie"
    if tickers:
        subject += f" ({', '.join(tickers[:4])}{'…' if len(tickers) > 4 else ''})"
    subtitle = ("Izba Reprezentantów USA. Politycy mają do 45 dni na zgłoszenie, więc transakcja jest "
                "zwykle sprzed kilku tygodni. Ostatnia kolumna pokazuje, jak zmienił się kurs od dnia transakcji.")
    return subject, layout("Nowe transakcje polityków", escape(subtitle), "".join(blocks), cfg)


def is_watched(name: str, watchlist: list) -> bool:
    return any(re.search(rf"\b{re.escape(w)}\b", name, re.I) for w in watchlist or [])


# --- Weekly summary ---------------------------------------------------------------------

def stat_tile(label: str, value: str) -> str:
    return (f'<td style="width:33%;padding:10px 12px;background:{CARD};border:1px solid {LINE};border-radius:10px">'
            f'<div style="font-size:12px;color:{MUTED}">{label}</div>'
            f'<div style="font-size:22px;font-weight:700;margin-top:2px">{value}</div></td>')


def section(title: str, note: str) -> str:
    return (f'<h2 style="font-size:16px;margin:22px 0 4px">{title}</h2>'
            f'<div style="font-size:13px;color:{MUTED};margin-bottom:8px">{note}</div>')


def render_weekly(mention_rows: list, trade_rows: list, stats: dict, cfg: dict) -> tuple[str, str]:
    body = ('<table style="width:100%;border-collapse:separate;border-spacing:8px 0;margin:0 -8px 4px"><tr>'
            + stat_tile("Nowe wzmianki (7 dni)", str(stats["week_mentions"]))
            + stat_tile("Nowe transakcje (7 dni)", str(stats["week_trades"]))
            + stat_tile("Pochwalone przez Trumpa vs S&amp;P 500", fmt_pct(stats["praised_excess"], " pp"))
            + "</tr></table>")

    body += section("Co się stało po wzmiankach",
                    "Zmiana kursu od pierwszej wzmianki. „vs S&amp;P” = różnica względem indeksu S&amp;P 500 "
                    "w tym samym okresie, w punktach procentowych.")
    if mention_rows:
        trs = ["<tr>"
               + td(f'{quote_link(r["ticker"])}<br><span style="font-size:12px;color:{MUTED}">{escape(r["name"])}</span>')
               + td(f'{r["date"]}<br><span style="font-size:12px;color:{MUTED}">{r["count"]}× · '
                    f'{effect_badge(r["effect"])}</span>')
               + td(price_move_cell(r["price_then"], r["price_now"], r["change"]), "right")
               + td(fmt_pct(r["excess"], " pp"), "right")
               + "</tr>" for r in mention_rows]
        body += ('<table style="width:100%;border-collapse:collapse">'
                 f'<tr>{th("Spółka")}{th("Wzmianka")}{th("Zmiana", "right")}{th("vs S&amp;P", "right")}</tr>'
                 + "".join(trs) + "</table>")
        if stats.get("praised_beat") is not None:
            body += (f'<div style="font-size:13px;color:{MUTED};margin-top:8px">{stats["praised_beat"]:.0f}% '
                     "spółek wspomnianych pozytywnie radzi sobie lepiej niż S&amp;P 500 od czasu wzmianki.</div>")
    else:
        body += card("Brak zapisanych wzmianek o spółkach w wybranym okresie.")

    body += section("Jak wychodzą transakcje polityków",
                    "Zmiana kursu od dnia transakcji. Zielony = dobry ruch (przy sprzedaży: kurs potem spadł).")
    if trade_rows:
        trs = ["<tr>"
               + td(f'<b>{escape(t["name"].split()[-1])}</b><br><span style="font-size:12px;color:{MUTED}">'
                    f'{datetime.fromisoformat(t["date"]).strftime("%d.%m.%Y")}</span>')
               + td(type_label(t) + "<br>" + quote_link(t["ticker"]))
               + td(price_move_cell(t["price_then"], t["price_now"], t["change"], is_sale(t)), "right")
               + td(fmt_pct(t["excess"], " pp", is_sale(t)), "right")
               + "</tr>" for t in trade_rows]
        body += ('<table style="width:100%;border-collapse:collapse">'
                 f'<tr>{th("Kto")}{th("Transakcja")}{th("Zmiana", "right")}{th("vs S&amp;P", "right")}</tr>'
                 + "".join(trs) + "</table>")
        if stats.get("bought_excess") is not None:
            body += (f'<div style="font-size:13px;color:{MUTED};margin-top:8px">Ich zakupy średnio: '
                     f'{fmt_pct(stats["bought_excess"], " pp")} względem S&amp;P 500.</div>')
    else:
        body += card("Brak zapisanych transakcji obserwowanych polityków w wybranym okresie.")

    subtitle = "Ten mail przychodzi co niedzielę – jest też znakiem, że tracker nadal działa."
    return "📊 Tracker: podsumowanie tygodnia", layout("Podsumowanie tygodnia", escape(subtitle), body, cfg)


def render_test(cfg: dict) -> tuple[str, str]:
    body = card("Jeśli to czytasz, wysyłka maili działa. Nic więcej nie musisz robić. Maile przyjdą, "
                "gdy Trump lub Biały Dom powie coś o spółkach albo gdy obserwowany polityk zgłosi "
                "transakcję, plus podsumowanie w każdą niedzielę.")
    return "✅ Tracker: test maila", layout("Test maila", "Konfiguracja SMTP jest poprawna.", body, cfg)


# --- Sending ---------------------------------------------------------------------------

def send(subject: str, html_body: str, cfg: dict, dry_run: bool = False) -> None:
    if dry_run:
        OUT_DIR.mkdir(exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "-", subject.lower()).strip("-")[:40] or "email"
        path = OUT_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-{slug}.html"
        path.write_text(html_body, encoding="utf-8")
        log.info("Dry run - email saved to %s (subject: %s)", path, subject)
        return

    e = cfg["email"]
    missing = [k for k, v in (("SMTP_USER", e["user"]), ("SMTP_PASSWORD", e["password"]), ("EMAIL_TO", e["to"])) if not v]
    if missing:
        raise RuntimeError(f"Missing secrets: {', '.join(missing)}")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"Tracker polityków <{e['from']}>"
    msg["To"] = ", ".join(e["to"])
    text = re.sub(r"<(br|/div|/tr|/h\d)[^>]*>", "\n", html_body)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", text))).strip()
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")

    context = ssl.create_default_context()
    if e["smtp_port"] == 465:
        with smtplib.SMTP_SSL(e["smtp_host"], 465, context=context, timeout=30) as s:
            s.login(e["user"], e["password"])
            s.send_message(msg)
    else:
        with smtplib.SMTP(e["smtp_host"], e["smtp_port"], timeout=30) as s:
            s.starttls(context=context)
            s.login(e["user"], e["password"])
            s.send_message(msg)
    log.info("Email sent: %s", subject)
