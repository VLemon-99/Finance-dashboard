"""Positions from trades (SPEC F3.3), using the average-cost method.

Pure functions: no database, no network. `build_positions` replays one user's trades in
date order and returns the open positions and the closed ones with their realized P&L.

Rules (see DECISIONS.md, "Phase 4 part B"):
- The entry price is the average of the buys (long) or sales (short) that opened it.
- Trading against a position reduces it; realized P&L = (exit - average entry) x quantity,
  reversed for shorts. Going past zero closes it and opens the opposite position with the
  rest (e.g. holding 100 shares and selling 150 leaves a short of 50).
- A position that gets back to zero is closed. A later trade starts a new position.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class TradeInput:
    """The parts of a stored trade the calculation needs."""

    id: int
    instrument_id: int
    side: str  # "bought" | "sold"
    quantity: int
    price: float  # per share
    trade_date: date
    size: int = 1  # 1 for stocks, 100 for options
    underlying_price: float | None = None  # options: stock price at the time
    reason: str = "trade"


@dataclass
class OpenPosition:
    instrument_id: int
    direction: int  # +1 long, -1 short
    quantity: int  # shares or contracts, always positive
    avg_price: float  # per share
    opened_on: date  # date of the first trade of this position
    size: int = 1
    realized: float = 0.0  # $ realized by partial closes so far
    # Options: the average stock price when the opening trades were made (None if any
    # opening trade didn't record it). Used for covered-call capital at risk (F3.5).
    underlying_avg: float | None = None
    trade_ids: list[int] = field(default_factory=list)

    @property
    def is_short(self) -> bool:
        return self.direction < 0


@dataclass(frozen=True)
class ClosedPosition:
    instrument_id: int
    direction: int
    quantity: int  # every share/contract opened in this position
    avg_entry: float
    avg_exit: float
    opened_on: date
    closed_on: date
    realized: float  # $
    size: int = 1
    ended_by: str = "trade"  # the closing trade's reason: trade | expired | assigned | exercised

    @property
    def realized_pct(self) -> float | None:
        cost = self.avg_entry * self.quantity * self.size
        return self.realized / cost * 100 if cost > 0 else None


@dataclass
class _Running:
    """A position while trades are replayed."""

    open: OpenPosition
    opened_quantity: int  # every unit ever added, for the closed summary
    exit_value: float = 0.0  # sum of exit price x quantity
    exit_quantity: int = 0
    underlying_value: float | None = 0.0  # sum of stock price x quantity (opening trades)


def build_positions(
    trades: Iterable[TradeInput],
) -> tuple[list[OpenPosition], list[ClosedPosition]]:
    """Replay trades (by date, then the order entered). Returns (open, closed)."""
    running: dict[int, _Running] = {}
    closed: list[ClosedPosition] = []
    for trade in sorted(trades, key=lambda t: (t.trade_date, t.id)):
        direction = 1 if trade.side == "bought" else -1
        current = running.get(trade.instrument_id)
        remaining = trade.quantity

        if current is not None and current.open.direction != direction:
            pos = current.open
            reduce = min(remaining, pos.quantity)
            pos.realized += pos.direction * (trade.price - pos.avg_price) * reduce * pos.size
            pos.quantity -= reduce
            pos.trade_ids.append(trade.id)
            current.exit_value += trade.price * reduce
            current.exit_quantity += reduce
            remaining -= reduce
            if pos.quantity == 0:
                closed.append(
                    ClosedPosition(
                        instrument_id=pos.instrument_id,
                        direction=pos.direction,
                        quantity=current.opened_quantity,
                        avg_entry=pos.avg_price,
                        avg_exit=current.exit_value / current.exit_quantity,
                        opened_on=pos.opened_on,
                        closed_on=trade.trade_date,
                        realized=pos.realized,
                        size=pos.size,
                        ended_by=trade.reason,
                    )
                )
                del running[trade.instrument_id]
                current = None

        if remaining == 0:
            continue
        if current is None:
            current = running[trade.instrument_id] = _Running(
                open=OpenPosition(
                    instrument_id=trade.instrument_id,
                    direction=direction,
                    quantity=0,
                    avg_price=0.0,
                    opened_on=trade.trade_date,
                    size=trade.size,
                ),
                opened_quantity=0,
            )
        _add(current, trade, remaining)

    return [r.open for r in running.values()], closed


def _add(current: _Running, trade: TradeInput, quantity: int) -> None:
    """Add to a position in the trade's direction, averaging the entry price."""
    pos = current.open
    total = pos.quantity + quantity
    pos.avg_price = (pos.avg_price * pos.quantity + trade.price * quantity) / total
    pos.quantity = total
    pos.trade_ids.append(trade.id)
    current.opened_quantity += quantity
    if current.underlying_value is not None and trade.underlying_price is not None:
        current.underlying_value += trade.underlying_price * quantity
        pos.underlying_avg = current.underlying_value / current.opened_quantity
    else:
        current.underlying_value = None
        pos.underlying_avg = None
