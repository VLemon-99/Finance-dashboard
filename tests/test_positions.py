"""Positions from trades, average-cost method, with known answers (SPEC F3.1, F3.3)."""

from datetime import date

import pytest

from app.services.positions import TradeInput, build_positions

D1, D2, D3, D4 = date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3), date(2026, 10, 4)
_next_id = iter(range(1, 10_000))


def t(side, qty, price, day=D1, instrument=1, size=1, underlying=None, reason="trade"):
    return TradeInput(
        id=next(_next_id),
        instrument_id=instrument,
        side=side,
        quantity=qty,
        price=price,
        trade_date=day,
        size=size,
        underlying_price=underlying,
        reason=reason,
    )


def test_average_entry_price():
    [pos], closed = build_positions([t("bought", 10, 100), t("bought", 30, 120, D2)])
    assert (pos.direction, pos.quantity) == (1, 40)
    assert pos.avg_price == pytest.approx(115)  # (10x100 + 30x120) / 40
    assert pos.opened_on == D1
    assert closed == []


def test_partial_close_realizes_against_the_average():
    [pos], closed = build_positions(
        [t("bought", 10, 100), t("bought", 10, 120, D2), t("sold", 5, 130, D3)]
    )
    assert pos.quantity == 15
    assert pos.avg_price == pytest.approx(110)  # unchanged by a sale
    assert pos.realized == pytest.approx(100)  # (130 - 110) x 5
    assert closed == []


def test_full_close_moves_to_closed_with_realized_pnl():
    opened, [done] = build_positions(
        [t("bought", 10, 100), t("sold", 4, 90, D2), t("sold", 6, 110, D3)]
    )
    assert opened == []
    assert done.direction == 1 and done.quantity == 10
    assert done.avg_entry == pytest.approx(100)
    assert done.avg_exit == pytest.approx(102)  # (4x90 + 6x110) / 10
    assert done.realized == pytest.approx(20)  # -40 + 60
    assert done.realized_pct == pytest.approx(2.0)
    assert (done.opened_on, done.closed_on) == (D1, D3)


def test_f3_1_sold_with_no_position_opens_a_short():
    [pos], _ = build_positions([t("sold", 2, 3.00, size=100, underlying=150)])
    assert pos.is_short and pos.quantity == 2 and pos.avg_price == 3.00
    assert pos.underlying_avg == 150


def test_short_option_closed_for_profit():
    # Sold 2 puts at 2.00, bought back at 0.50 -> (2.00 - 0.50) x 2 x 100 = +300.
    _, [done] = build_positions([t("sold", 2, 2.00, size=100), t("bought", 2, 0.50, D2, size=100)])
    assert done.direction == -1
    assert done.realized == pytest.approx(300)
    assert done.realized_pct == pytest.approx(75)


def test_going_past_zero_flips_the_position():
    [pos], [done] = build_positions([t("bought", 100, 50), t("sold", 150, 55, D2)])
    assert done.realized == pytest.approx(500)  # 100 x (55 - 50)
    assert (pos.direction, pos.quantity, pos.avg_price) == (-1, 50, 55)
    assert pos.opened_on == D2


def test_new_position_after_closing_starts_fresh():
    [pos], [done] = build_positions(
        [t("bought", 10, 100), t("sold", 10, 110, D2), t("bought", 5, 90, D3)]
    )
    assert done.realized == pytest.approx(100)
    assert (pos.quantity, pos.avg_price, pos.opened_on, pos.realized) == (5, 90, D3, 0)


def test_trades_replayed_in_date_order_not_entry_order():
    # Entered the sale first, but it happened after the buy.
    opened, [done] = build_positions([t("sold", 10, 120, D2), t("bought", 10, 100, D1)])
    assert opened == [] and done.direction == 1 and done.realized == pytest.approx(200)


def test_instruments_are_kept_apart():
    opened, _ = build_positions([t("bought", 10, 100, instrument=1), t("sold", 1, 2, instrument=2)])
    assert {(p.instrument_id, p.direction) for p in opened} == {(1, 1), (2, -1)}


def test_underlying_average_for_covered_calls():
    [pos], _ = build_positions(
        [
            t("sold", 1, 3.0, size=100, underlying=150),
            t("sold", 3, 2.0, D2, size=100, underlying=158),
        ]
    )
    assert pos.underlying_avg == pytest.approx(156)  # (150 + 3x158) / 4
    assert pos.avg_price == pytest.approx(2.25)
    [pos], _ = build_positions(
        [t("sold", 1, 3.0, size=100, underlying=150), t("sold", 1, 2.0, D2, size=100)]
    )
    assert pos.underlying_avg is None  # one sale without a stock price


def test_closing_reason_is_kept():
    _, [done] = build_positions(
        [t("sold", 1, 2.0, size=100), t("bought", 1, 0.0, D4, size=100, reason="expired")]
    )
    assert done.ended_by == "expired"
    assert done.realized == pytest.approx(200)
