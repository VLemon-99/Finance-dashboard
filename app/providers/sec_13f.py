"""Quarterly 13F holdings of the funds the owner follows (SPEC F4.8), from SEC EDGAR.

Every investment manager with over $100 million in US stocks files a 13F report within 45
days of each quarter's end, listing what it held. Free, no key; the same SEC contact
(`SEC_USER_AGENT` in `.env`) and polite pace as the other SEC downloads.

For each fund (config/sources.yaml, `followed_funds`):
1. Its "submissions" file lists its filings; we take its two latest 13F reports
   (amendments are skipped).
2. Each report's folder holds an "information table" (XML) with one row per holding:
   issuer name, CUSIP (a 9-character security code), share count and dollar value.
   Reports never change, so each is downloaded once and kept while the app runs.

13F reports name stocks by issuer and CUSIP, not by ticker, so a stock is found by its
company name (abbreviations like "PETE" for "Petroleum" are spelled out). The owner can
give a ticker's CUSIP in config/sources.yaml when a name isn't recognized.
"""

import json
import logging
import re
import threading
import xml.etree.ElementTree as ET  # noqa: S405 (SEC XML; Python's parser limits entity expansion)
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.sources.base import Fetch, SourceError
from app.sources.edgar import looks_like_contact
from app.timeutils import utc_now

log = logging.getLogger(__name__)

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
FOLDER_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}"
REPORT_FORM = "13F-HR"
_SUBMISSIONS_MAX_AGE = timedelta(hours=12)


@dataclass(frozen=True)
class Position:
    issuer: str  # e.g. "AMAZON COM INC"
    title: str  # share class, e.g. "COM"
    cusip: str
    shares: float
    value: float  # dollars


@dataclass(frozen=True)
class FundReport:
    """One quarterly 13F report."""

    cik: int
    fund: str  # the SEC's name for the filer
    period: date  # the quarter end it reports
    filed: date
    url: str  # the filing's folder on EDGAR
    positions: tuple[Position, ...]

    @property
    def total_value(self) -> float:
        return sum(p.value for p in self.positions)


class ThirteenFClient:
    def __init__(self, fetch: Fetch, user_agent: str) -> None:
        self._fetch = fetch
        self._user_agent_ok = looks_like_contact(user_agent)
        self._reports: dict[str, FundReport] = {}  # accession -> report (never changes)
        self._listings: dict[int, tuple[datetime, list[dict]]] = {}
        self._lock = threading.Lock()

    def latest_reports(self, cik: int) -> tuple[FundReport | None, FundReport | None]:
        """(latest, previous) 13F reports of one fund. Raises SourceError."""
        if not self._user_agent_ok:
            raise SourceError(
                "SEC filings need your contact details: set SEC_USER_AGENT in the .env file."
            )
        filings = self._filings(cik)
        if not filings:
            raise SourceError("the SEC lists no 13F reports for it (check its CIK number).")
        reports = [self._report(cik, f) for f in filings[:2]]
        return reports[0], (reports[1] if len(reports) > 1 else None)

    def _filings(self, cik: int) -> list[dict]:
        with self._lock:
            cached = self._listings.get(cik)
        if cached and utc_now() - cached[0] < _SUBMISSIONS_MAX_AGE:
            return cached[1]
        filings = parse_fund_filings(self._fetch(SUBMISSIONS_URL.format(cik=cik)))
        with self._lock:
            self._listings[cik] = (utc_now(), filings)
        return filings

    def _report(self, cik: int, filing: dict) -> FundReport:
        with self._lock:
            found = self._reports.get(filing["accession"])
        if found is not None:
            return found
        folder = FOLDER_URL.format(cik=cik, folder=filing["accession"].replace("-", ""))
        table_name = information_table_name(self._fetch(f"{folder}/index.json"))
        positions = parse_information_table(self._fetch(f"{folder}/{table_name}"))
        report = FundReport(
            cik=cik,
            fund=filing["fund"],
            period=filing["period"],
            filed=filing["filed"],
            url=folder,
            positions=positions,
        )
        with self._lock:
            self._reports[filing["accession"]] = report
        return report


# --- reading SEC files (pure functions, tested with hand-made samples) ---------------


def parse_fund_filings(body: bytes) -> list[dict]:
    """A filer's submissions file -> its original 13F reports, newest first."""
    try:
        data = json.loads(body)
        recent = data["filings"]["recent"]
        rows = zip(
            recent["form"],
            recent["accessionNumber"],
            recent["filingDate"],
            recent["reportDate"],
            strict=True,
        )
        filings = [
            {
                "fund": str(data.get("name") or ""),
                "accession": accession,
                "filed": date.fromisoformat(filed),
                "period": date.fromisoformat(period),
            }
            for form, accession, filed, period in rows
            if form == REPORT_FORM and period
        ]
    except (ValueError, KeyError, TypeError) as exc:
        raise SourceError("the SEC's filing list wasn't in the expected format.") from exc
    return sorted(filings, key=lambda f: (f["period"], f["filed"]), reverse=True)


