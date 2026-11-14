"""How a stored report is laid out on the page: small, display-only helpers for the report
templates. Nothing here fetches, stores or recalculates a finance figure: every number is
read from the stored report body (built in `services/reports.py`) and only formatted.
"""

from datetime import datetime
from typing import Any

from app.timeutils import EASTERN, as_utc
from app.web.formatting import DASH, _shown, compact, money, multiple, vs

NA = "n/a"
# Valuation benchmarks in the order the tile prefers them: (key in a valuation row, wording).
BENCHMARKS = (
    ("history", "own history"),
    ("industry", "peers"),
    ("sector", "sector"),
    ("damodaran", "industry avg"),
)


def _count(number: int, one: str, many: str | None = None) -> str:
    """'1 buy', '2 buys', '1 sale'."""
    return f"{number} {one if number == 1 else (many or one + 's')}"


def _tile(key: str, label: str, section: str, value: str, detail: str, tone: str = "flat"):
    return {
        "key": key,
        "label": label,
        "section": section,
        "value": value,
        "detail": detail,
        "tone": tone,
    }


def _sentiment_tile(body: dict[str, Any]) -> dict[str, str]:
    sentiment = body.get("sentiment") or {}
    score = sentiment.get("score")
    counts = sentiment.get("counts") or {}
    if score is None:
        detail = "no stories rated" if body.get("ai_model", True) else "AI ratings off"
        return _tile("sentiment", "Sentiment", "sentiment", NA, detail)
    value = "0" if score == 0 else f"{score:+d}".replace("-", "−")
    detail = " · ".join(f"{counts.get(k) or 0} {k}" for k in ("bullish", "neutral", "bearish"))
    tone = "up" if score > 0 else ("down" if score < 0 else "flat")
    return _tile("sentiment", "Sentiment", "sentiment", value, detail, tone)


def _valuation_tile(body: dict[str, Any]) -> dict[str, str]:
    valuation = body.get("valuation")
    if not valuation:
        return _tile("valuation", "Valuation", "valuation", NA, "not in this report")
    row = next((r for r in valuation.get("rows") or [] if r.get("now") is not None), None)
    if row is None:
        return _tile("valuation", "Valuation", "valuation", NA, "no multiples available")
    detail = "no benchmark"
    for key, wording in BENCHMARKS:
        cell = row.get(key)
        if cell and cell.get("vs") is not None:
            detail = f"{vs(cell['vs'])} vs {wording}"
            break
    return _tile(
        "valuation", "Valuation", "valuation", f"{row['label']} {multiple(row['now'])}", detail
    )


def _target_tile(body: dict[str, Any]) -> dict[str, str]:
    valuation = body.get("valuation")
    if not valuation:
        return _tile("target", "Analyst target", "valuation", NA, "not in this report")
    targets = valuation.get("targets") or {}
    if not targets.get("mean"):
        return _tile("target", "Analyst target", "valuation", NA, "no analyst targets")
    parts = []
    if targets.get("mean_vs") is not None:
        parts.append(f"{vs(targets['mean_vs'])} vs price")
    if targets.get("analysts"):
        parts.append(_count(targets["analysts"], "analyst"))
    return _tile(
        "target",
        "Analyst target",
        "valuation",
        f"${money(targets['mean'])}",
        " · ".join(parts) or "average",
    )


def _insider_tile(body: dict[str, Any]) -> dict[str, str]:
    insider = body.get("insider") or {}
    trades = insider.get("trades") or []
    days = insider.get("lookback_days")
    period = f"last {days} days" if days else "recent"
    if not trades:
        if not insider or insider.get("available") is False:
            return _tile("insider", "Insiders", "insider", NA, "SEC filings missing")
        return _tile("insider", "Insiders", "insider", "No trades", period)
    net = insider.get("net_value") or 0
    detail = (
        f"{_count(insider.get('buys') or 0, 'buy')} · "
        f"{_count(insider.get('sells') or 0, 'sale')}, {period}"
    )
    if insider.get("available") is False:
        detail += " (may be incomplete)"
    if net > 0:
        return _tile("insider", "Insiders", "insider", f"Net bought ${compact(net)}", detail, "up")
    if net < 0:
        return _tile("insider", "Insiders", "insider", f"Net sold ${compact(-net)}", detail, "down")
    return _tile("insider", "Insiders", "insider", "Net $0", detail)


def _options_tile(body: dict[str, Any]) -> dict[str, str]:
    unusual = body.get("unusual_options")
    if not unusual:
        return _tile("options", "Options flow", "insider", NA, "not in this report")
    if not unusual.get("count"):
        detail = "today" if unusual.get("expirations", True) else "no listed options"
        return _tile("options", "Options flow", "insider", "Nothing unusual", detail)
    detail = (
        f"{_count(unusual.get('calls') or 0, 'call')} · {_count(unusual.get('puts') or 0, 'put')}"
    )
    return _tile("options", "Options flow", "insider", f"{unusual['count']} unusual", detail)


