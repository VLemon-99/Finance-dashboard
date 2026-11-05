"""Market facts for reports (SPEC F4.7, F4.8): valuation and its benchmarks, institutional
holders, funds the owner follows, unusual options activity.

Numbers only, worked out in code (app/services/valuation.py); none of this goes to the AI.
Everything is fetched for all of a run's tickers together, a few at a time:
1. Each ticker's own figures (multiples, yearly results).
2. Its comparison groups: the industry's and the sector's largest companies, or the owner's
   overrides from config/benchmarks.yaml. Each group, and each peer's figures, is fetched
   once per run and reused for `refresh_hours` (24), so tickers sharing an industry cost
   nothing extra.
3. Damodaran's industry averages (one download a day).
4. Each ticker's institutional ownership (Yahoo), and the latest two 13F reports of each
   fund the owner follows (SEC; once per run for all tickers, each report kept for good).
5. Unusual options activity in each ticker's nearest option chains (unusual_options.py).
Each part that fails is listed in the report's "missing" notes; the rest is still shown
(SPEC F4.11).
"""

import logging
import re
import threading
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.config import (
    YAHOO_SECTORS,
    BenchmarksConfig,
    FollowedFund,
    InstitutionalConfig,
    TickerBenchmarks,
    slug,
)
from app.providers.damodaran import DamodaranClient, DamodaranError, IndustryAverages
from app.providers.market_data import (
    AnnualFigures,
    CompanyProfile,
    GroupMembers,
    MarketData,
    MarketDataError,
    OwnershipData,
    TickerNotFound,
)
from app.providers.sec_13f import FundReport, ThirteenFClient, holding, matching_cusips
from app.services.unusual_options import scan
from app.services.valuation import (
    METRICS,
    fiscal_label,
    group_benchmark,
    history_multiples,
    metric_rows,
    target_section,
)
from app.sources.base import SourceError
from app.timeutils import today_eastern, utc_now

log = logging.getLogger(__name__)

UNEXPECTED = "unexpected error (details in the app log)."


