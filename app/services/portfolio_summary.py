"""The portfolio at a glance (IMP-10): total open P&L, open positions, capital at risk.

Pure arithmetic over figures the portfolio page already calculates (SPEC F3.3-F3.10), so
every total has known-answer tests in `tests/test_portfolio_summary.py`.

Rules:
- One open position = one stock holding, one option contract position, or one linked pair
  (a credit spread or a poor man's covered call counts as 1).
- Expired positions waiting for their outcome (F3.9) are left out of every total and counted
  separately.
- Total P&L adds the positions that have a current price; the others are counted as unpriced.
  A stale price (the data source failed, the last stored price is used) still counts, and the
  summary says how many positions use one (owner 2026-11-13).
- Capital at risk adds cash-secured puts, covered calls, credit spreads and poor man's covered
  calls. Uncovered short calls have no capital figure (F3.8) and are counted separately; so
  are covered calls whose stock price at sale wasn't recorded. Long options and stocks have
  no capital figure.
"""

from dataclasses import dataclass

from app.services.portfolio import PortfolioView


@dataclass(frozen=True)
class SummaryLine:
    """One open position, reduced to what the summary needs."""

    pnl: float | None  # $; None when there's no current price
    capital: float | None = None  # $ at risk, only for the strategies that have one
    uncovered_call: bool = False
    capital_unknown: bool = False  # a strategy with capital at risk, but no figure for it
    stale: bool = False  # priced from the last stored price because the source failed
    expired: bool = False


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


@dataclass(frozen=True)
class PortfolioSummary:
    positions: int  # open, not expired
    total_pnl: float | None  # $ over the priced positions; None when none is priced
    unpriced: int  # open positions left out of total_pnl (no current price)
    stale: int  # priced positions in total_pnl that use a stale price
    capital_at_risk: float  # $
    uncovered_calls: int  # short calls left out of capital_at_risk
    capital_unknown: int  # covered calls without a recorded stock price, left out too
    expired: int  # expired positions waiting for their outcome, left out of everything

    @property
    def notes(self) -> list[str]:
        """What the totals leave out, in plain words."""
        notes = []
        if self.unpriced:
            notes.append(
                _count(
                    self.unpriced,
                    "position without a current price isn't",
                    "positions without a current price aren't",
                )
                + " included in the P&L"
            )
        if self.stale:
            notes.append(
                _count(self.stale, "position uses a stale price", "positions use stale prices")
                + " (included in the P&L)"
            )
        if self.uncovered_calls:
            notes.append(
                _count(
                    self.uncovered_calls,
                    "uncovered short call isn't",
                    "uncovered short calls aren't",
                )
                + " included in capital at risk"
            )
        if self.capital_unknown:
            notes.append(
                _count(self.capital_unknown, "covered call has", "covered calls have")
                + " no stock price at sale recorded, so capital at risk leaves it out"
            )
        if self.expired:
            notes.append(
                _count(
                    self.expired,
                    "expired position waiting for its outcome isn't",
                    "expired positions waiting for their outcome aren't",
                )
                + " included"
            )
        return notes


def summarize(lines: list[SummaryLine]) -> PortfolioSummary:
    live = [line for line in lines if not line.expired]
    priced = [line.pnl for line in live if line.pnl is not None]
    return PortfolioSummary(
        positions=len(live),
        total_pnl=round(sum(priced), 2) if priced else None,
        unpriced=len(live) - len(priced),
        stale=sum(1 for line in live if line.stale and line.pnl is not None),
        capital_at_risk=round(sum(line.capital for line in live if line.capital is not None), 2),
        uncovered_calls=sum(1 for line in live if line.uncovered_call),
        capital_unknown=sum(1 for line in live if line.capital_unknown),
        expired=len(lines) - len(live),
    )


def summary_lines(view: PortfolioView) -> list[SummaryLine]:
    """The page's positions as summary lines: stocks, then each option item."""
    lines = [SummaryLine(pnl=row.pnl, stale=row.price_status == "stale") for row in view.stocks]
    for group in view.option_groups:
        for item in group.items:
            row = item.row
            if item.kind == "single":
                lines.append(
                    SummaryLine(
                        pnl=row.pnl,
                        capital=row.capital,
                        uncovered_call=row.strategy == "uncovered call",
                        capital_unknown=row.strategy == "covered call" and row.capital is None,
                        stale=row.price_status == "stale",
                        expired=row.expired,
                    )
                )
            else:  # a credit spread or a poor man's covered call
                stale = "stale" in (row.short.price_status, row.long.price_status)
                lines.append(
                    SummaryLine(pnl=row.pnl, capital=row.capital, stale=stale, expired=row.expired)
                )
    return lines


def portfolio_summary(view: PortfolioView) -> PortfolioSummary:
    return summarize(summary_lines(view))
