"""Unusual options activity (SPEC F4.8), worked out in code.

A contract is unusual when today's volume is more than `volume_to_open_interest` (2) times
its open interest and at least `min_volume` (500) contracts traded (config/institutional.yaml).
Open interest is the number of contracts open at the start of the day, so volume far above
it means new positions are being opened: someone is making a sizeable bet.

Only expiration dates in the next `days_ahead` (60) days are scanned, at most
`max_expirations` (6) per stock: each date is one request to Yahoo. Outside market hours
Yahoo's volume is the last session's.
"""

from collections.abc import Iterable
from datetime import date, timedelta
from typing import Any

from app.config import UnusualOptionsConfig
from app.providers.market_data import MarketData, NoOptionChain, OptionChainData


def is_unusual(volume: int | None, open_interest: int | None, cfg: UnusualOptionsConfig) -> bool:
    volume, open_interest = volume or 0, open_interest or 0
    return volume >= cfg.min_volume and volume > cfg.volume_to_open_interest * open_interest


def unusual_contracts(
    chains: Iterable[OptionChainData], cfg: UnusualOptionsConfig
) -> list[dict[str, Any]]:
    """Every unusual contract in these chains, biggest volume first."""
    found = []
    for chain in chains:
        for c in chain.contracts:
            if not is_unusual(c.volume, c.open_interest, cfg):
                continue
            found.append(
                {
                    "expiration": chain.expiration.isoformat(),
                    "type": c.option_type,
                    "strike": c.strike,
                    "volume": c.volume,
                    "open_interest": c.open_interest or 0,
                    # None when nothing was open yet: all of today's volume is new.
                    "ratio": c.volume / c.open_interest if c.open_interest else None,
                    "last_price": c.last_price,
                    "implied_volatility": c.implied_volatility,
                }
            )
    found.sort(key=lambda r: (-r["volume"], r["expiration"], r["strike"]))
    return found


def scan(
    market_data: MarketData, symbol: str, today: date, cfg: UnusualOptionsConfig
) -> dict[str, Any]:
    """Scan one stock's nearest expirations. Raises MarketDataError if Yahoo fails."""
    last_day = today + timedelta(days=cfg.days_ahead)
    dates = [d for d in market_data.get_option_expirations(symbol) if today <= d <= last_day]
    dates = dates[: cfg.max_expirations]
    chains = []
    for day in dates:
        try:
            chains.append(market_data.get_option_chain(symbol, day))
        except NoOptionChain:
            continue  # Yahoo listed the date but has no chain for it: nothing to scan
    found = unusual_contracts(chains, cfg)
    calls = [r for r in found if r["type"] == "call"]
    puts = [r for r in found if r["type"] == "put"]
    return {
        "expirations": len(chains),
        "days_ahead": cfg.days_ahead,
        "min_volume": cfg.min_volume,
        "volume_to_open_interest": cfg.volume_to_open_interest,
        "count": len(found),
        "calls": len(calls),
        "puts": len(puts),
        "call_volume": sum(r["volume"] for r in calls),
        "put_volume": sum(r["volume"] for r in puts),
        "contracts": found[: cfg.max_rows],
        "as_of": max(c.fetched_at for c in chains).isoformat() if chains else None,
    }