def _sources_tile(body: dict[str, Any], missing: list[str]) -> dict[str, str]:
    found = len(body.get("stories") or [])
    detail = "source" if found == 1 else "sources"
    if missing:
        return _tile(
            "sources",
            "Sources",
            "sources",
            str(found),
            f"{detail} · {len(missing)} missing",
            "warn",
        )
    return _tile("sources", "Sources", "sources", str(found), detail)


def glance(body: dict[str, Any], missing: list[str] | None = None) -> list[dict[str, str]]:
    """The six "At a glance" tiles on top of a ticker's report: sentiment, valuation, analyst
    target, insiders, options flow, sources. Each has a label, a value, a detail line, a tone
    (`up` / `down` / `flat` / `warn`, a CSS class) and the report section it jumps to.

    A part the report doesn't have (old reports, a failed source) gives an "n/a" tile.
    """
    return [
        _sentiment_tile(body),
        _valuation_tile(body),
        _target_tile(body),
        _insider_tile(body),
        _options_tile(body),
        _sources_tile(body, missing or []),
    ]


# --- bars: where a figure sits on a range (display only) ---------------------------------


def position(value: float | None, low: float | None, high: float | None) -> float | None:
    """Where `value` sits between `low` (0) and `high` (100), in percent, kept inside 0-100.
    None when a figure is missing or the range has no width."""
    if value is None or low is None or high is None or high <= low:
        return None
    return min(100.0, max(0.0, (value - low) / (high - low) * 100))


def _outside(value: float, low: float, high: float) -> str | None:
    return "below" if value < low else ("above" if value > high else None)


def vs_class(fraction: float | None) -> str:
    """'premium' when the stock's multiple is above the benchmark, 'discount' when below, ''
    when the gap shows as 0% (same rounding as the `vs` filter) or there's nothing to compare."""
    if fraction is None:
        return ""
    shown = _shown(fraction * 100, 0)
    return "premium" if shown > 0 else ("discount" if shown < 0 else "")


def target_bar(targets: dict[str, Any] | None) -> dict[str, Any] | None:
    """The analysts' low-to-high target range with the average target and the share price
    placed on it. None unless both ends are known.

    The stored report keeps the average target and its gap to the price (gap = target / price
    - 1, see `services/valuation.py`), so the price is read back as target / (1 + gap).
    """
    if not targets:
        return None
    low, high, mean = targets.get("low"), targets.get("high"), targets.get("mean")
    if low is None or high is None or high <= low:
        return None
    gap = targets.get("mean_vs")
    price = mean / (1 + gap) if mean and gap is not None and gap > -1 else None
    return {
        "low": low,
        "high": high,
        "mean": mean,
        "mean_position": position(mean, low, high),
        "price": price,
        "price_position": position(price, low, high),
        "price_outside": _outside(price, low, high) if price is not None else None,
    }


