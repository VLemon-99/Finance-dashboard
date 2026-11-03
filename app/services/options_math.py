"""Option math: Black-Scholes price and greeks, days to expiry, mid price, in/out of the money.

Pure functions with no database or network, so every formula can be tested against
known answers (SPEC F2.3). The AI never calculates any of these.

Conventions (see DECISIONS.md, "Phase 4: option math conventions"):
- Volatility and the risk-free rate are fractions: 0.20 means 20%.
- No dividends (as in the SPEC F2.3 test case).
- Time is measured in calendar days over a 365-day year, up to 4:00 PM US Eastern on the
  expiration date (when US stock options stop trading).
- Theta is the change in price per calendar day. Vega is the change in price for a
  1 percentage point rise in volatility (e.g. 20% -> 21%). This is how most broker screens
  show them.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Literal

from app.timeutils import EASTERN, as_utc

OptionType = Literal["call", "put"]

DAYS_PER_YEAR = 365.0
SECONDS_PER_YEAR = DAYS_PER_YEAR * 24 * 60 * 60
MARKET_CLOSE = time(16, 0)  # US Eastern


@dataclass(frozen=True)
class Greeks:
    """Black-Scholes value of one option contract (per share) and its sensitivities."""

    price: float
    delta: float  # change in option price for a $1 rise in the stock
    gamma: float  # change in delta for a $1 rise in the stock
    theta: float  # change in option price per calendar day (usually negative)
    vega: float  # change in option price for +1 percentage point of volatility


def normal_cdf(x: float) -> float:
    """Chance that a standard normal value is below x."""
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def normal_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def black_scholes(
    option_type: OptionType,
    spot: float,
    strike: float,
    years: float,
    rate: float,
    volatility: float,
) -> Greeks | None:
    """Price and greeks, or None when they can't be calculated.

    None when the stock price, strike, time left or volatility is missing, zero or
    negative (e.g. an expired contract, or Yahoo gave no usable IV).
    """
    values = (spot, strike, years, rate, volatility)
    if any(v is None or not math.isfinite(v) for v in values):
        return None
    if spot <= 0 or strike <= 0 or years <= 0 or volatility <= 0:
        return None
    if option_type not in ("call", "put"):
        raise ValueError(f"option_type must be 'call' or 'put', not {option_type!r}")

    root_t = math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + volatility**2 / 2) * years) / (volatility * root_t)
    d2 = d1 - volatility * root_t
    discount = math.exp(-rate * years)
    gamma = normal_pdf(d1) / (spot * volatility * root_t)
    vega = spot * normal_pdf(d1) * root_t / 100  # per 1 percentage point
    time_decay = -spot * normal_pdf(d1) * volatility / (2 * root_t)

    if option_type == "call":
        price = spot * normal_cdf(d1) - strike * discount * normal_cdf(d2)
        delta = normal_cdf(d1)
        theta_per_year = time_decay - rate * strike * discount * normal_cdf(d2)
    else:
        price = strike * discount * normal_cdf(-d2) - spot * normal_cdf(-d1)
        delta = normal_cdf(d1) - 1
        theta_per_year = time_decay + rate * strike * discount * normal_cdf(-d2)

    return Greeks(
        price=price,
        delta=delta,
        gamma=gamma,
        theta=theta_per_year / DAYS_PER_YEAR,
        vega=vega,
    )


# --- time ---------------------------------------------------------------------------


def expires_at(expiration: date) -> datetime:
    """The moment a US stock option stops trading: 4:00 PM Eastern on its date, in UTC."""
    return datetime.combine(expiration, MARKET_CLOSE, tzinfo=EASTERN).astimezone(UTC)


def days_to_expiry(expiration: date, today: date) -> int:
    """DTE: calendar days from today (Eastern) to the expiration date. 0 on the day itself."""
    return (expiration - today).days


def years_to_expiry(expiration: date, now: datetime) -> float:
    """Time left until 4:00 PM Eastern on the expiration date, in years. Never negative."""
    seconds = (expires_at(expiration) - as_utc(now)).total_seconds()
    return max(seconds, 0.0) / SECONDS_PER_YEAR


def is_expired(expiration: date, now: datetime) -> bool:
    """True from 4:00 PM Eastern on the expiration date (SPEC F2.7)."""
    return as_utc(now) >= expires_at(expiration)


# --- prices and strikes ---------------------------------------------------------------


def mid_price(bid: float | None, ask: float | None) -> float | None:
    """Halfway between bid and ask. None unless both are there and ask >= bid > 0."""
    if bid is None or ask is None or bid <= 0 or ask < bid:
        return None
    return (bid + ask) / 2


def is_in_the_money(option_type: OptionType, strike: float, spot: float | None) -> bool:
    """A call is in the money when the stock is above the strike; a put when it's below."""
    if spot is None:
        return False
    return strike < spot if option_type == "call" else strike > spot


def nearest_strike(strikes: Iterable[float], spot: float | None) -> float | None:
    """The strike closest to the stock price (the lower one if two are equally close)."""
    if spot is None:
        return None
    return min(sorted(strikes), key=lambda k: abs(k - spot), default=None)
