"""Valuation numbers (SPEC F4.7), worked out in code, never by the AI.

- Each past fiscal year's multiples, from that year's figures and the share price at the
  year's end:
    P/E        = price / diluted earnings per share
    P/S        = market cap / revenue, where market cap = price x shares outstanding
    EV/EBITDA  = (market cap + total debt - cash) / EBITDA
  A multiple is "not meaningful" (None) when its denominator is zero or negative (a loss),
  as is standard practice (Damodaran does the same).
- The stock's own range: average, low and high of those yearly values.
- A group's benchmark: the median of its companies' values (the median, not the average,
  so one extreme company can't swing it). Companies without a meaningful value are left out.
- "vs": how far the stock's multiple is above (+) or below (-) a benchmark, as a fraction:
  value / benchmark - 1. +0.25 means 25% more expensive than the benchmark.
- Analyst targets: the % gap from today's price to each target, target / price - 1.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from statistics import median
from typing import Any

from app.providers.market_data import AnnualFigures, CompanyProfile, Multiples

# The multiples we show, in order, with their labels. The second item is the matching
# field of a past year (forward P/E has no history: it's about expected earnings).
METRICS: tuple[tuple[str, str], ...] = (
    ("trailing_pe", "P/E"),
    ("forward_pe", "Forward P/E"),
    ("ev_ebitda", "EV/EBITDA"),
    ("price_to_sales", "P/S"),
)
HISTORY_FIELDS = {"trailing_pe": "pe", "ev_ebitda": "ev_ebitda", "price_to_sales": "ps"}

# A past year whose share count differs from today's by more than this factor is skipped:
# it most likely straddles a stock split that Yahoo's figures weren't adjusted for, which
# would make its multiples wrong by the split ratio.
SPLIT_MISMATCH_FACTOR = 1.8


@dataclass(frozen=True)
class YearMultiples:
    period_end: date
    pe: float | None
    ps: float | None
    ev_ebitda: float | None


def _positive(value: float | None) -> bool:
    return value is not None and value > 0


def year_multiples(fig: AnnualFigures) -> YearMultiples:
    """One fiscal year's P/E, P/S and EV/EBITDA (see the module notes for the formulas)."""
    price = fig.price if _positive(fig.price) else None
    pe = price / fig.diluted_eps if price and _positive(fig.diluted_eps) else None
    market_cap = price * fig.shares if price and _positive(fig.shares) else None
    ps = market_cap / fig.revenue if market_cap and _positive(fig.revenue) else None
    ev_ebitda = None
    if market_cap and _positive(fig.ebitda):
        ev = market_cap + (fig.total_debt or 0.0) - (fig.cash or 0.0)
        ev_ebitda = ev / fig.ebitda if ev > 0 else None
    return YearMultiples(period_end=fig.period_end, pe=pe, ps=ps, ev_ebitda=ev_ebitda)


def history_multiples(
    figures: Sequence[AnnualFigures], current_shares: float | None
) -> tuple[list[YearMultiples], int]:
    """Each usable year's multiples (oldest first) and how many years were skipped because
    their share count doesn't match today's (a stock split, see SPLIT_MISMATCH_FACTOR)."""
    years, skipped = [], 0
    for fig in sorted(figures, key=lambda f: f.period_end):
        if _positive(current_shares) and _positive(fig.shares):
            ratio = fig.shares / current_shares
            if ratio > SPLIT_MISMATCH_FACTOR or ratio < 1 / SPLIT_MISMATCH_FACTOR:
                skipped += 1
                continue
        multiples = year_multiples(fig)
        if multiples.pe or multiples.ps or multiples.ev_ebitda:
            years.append(multiples)
    return years, skipped


@dataclass(frozen=True)
class OwnRange:
    average: float
    low: float
    high: float
    years: int


def own_range(values: Iterable[float | None]) -> OwnRange | None:
    """Average, low and high of the meaningful (positive) values; None if there are none."""
    usable = [v for v in values if _positive(v)]
    if not usable:
        return None
    return OwnRange(
        average=sum(usable) / len(usable), low=min(usable), high=max(usable), years=len(usable)
    )


def group_median(values: Iterable[float | None]) -> tuple[float | None, int]:
    """Median of the meaningful (positive) values, and how many there were."""
    usable = [v for v in values if _positive(v)]
    return (median(usable) if usable else None), len(usable)


def relative(value: float | None, benchmark: float | None) -> float | None:
    """How far `value` is above (+) or below (-) `benchmark`, as a fraction."""
    if not _positive(value) or not _positive(benchmark):
        return None
    return value / benchmark - 1


def group_benchmark(profiles: Iterable[CompanyProfile]) -> dict[str, dict[str, Any]]:
    """Per multiple: the group's median and how many companies it's based on."""
    profiles = list(profiles)
    result = {}
    for key, _ in METRICS:
        value, count = group_median(getattr(p.multiples, key) for p in profiles)
        result[key] = {"value": value, "count": count}
    return result


def target_section(profile: CompanyProfile) -> dict[str, Any] | None:
    """Analysts' low / average / high targets and the % gap to today's price."""
    targets = {
        "low": profile.target_low,
        "mean": profile.target_mean,
        "high": profile.target_high,
    }
    if not any(targets.values()):
        return None
    price = profile.price
    return {
        "analysts": profile.analyst_count,
        **targets,
        **{f"{k}_vs": relative(v, price) for k, v in targets.items()},
    }


def fiscal_label(day: date) -> str:
    """'FY2025' for a fiscal year ending in 2025."""
    return f"FY{day.year}"


def metric_rows(
    multiples: Multiples,
    years: Sequence[YearMultiples],
    benchmarks: dict[str, dict[str, dict[str, Any]] | None],
) -> list[dict[str, Any]]:
    """One row per multiple: now, own history, and each benchmark with its "vs".

    `benchmarks` maps a column name ("industry", "sector", "damodaran") to that group's
    per-multiple {"value", "count"} (or None when the column is missing)."""
    rows = []
    for key, label in METRICS:
        now = getattr(multiples, key)
        history = None
        field = HISTORY_FIELDS.get(key)
        if field:
            span = own_range(getattr(y, field) for y in years)
            if span:
                history = {
                    "average": span.average,
                    "low": span.low,
                    "high": span.high,
                    "years": span.years,
                    "vs": relative(now, span.average),
                }
        row: dict[str, Any] = {"key": key, "label": label, "now": now, "history": history}
        for column, group in benchmarks.items():
            entry = (group or {}).get(key) or {}
            value = entry.get("value")
            row[column] = (
                {"value": value, "count": entry.get("count"), "vs": relative(now, value)}
                if value is not None
                else None
            )
        rows.append(row)
    return rows