class TimedCache:
    """Values kept for `max_age`, shared by report runs. Failures are never kept."""

    def __init__(self, max_age: timedelta) -> None:
        self.max_age = max_age
        self._items: dict[Any, tuple[datetime, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: Any) -> Any | None:
        with self._lock:
            found = self._items.get(key)
        if found is None or utc_now() - found[0] > self.max_age:
            return None
        return found[1]

    def put(self, key: Any, value: Any) -> None:
        with self._lock:
            self._items[key] = (utc_now(), value)


@dataclass
class Attempt[T]:
    value: T | None = None
    error: str | None = None


def attempt[T](fn: Callable[[], T]) -> Attempt[T]:
    """Run one outside request; a failure becomes a plain message instead of an exception."""
    try:
        return Attempt(value=fn())
    except TickerNotFound:
        return Attempt(error="Yahoo Finance has no data for this ticker.")
    except (MarketDataError, DamodaranError, SourceError) as exc:
        return Attempt(error=str(exc))
    except Exception:
        log.exception("Market facts request failed unexpectedly")
        return Attempt(error=UNEXPECTED)


@dataclass
class TickerFacts:
    """What a ticker's report gets: sections (None = not available) and missing notes."""

    valuation: dict[str, Any] | None = None
    institutional: dict[str, Any] | None = None
    unusual_options: dict[str, Any] | None = None
    missing: list[str] = field(default_factory=list)


@dataclass
class _Plan:
    """Which groups a ticker is compared with."""

    ticker: str
    profile: CompanyProfile
    override: TickerBenchmarks
    industry_key: str | None
    sector_key: str | None

    @property
    def custom_peers(self) -> list[str] | None:
        return self.override.peers


@dataclass
class MarketFacts:
    market_data: MarketData
    benchmarks: BenchmarksConfig
    damodaran: DamodaranClient | None = None
    funds: ThirteenFClient | None = None
    institutional: InstitutionalConfig = field(default_factory=InstitutionalConfig)
    max_parallel: int = 4

    def __post_init__(self) -> None:
        age = timedelta(hours=self.benchmarks.refresh_hours)
        self._profiles = TimedCache(age)
        self._groups = TimedCache(age)

    # --- the whole run ------------------------------------------------------------

    def gather(
        self, tickers: Sequence[str], company_names: Callable[[str], str | None] | None = None
    ) -> dict[str, TickerFacts]:
        """Everything for these tickers. `company_names` gives a ticker's SEC company name
        (it helps recognize the stock in funds' 13F reports)."""
        tickers = list(dict.fromkeys(tickers))
        facts = {t: TickerFacts() for t in tickers}
        profiles = self._parallel(tickers, self._profile)
        figures = self._parallel(
            [t for t in tickers if profiles[t].value],
            lambda t: attempt(lambda: self.market_data.get_annual_figures(t)),
        )
        plans = {}
        for t in tickers:
            got = profiles[t]
            if got.error:
                facts[t].missing.append(f"Valuation: {got.error}")
            elif not has_valuation(got.value):
                facts[t].missing.append(
                    "Valuation: Yahoo Finance has no valuation figures for this ticker "
                    "(funds and ETFs have none)."
                )
            else:
                plans[t] = self._plan(t, got.value)

        groups = self._fetch_groups(plans.values())
        self._fetch_profiles(self._peer_symbols(plans.values(), groups))
        damodaran = self._damodaran() if plans else None

        for t, plan in plans.items():
            facts[t].valuation = self._valuation(
                plan, figures[t], groups, damodaran, facts[t].missing
            )

        ownership = self._parallel(
            tickers, lambda t: attempt(lambda: self.market_data.get_ownership(t))
        )
        funds = self._fund_reports()
        for t in tickers:
            names = [profiles[t].value.name if profiles[t].value else None]
            if company_names is not None:
                names.append(_safe_name(company_names, t))
            facts[t].institutional = self._institutional(
                t, ownership[t], funds, names, facts[t].missing
            )

        options_cfg = self.institutional.unusual_options
        if options_cfg.enabled:
            today = today_eastern()
            scans = self._parallel(
                tickers,
                lambda t: attempt(lambda: scan(self.market_data, t, today, options_cfg)),
            )
            for t in tickers:
                if scans[t].error:
                    facts[t].missing.append(f"Unusual options activity: {scans[t].error}")
                facts[t].unusual_options = scans[t].value
        return facts

    def _parallel[K](self, items: Iterable[K], fn: Callable[[K], Any]) -> dict[K, Any]:
        items = list(items)
        if not items:
            return {}
        with ThreadPoolExecutor(max_workers=self.max_parallel) as pool:
            return dict(zip(items, pool.map(fn, items), strict=True))

    # --- figures, with the shared cache ---------------------------------------------

    def _profile(self, symbol: str) -> Attempt[CompanyProfile]:
        cached = self._profiles.get(symbol)
        if cached is not None:
            return Attempt(value=cached)
        got = attempt(lambda: self.market_data.get_profile(symbol))
        if got.value is not None:
            self._profiles.put(symbol, got.value)
        return got

    def _group(self, key: tuple[str, str]) -> Attempt[GroupMembers]:
        cached = self._groups.get(key)
        if cached is not None:
            return Attempt(value=cached)
        got = attempt(lambda: self.market_data.get_group_members(*key))
        if got.value is not None:
            self._groups.put(key, got.value)
        return got

    def _fetch_groups(self, plans: Iterable[_Plan]) -> dict[tuple[str, str], Attempt]:
        needed = set()
        for plan in plans:
            if plan.industry_key and not plan.custom_peers:
                needed.add(("industry", plan.industry_key))
            if plan.sector_key:
                needed.add(("sector", plan.sector_key))
        return self._parallel(sorted(needed), self._group)

    def _peer_symbols(self, plans: Iterable[_Plan], groups: dict) -> set[str]:
        symbols: set[str] = set()
        for plan in plans:
            symbols |= set(self._industry_peers(plan, groups))
            symbols |= set(self._sector_companies(plan, groups))
        return symbols

    def _fetch_profiles(self, symbols: set[str]) -> None:
        self._parallel(sorted(symbols), self._profile)

    def _industry_peers(self, plan: _Plan, groups: dict) -> list[str]:
        if plan.custom_peers:
            return [s for s in plan.custom_peers if s != plan.ticker]
        got = groups.get(("industry", plan.industry_key))
        members = got.value.symbols if got and got.value else ()
        return [s for s in members if s != plan.ticker][: self.benchmarks.industry_peers]

    def _sector_companies(self, plan: _Plan, groups: dict) -> list[str]:
        got = groups.get(("sector", plan.sector_key))
        members = got.value.symbols if got and got.value else ()
        return [s for s in members if s != plan.ticker][: self.benchmarks.sector_companies]

    def _damodaran(self) -> Attempt[IndustryAverages] | None:
        if self.damodaran is None or not self.benchmarks.damodaran.enabled:
            return None
        return attempt(self.damodaran.averages)

    # --- one ticker ----------------------------------------------------------------

    def _plan(self, ticker: str, profile: CompanyProfile) -> _Plan:
        override = self.benchmarks.for_ticker(ticker)
        industry = override.industry or profile.industry_key or slug(profile.industry or "")
        sector = override.sector or profile.sector_key or slug(profile.sector or "")
        return _Plan(ticker, profile, override, industry or None, sector or None)

    def _valuation(
        self,
        plan: _Plan,
        figures: Attempt[list[AnnualFigures]],
        groups: dict,
        damodaran: Attempt[IndustryAverages] | None,
        missing: list[str],
    ) -> dict[str, Any]:
        profile = plan.profile
        if figures.error:
            missing.append(f"Valuation history: {figures.error}")
        years, skipped = history_multiples(figures.value or [], profile.shares_outstanding)

        industry = self._industry_column(plan, groups, missing)
        sector = self._sector_column(plan, groups, missing)
        damo = self._damodaran_column(plan, damodaran, missing)
        rows = metric_rows(
            profile.multiples,
            years,
            {
                "industry": industry["bench"] if industry else None,
                "sector": sector["bench"] if sector else None,
                "damodaran": damo["bench"] if damo else None,
            },
        )
        return {
            "price": profile.price,
            "sector": profile.sector,
            "industry": profile.industry,
            "rows": rows,
            "history": {
                "span": f"{fiscal_label(years[0].period_end)}–{fiscal_label(years[-1].period_end)}"
                if len(years) > 1
                else fiscal_label(years[0].period_end),
                "years": [
                    {
                        "year": fiscal_label(y.period_end),
                        "pe": y.pe,
                        "ev_ebitda": y.ev_ebitda,
                        "ps": y.ps,
                    }
                    for y in years
                ],
                "skipped": skipped,
            }
            if years
            else None,
            "industry_peers": _without(industry, "bench"),
            "sector_group": _without(sector, "bench"),
            "damodaran": _without(damo, "bench"),
            "targets": target_section(profile),
        }

    def _peer_profiles(self, symbols: Iterable[str]) -> list[CompanyProfile]:
        return [p for p in (self._profiles.get(s) for s in symbols) if p is not None]

    def _industry_column(self, plan: _Plan, groups: dict, missing: list[str]) -> dict | None:
        symbols = self._industry_peers(plan, groups)
        if plan.custom_peers:
            name, custom = "Your peers", True
        else:
            got = groups.get(("industry", plan.industry_key))
            if got is None:
                return None  # Yahoo gave no industry
            if got.error:
                missing.append(f"Valuation, industry peers: {got.error}")
                return None
            name, custom = got.value.name, False
        peers = self._peer_profiles(symbols)
        if not peers:
            if symbols:
                missing.append("Valuation, industry peers: Yahoo Finance had no figures for them.")
            return None
        return {
            "name": name,
            "custom": custom,
            "bench": group_benchmark(peers),
            "peers": [
                {
                    "symbol": p.symbol,
                    "name": p.name,
                    **{key: getattr(p.multiples, key) for key, _ in METRICS},
                }
                for p in peers
            ],
        }

    def _sector_column(self, plan: _Plan, groups: dict, missing: list[str]) -> dict | None:
        got = groups.get(("sector", plan.sector_key))
        if got is None:
            return None
        if got.error:
            missing.append(f"Valuation, sector benchmark: {got.error}")
            return None
        companies = self._peer_profiles(self._sector_companies(plan, groups))
        if not companies:
            missing.append("Valuation, sector benchmark: Yahoo Finance had no figures for it.")
            return None
        return {
            "name": YAHOO_SECTORS.get(plan.sector_key or "", got.value.name),
            "override": plan.override.sector is not None,
            "count": len(companies),
            "bench": group_benchmark(companies),
        }

    def _damodaran_column(
        self, plan: _Plan, averages: Attempt[IndustryAverages] | None, missing: list[str]
    ) -> dict | None:
        if averages is None:
            return None
        if averages.error:
            missing.append(f"Damodaran industry averages: {averages.error}")
            return None
        wanted = plan.override.damodaran_industry or damodaran_name(
            self.benchmarks, plan.industry_key
        )
        if not wanted:
            label = plan.profile.industry or plan.industry_key or "this industry"
            missing.append(
                f"Damodaran industry averages: no Damodaran industry is set for '{label}' "
                "(config/benchmarks.yaml)."
            )
            return None
        found = averages.value.find(wanted)
        if found is None:
            missing.append(
                f"Damodaran industry averages: his page has no industry called '{wanted}' "
                "(check config/benchmarks.yaml)."
            )
            return None
        name, multiples = found
        for problem in averages.value.problems:
            missing.append(f"Damodaran industry averages, {problem}")
        return {
            "industry": name,
            "updated": averages.value.updated,
            "bench": {key: {"value": getattr(multiples, key), "count": None} for key, _ in METRICS},
        }

    # --- holders and followed funds (F4.8) ------------------------------------------

    def _fund_reports(self) -> list[tuple[FollowedFund, Attempt]] | None:
        cfg = self.institutional.followed_funds
        if self.funds is None or not cfg.enabled or not cfg.funds:
            return None
        # One fund after another: the SEC asks for a gentle pace.
        return [(f, attempt(lambda f=f: self.funds.latest_reports(f.cik))) for f in cfg.funds]

    def _institutional(
        self,
        ticker: str,
        ownership: Attempt[OwnershipData],
        funds: list[tuple[FollowedFund, Attempt]] | None,
        names: list[str | None],
        missing: list[str],
    ) -> dict[str, Any]:
        if ownership.error:
            missing.append(f"Top institutional holders: {ownership.error}")
        section: dict[str, Any] = {
            "ownership": ownership_section(ownership.value) if ownership.value else None,
            "funds": None,
        }
        if funds is None:
            return section
        problems: dict[str, list[str]] = {}
        reports = []
        for fund, got in funds:
            if got.error:
                problems.setdefault(got.error, []).append(fund.name)
            else:
                reports.append((fund, *got.value))
        for message, failed in problems.items():
            if len(failed) == len(funds):
                missing.append(f"Funds you follow: {message}")
            else:
                missing.append(f"Funds you follow ({', '.join(failed)}): {message}")
        if reports:
            cusip = self.institutional.followed_funds.cusips.get(ticker)
            positions = fund_positions(reports, names, cusip)
            periods = [latest.period for _, latest, _ in reports if latest]
            section["funds"] = {
                "checked": len(reports),
                "period": max(periods).isoformat() if periods else None,
                "positions": positions,
            }
        return section


def ownership_section(own: OwnershipData) -> dict[str, Any]:
    """% held by institutions and the top holders with the change in their stakes."""
    dates = [h.reported for h in own.top_institutions if h.reported]
    return {
        "institutions_pct": own.institutions_pct,
        "institutions_count": own.institutions_count,
        "as_of": max(dates).isoformat() if dates else None,
        "top": [
            {
                "name": h.name,
                "pct_held": h.pct_held,
                "pct_change": h.pct_change,
                "shares": h.shares,
                "value": h.value,
            }
            for h in own.top_institutions
        ],
    }


# A stake that changed by less than this (fraction) counts as unchanged.
UNCHANGED_BELOW = 0.005


def fund_positions(
    reports: Iterable[tuple[FollowedFund, FundReport | None, FundReport | None]],
    names: Iterable[str | None],
    cusip: str | None = None,
) -> list[dict[str, Any]]:
    """The followed funds that hold the stock, or sold it in their latest quarter.

    status: "new" (bought since the previous report), "added", "reduced", "unchanged",
    "sold" (all of it), or "held" (the fund's first report: nothing to compare with).
    change: the change in its share count since the previous report (0.1 = +10%).
    portfolio_pct: the stake's share of everything the fund reported (conviction)."""
    names = list(names)
    found = []
    for fund, latest, previous in reports:
        cusips = matching_cusips([latest, previous], names, cusip)
        now, before = holding(latest, cusips), holding(previous, cusips)
        if now is None and before is None:
            continue
        change = None
        if now and before:
            change = now[0] / before[0] - 1 if before[0] else None
            if change is None or abs(change) < UNCHANGED_BELOW:
                status = "unchanged"
            else:
                status = "added" if change > 0 else "reduced"
        elif now:
            status = "new" if previous is not None else "held"
        else:
            status, change = "sold", -1.0
        total = latest.total_value if latest else 0
        found.append(
            {
                "fund": fund.name,
                "status": status,
                "shares": now[0] if now else 0.0,
                "value": now[1] if now else 0.0,
                "change": change,
                "portfolio_pct": now[1] / total if now and total else None,
                "period": latest.period.isoformat() if latest else None,
                "url": latest.url if latest else None,
            }
        )
    found.sort(key=lambda f: (f["status"] == "sold", -f["value"]))
    return found


def _safe_name(company_names: Callable[[str], str | None], ticker: str) -> str | None:
    try:
        return company_names(ticker)
    except Exception:  # e.g. the SEC isn't set up: Yahoo's name is still used
        return None


def has_valuation(profile: CompanyProfile | None) -> bool:
    """A stock with a sector or any multiple. Funds and ETFs usually have neither."""
    if profile is None:
        return False
    m = profile.multiples
    return bool(profile.sector or m.trailing_pe or m.forward_pe or m.ev_ebitda or m.price_to_sales)


def damodaran_name(config: BenchmarksConfig, industry_key: str | None) -> str | None:
    """The Damodaran industry set for a Yahoo industry (matched ignoring punctuation)."""
    if not industry_key:
        return None
    wanted = _key(industry_key)
    for yahoo_name, damodaran in config.damodaran.industries.items():
        if _key(yahoo_name) == wanted:
            return damodaran
    return None


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _without(column: dict | None, key: str) -> dict | None:
    return {k: v for k, v in column.items() if k != key} if column else None