def information_table_name(body: bytes) -> str:
    """The holdings file in a 13F folder: the XML file that isn't the cover page."""
    try:
        items = json.loads(body)["directory"]["item"]
        xmls = [
            i
            for i in items
            if str(i["name"]).lower().endswith(".xml")
            and str(i["name"]).lower() != "primary_doc.xml"
        ]
    except (ValueError, KeyError, TypeError) as exc:
        raise SourceError("a 13F report's file list wasn't in the expected format.") from exc
    if not xmls:
        raise SourceError("a 13F report had no holdings file.")
    xmls.sort(key=lambda i: ("info" not in str(i["name"]).lower(), -_size(i.get("size"))))
    return str(xmls[0]["name"])


def parse_information_table(body: bytes) -> tuple[Position, ...]:
    """A 13F information table -> one Position per security (rows of the same CUSIP, e.g.
    held through several managers, are added up). Options (put/call rows) and bonds are
    left out: only shares count."""
    try:
        root = ET.fromstring(body)  # noqa: S314 (see the import note above)
    except ET.ParseError as exc:
        raise SourceError("a 13F holdings file couldn't be read.") from exc
    totals: dict[str, list] = {}
    for row in root.iter():
        if _local(row.tag) != "infoTable":
            continue
        fields = {_local(e.tag): (e.text or "").strip() for e in row.iter()}
        if fields.get("putCall") or fields.get("sshPrnamtType", "SH").upper() != "SH":
            continue
        cusip = fields.get("cusip", "").upper()
        shares, value = _float(fields.get("sshPrnamt")), _float(fields.get("value"))
        if not cusip or shares is None:
            continue
        entry = totals.setdefault(
            cusip, [fields.get("nameOfIssuer", ""), fields.get("titleOfClass", ""), 0.0, 0.0]
        )
        entry[2] += shares
        entry[3] += value or 0.0
    return tuple(
        Position(issuer=i, title=t, cusip=c, shares=s, value=v)
        for c, (i, t, s, v) in totals.items()
    )


# Words that don't help tell companies apart, and 13F abbreviations spelled out.
_DROP = {
    "INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COM", "COMPANY", "COS", "LTD",
    "LIMITED", "PLC", "NV", "SA", "AG", "SE", "LP", "LLC", "HLDGS", "HLDG", "HOLDINGS",
    "HOLDING", "GROUP", "GRP", "NEW", "DEL", "DE", "THE", "OF", "AND", "CL", "CLASS", "SHS",
    "ORD", "ADR", "SPONSORED", "SPON", "A", "B", "C",
}  # fmt: skip
_SPELLED_OUT = {
    "PETE": "PETROLEUM", "MTRS": "MOTORS", "INTL": "INTERNATIONAL",
    "INTERNATL": "INTERNATIONAL", "TECHNOLOGIES": "TECHNOLOGY", "TECH": "TECHNOLOGY",
    "SYS": "SYSTEMS", "COMMUNICATIONS": "COMMUNICATION", "MFG": "MANUFACTURING",
    "SVCS": "SERVICES", "SVC": "SERVICES", "FINL": "FINANCIAL", "PPTYS": "PROPERTIES",
    "RES": "RESOURCES", "ENTMT": "ENTERTAINMENT", "PHARMACEUTICALS": "PHARMACEUTICAL",
    "PHARMACEUTICA": "PHARMACEUTICAL", "LABS": "LABORATORIES", "AMER": "AMERICA",
    "AMERN": "AMERICAN", "NATL": "NATIONAL", "INDS": "INDUSTRIES", "MGMT": "MANAGEMENT",
    "PRODS": "PRODUCTS", "ELEC": "ELECTRIC", "INVTS": "INVESTMENTS", "SOLUTIONS": "SOLUTION",
    "BANCORPORATION": "BANCORP", "SEMICONDUCTOR": "SEMICONDUCTORS", "CMNTY": "COMMUNITY",
}  # fmt: skip


def issuer_key(name: str) -> str:
    """'AMAZON COM INC' and 'Amazon.com, Inc.' -> 'AMAZON'; 'OCCIDENTAL PETE CORP' and
    'Occidental Petroleum Corporation' -> 'OCCIDENTAL PETROLEUM'."""
    words = re.findall(r"[A-Z0-9]+", name.upper().replace("&", " AND "))
    kept = [_SPELLED_OUT.get(w, w) for w in words if w not in _DROP]
    return " ".join(kept)


def matching_cusips(
    reports: Iterable[FundReport | None], names: Iterable[str | None], cusip: str | None = None
) -> set[str]:
    """The CUSIPs in these reports that belong to a company: by any of its names (all share
    classes), or the CUSIP the owner set. Looking across both quarters keeps a holding
    recognizable when the 13F name changed (e.g. Facebook -> Meta)."""
    keys = {issuer_key(n) for n in names if n} - {""}
    found = {cusip.upper()} if cusip else set()
    for report in reports:
        for p in report.positions if report else ():
            if issuer_key(p.issuer) in keys:
                found.add(p.cusip)
    return found


def holding(report: FundReport | None, cusips: set[str]) -> tuple[float, float] | None:
    """(shares, value) the report shows for these CUSIPs, or None if it holds none."""
    if report is None:
        return None
    found = [p for p in report.positions if p.cusip in cusips]
    if not found:
        return None
    return sum(p.shares for p in found), sum(p.value for p in found)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _float(text: str | None) -> float | None:
    try:
        return float((text or "").replace(",", ""))
    except ValueError:
        return None


def _size(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
