"""How a stored report is laid out on the page: small, display-only helpers for the report
templates. Nothing here fetches, stores or recalculates a finance figure: every number is
read from the stored report body (built in `services/reports.py`) and only formatted.
"""

from typing import Any

from app.web.formatting import compact, money, multiple, vs

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
