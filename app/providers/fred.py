"""FRED adapter (the St. Louis Fed's free economic data service). The only file that talks
to FRED.

Phase 6 uses FRED's release calendar: the dates when CPI, the jobs report, PCE and GDP come
out (SPEC F5.2). FRED gives dates only; the usual release times are in config/settings.yaml.

Phase 9 reads series (dated values, e.g. the 10-year minus 2-year Treasury yield) for the
macro dashboard (SPEC F6). Any FRED series id works, so indicators are added in
config/macro.yaml without code changes (F6.3).

The API key travels in the address (FRED's rule), so addresses are never logged or shown:
errors say what went wrong without the address.
"""

import json
import logging
import math
from datetime import date

import httpx

log = logging.getLogger(__name__)
# httpx logs each request's full address (with the key) at INFO level; keep it quieter.
logging.getLogger("httpx").setLevel(logging.WARNING)

RELEASE_DATES_URL = "https://api.stlouisfed.org/fred/release/dates"
RELEASE_PAGE_URL = "https://fred.stlouisfed.org/release?rid={release_id}"
OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
SERIES_PAGE_URL = "https://fred.stlouisfed.org/series/{series_id}"

# The value in .env.example: treated as "no key yet".
_PLACEHOLDER_KEY = "your-fred-key-here"


class FredError(Exception):
    """FRED failed or isn't set up. The message is shown on the calendar page."""


class FredClient:
    def __init__(
        self,
        api_key: str,
        timeout_seconds: float = 15.0,
        transport: httpx.BaseTransport | None = None,  # tests only
    ) -> None:
        key = api_key.strip()
        self.is_set_up = bool(key) and key != _PLACEHOLDER_KEY
        self._key = key
        self._client = httpx.Client(transport=transport, timeout=timeout_seconds)

    def release_dates(self, release_id: int, start: date, end: date) -> list[date]:
        """The release's dates between two days (both included), including scheduled ones."""
        params = {
            "release_id": release_id,
            "realtime_start": start.isoformat(),
            "realtime_end": "9999-12-31",
            # Without this, FRED leaves out future dates (no data yet), which are the ones
            # a calendar needs.
            "include_release_dates_with_no_data": "true",
            "sort_order": "asc",
            "limit": 1000,
        }
        body = self._get(RELEASE_DATES_URL, params)
        return [d for d in dates_from_fred(body) if start <= d <= end]

    def series_observations(self, series_id: str, start: date) -> list[tuple[date, float]]:
        """A series' values from `start` on, oldest first, as (date, value). Days FRED has
        no value for (holidays) are left out."""
        params = {"series_id": series_id, "observation_start": start.isoformat()}
        try:
            body = self._get(OBSERVATIONS_URL, params)
        except FredError as exc:
            if "does not exist" in str(exc):
                raise FredError(
                    f"FRED has no series called {series_id}: check its id in config/macro.yaml."
                ) from exc
            raise
        return observations_from_fred(body)

    def _get(self, url: str, params: dict) -> bytes:
        if not self.is_set_up:
            raise FredError("set FRED_API_KEY in the .env file (free, see the README).")
        params = {**params, "api_key": self._key, "file_type": "json"}
        try:
            response = self._client.get(url, params=params)
        except httpx.TimeoutException as exc:
            raise FredError("FRED didn't answer in time.") from exc
        except httpx.HTTPError as exc:
            log.warning("FRED request failed (%s)", type(exc).__name__)
            raise FredError(f"Couldn't reach FRED ({type(exc).__name__}).") from exc
        if response.status_code in (400, 403) and "api_key" in response.text:
            raise FredError("FRED refused the key: check FRED_API_KEY in the .env file.")
        if response.status_code == 400 and "does not exist" in response.text.lower():
            raise FredError("FRED says that series does not exist.")
        if response.status_code == 429:
            raise FredError("FRED says we're asking too often; try again later.")
        if response.status_code >= 400:
            raise FredError(f"FRED answered with an error ({response.status_code}).")
        return response.content


def dates_from_fred(body: bytes) -> list[date]:
    """FRED's `{"release_dates": [{"release_id": 10, "date": "2026-11-12"}, ...]}` -> dates."""
    try:
        rows = json.loads(body)["release_dates"]
        return sorted({date.fromisoformat(row["date"]) for row in rows})
    except (ValueError, KeyError, TypeError) as exc:
        raise FredError("FRED sent something unexpected.") from exc


def observations_from_fred(body: bytes) -> list[tuple[date, float]]:
    """FRED's `{"observations": [{"date": "2026-11-06", "value": "0.52"}, ...]}` -> values.
    FRED writes "." for a day without a value; those are left out."""
    try:
        rows = json.loads(body)["observations"]
        found: dict[date, float] = {}
        for row in rows:
            raw = str(row["value"]).strip()
            if raw in (".", ""):
                continue
            value = float(raw)
            if math.isfinite(value):
                found[date.fromisoformat(row["date"])] = value
    except (ValueError, KeyError, TypeError) as exc:
        raise FredError("FRED sent something unexpected.") from exc
    return sorted(found.items())
