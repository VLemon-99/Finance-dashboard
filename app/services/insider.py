"""Insider buys and sells from Form 4 filings (SPEC F4.8). Calculated in code, not by the AI.

Only open-market purchases (code P) and sales (code S) count. Value = shares x price per
share as reported on the Form 4; a trade without a price is listed but left out of the
dollar totals (and flagged, so the totals aren't silently understated).
"""

from collections.abc import Iterable
from dataclasses import dataclass, field

from app.db.models import SourceItemRow


@dataclass(frozen=True)
class InsiderTrade:
    ref: int  # the Form 4's stored item id (its source)
    date: str | None
    name: str
    role: str
    kind: str  # "buy" | "sell"
    shares: float
    price: float | None
    value: float | None  # shares x price
    plan_10b5_1: bool


@dataclass
class InsiderSummary:
    trades: list[InsiderTrade] = field(default_factory=list)  # newest first
    buys: int = 0
    sells: int = 0
    shares_bought: float = 0.0
    shares_sold: float = 0.0
    value_bought: float = 0.0
    value_sold: float = 0.0
    missing_prices: int = 0  # trades without a price, left out of the dollar totals
    filings_without_trades: int = 0  # Form 4s with only grants, exercises, gifts...

    @property
    def net_value(self) -> float:
        return self.value_bought - self.value_sold


def insider_summary(form4_rows: Iterable[SourceItemRow]) -> InsiderSummary:
    summary = InsiderSummary()
    for row in form4_rows:
        data = row.data or {}
        trades = data.get("trades") or []
        if not trades:
            summary.filings_without_trades += 1
            continue
        owners = data.get("owners") or [{"name": "Unknown", "role": "Insider"}]
        name = "; ".join(o["name"] for o in owners)
        role = "; ".join(o["role"] for o in owners)
        for t in trades:
            shares = float(t["shares"])
            price = t.get("price")
            price = float(price) if price else None
            value = shares * price if price is not None else None
            summary.trades.append(
                InsiderTrade(
                    ref=row.id,
                    date=t.get("date"),
                    name=name,
                    role=role,
                    kind=t["kind"],
                    shares=shares,
                    price=price,
                    value=value,
                    plan_10b5_1=bool(data.get("plan_10b5_1")),
                )
            )
            if value is None:
                summary.missing_prices += 1
            if t["kind"] == "buy":
                summary.buys += 1
                summary.shares_bought += shares
                summary.value_bought += value or 0.0
            else:
                summary.sells += 1
                summary.shares_sold += shares
                summary.value_sold += value or 0.0
    summary.trades.sort(key=lambda t: t.date or "", reverse=True)
    return summary