def history_bars(valuation: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Per multiple with own-history figures: where today's value sits between the lowest and
    highest of the stock's past fiscal years."""
    bars = []
    for row in (valuation or {}).get("rows") or []:
        history, now = row.get("history"), row.get("now")
        if not history or now is None:
            continue
        low, high = history.get("low"), history.get("high")
        place = position(now, low, high)
        if place is None:
            continue
        bars.append(
            {
                "label": row["label"],
                "now": now,
                "low": low,
                "high": high,
                "position": place,
                "outside": _outside(now, low, high),
            }
        )
    return bars


def sentiment_bar(sentiment: dict[str, Any] | None) -> dict[str, Any] | None:
    """The bullish / neutral / bearish shares of the rated stories (percent, for a three-part
    bar) and where the score sits on the -100..+100 scale (percent; None without a score).
    None when no story was rated."""
    counts = (sentiment or {}).get("counts") or {}
    shares = {k: counts.get(k) or 0 for k in ("bullish", "neutral", "bearish")}
    total = sum(shares.values())
    if not total:
        return None
    score = (sentiment or {}).get("score")
    return {
        **{k: v / total * 100 for k, v in shares.items()},
        "counts": shares,
        "marker": position(score, -100, 100),
    }


# --- the briefing table on top of a whole-watchlist report -------------------------------

BRIEFING_KEYS = ("sentiment", "valuation", "target", "insider", "options")


def briefing(reports: list[Any]) -> list[dict[str, Any]]:
    """One row per ticker's report, in the order given: the same key figures as its "At a
    glance" tiles (a part the report lacks shows a dash), its first development, the
    bullish / neutral / bearish bar and how many sources were missing."""
    rows = []
    for report in reports:
        body = report.body or {}
        tiles = {t["key"]: t for t in glance(body, report.missing)}
        row: dict[str, Any] = {"ticker": report.ticker, "report_id": report.id}
        for key in BRIEFING_KEYS:
            tile = tiles[key]
            if tile["value"] == NA:
                tile = {**tile, "value": DASH, "detail": "", "tone": "flat"}
            row[key] = tile
        sentiment = body.get("sentiment") or {}
        row["counts"] = sentiment.get("counts") or {}
        row["mix"] = sentiment_bar(sentiment)
        developments = body.get("developments") or []
        row["top"] = developments[0] if developments else None
        row["missing"] = len(report.missing or [])
        rows.append(row)
    return rows


# --- statements and their sources --------------------------------------------------------

MAX_OUTLETS_NAMED = 2  # per statement line; the rest show as "+N more"
# Story kinds in the order they're counted above the Sources list: (kind, one, many).
KIND_LABELS = (
    ("news", "news", "news"),
    ("filing", "filing", "filings"),
    ("blog", "blog post", "blog posts"),
    ("video", "video", "videos"),
    ("post", "X post", "X posts"),
)


def _cited(refs: list[int], stories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The stories a statement cites, in the order cited."""
    by_ref = {story.get("ref"): story for story in stories or []}
    return [by_ref[ref] for ref in refs or [] if ref in by_ref]


def claim_tone(refs: list[int], stories: list[dict[str, Any]]) -> str | None:
    """The tone of the stories a statement cites, from their stored ratings: 'bullish' when
    all rated ones are bullish, 'bearish' when all are bearish, 'mixed' when both appear,
    otherwise 'neutral'. None when none of them was rated. It describes the cited stories,
    not the statement itself."""
    ratings = {story.get("sentiment") for story in _cited(refs, stories)} - {None, ""}
    if not ratings:
        return None
    if ratings == {"bullish"} or ratings == {"bearish"}:
        return ratings.pop()
    if {"bullish", "bearish"} <= ratings:
        return "mixed"
    return "neutral"


def claim_origin(refs: list[int], stories: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Who reported what a statement cites: up to two outlet names (each once, in order), how
    many more there are, and the US Eastern day of the first cited story ('Oct 30')."""
    cited = _cited(refs, stories)
    if not cited:
        return None
    outlets: list[str] = []
    for story in cited:
        for item in story.get("items") or []:
            outlet = item.get("outlet")
            if outlet and outlet not in outlets:
                outlets.append(outlet)
    day = ""
    published = cited[0].get("published_at")
    if published:
        when = as_utc(datetime.fromisoformat(published)).astimezone(EASTERN)
        day = f"{when:%b} {when.day}"
    if not outlets and not day:
        return None
    return {
        "outlets": outlets[:MAX_OUTLETS_NAMED],
        "more": max(0, len(outlets) - MAX_OUTLETS_NAMED),
        "day": day,
    }


def source_kinds(stories: list[dict[str, Any]]) -> list[tuple[str, int]]:
    """How many stories of each kind the Sources list holds, e.g. [('news', 5), ('filings',
    3)]; kinds with none are left out."""
    counts: dict[str, int] = {}
    for story in stories or []:
        kind = story.get("kind") or "news"
        counts[kind] = counts.get(kind, 0) + 1
    known = {kind for kind, _, _ in KIND_LABELS}
    other = sum(count for kind, count in counts.items() if kind not in known)
    if other:
        counts["news"] = counts.get("news", 0) + other
    return [
        (one if counts[kind] == 1 else many, counts[kind])
        for kind, one, many in KIND_LABELS
        if counts.get(kind)
    ]


# --- ownership, condensed (owner 2026-11-14: highlight the major events) -----------------

FUND_MOVES = ("new", "added", "reduced", "sold")


def insider_highlights(insider: dict[str, Any] | None, limit: int) -> dict[str, Any]:
    """The largest insider trades by dollar value (trades without a value last), at most
    `limit`, plus what the fold-out holds: how many trades in all, how many more than shown,
    how many different insiders, and how many of the sales were under pre-arranged plans."""
    trades = list((insider or {}).get("trades") or [])
    ranked = sorted(trades, key=lambda t: (t.get("value") is None, -(t.get("value") or 0.0)))
    top = ranked[: max(0, limit)]
    sales = [t for t in trades if t.get("kind") == "sell"]
    return {
        "top": top,
        "total": len(trades),
        "more": len(trades) - len(top),
        "people": len({t.get("name") for t in trades}),
        "sales": len(sales),
        "planned_sales": sum(1 for t in sales if t.get("plan_10b5_1")),
    }


def fund_changes(funds: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    """The followed funds' positions split into those that moved since their previous 13F
    report (new, added, reduced, sold) and those that didn't."""
    positions = (funds or {}).get("positions") or []
    return {
        "changed": [p for p in positions if p.get("status") in FUND_MOVES],
        "unchanged": [p for p in positions if p.get("status") not in FUND_MOVES],
    }
