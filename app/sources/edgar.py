"""SEC EDGAR filings: 8-K, 10-Q, 10-K, Form 4 (insider trades) and 13D/13G (SPEC F4.3, F4.8).

This is the only file that talks to the SEC. Free, no key, but every request must carry a
contact "User-Agent" (`SEC_USER_AGENT` in `.env`) and stay under 10 requests a second.

How it works:
1. The SEC's ticker list maps a ticker to the company's id number (CIK) and name.
2. The company's "submissions" file lists its recent filings (including Form 4s and
   13D/13Gs that others filed about it).
3. For new filings only (`enrich`), we download the document: the text for the AI, or for
   a Form 4, the trades themselves, which are read here in code (never by the AI).
"""

import json
import logging
import re
import threading
import xml.etree.ElementTree as ET  # noqa: S405 (SEC XML; Python's parser limits entity expansion)
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.config import EdgarConfig
from app.sources.base import Fetch, SourceError, SourceItem
from app.sources.text import html_to_text
from app.timeutils import EASTERN, utc_now

log = logging.getLogger(__name__)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/{name}"
OUTLET = "SEC EDGAR"

# Refresh the ticker -> company list once a day.
_TICKER_LIST_MAX_AGE = timedelta(hours=24)

FORM_LABELS = {
    "8-K": "Current report",
    "10-Q": "Quarterly report",
    "10-K": "Annual report",
    "4": "Insider trade report",
    "SC 13D": "Ownership report by a 5%+ holder planning to influence the company",
    "SCHEDULE 13D": "Ownership report by a 5%+ holder planning to influence the company",
    "SC 13G": "Ownership report by a passive 5%+ holder",
    "SCHEDULE 13G": "Ownership report by a passive 5%+ holder",
}

# What each numbered 8-K item is about (from the SEC's Form 8-K instructions).
EIGHT_K_ITEMS = {
    "1.01": "Entry into a material agreement",
    "1.02": "Termination of a material agreement",
    "1.03": "Bankruptcy or receivership",
    "1.05": "Material cybersecurity incident",
    "2.01": "Completed acquisition or sale of assets",
    "2.02": "Results of operations and financial condition",
    "2.03": "New debt or financial obligation",
    "2.04": "Event that speeds up a debt obligation",
    "2.05": "Costs of exiting or closing activities",
    "2.06": "Material impairment",
    "3.01": "Delisting notice or listing rule problem",
    "3.02": "Unregistered sale of shares",
    "3.03": "Change to shareholders' rights",
    "4.01": "Change of auditor",
    "4.02": "Earlier financial statements can no longer be relied on",
    "5.01": "Change in control",
    "5.02": "Director or officer change, or executive pay",
    "5.03": "Change to articles, bylaws or fiscal year",
    "5.07": "Shareholder vote results",
    "7.01": "Regulation FD disclosure",
    "8.01": "Other events",
    "9.01": "Financial statements and exhibits",
}

# Words dropped from company names for news searches: "Apple Inc." -> "Apple".
_NAME_SUFFIXES = re.compile(
    r"[\s,]+(inc|incorporated|corp|corporation|co|company|ltd|limited|plc|holdings?|"
    r"group|n\.?v|s\.?a|ag|se|lp|llc|class [a-c]|/[a-z]{2}/?)\.?$",
    re.IGNORECASE,
)
_EXHIBIT_99 = re.compile(r"ex-?99[\w.-]*\.(htm|html|txt)$", re.IGNORECASE)


def short_company_name(sec_name: str) -> str:
    """'Apple Inc.' -> 'Apple'; 'MICROSOFT CORP' -> 'MICROSOFT'; 'Alphabet Inc.' -> 'Alphabet'."""
    name = sec_name.strip()
    while True:
        shorter = _NAME_SUFFIXES.sub("", name).strip(" ,.")
        if shorter == name or not shorter:
            return name
        name = shorter


def base_form(form: str) -> str:
    """'8-K/A' (an amendment) -> '8-K'."""
    return form.removesuffix("/A")


def parse_acceptance_time(value: str) -> datetime:
    """EDGAR's 'acceptanceDateTime' ends in 'Z' but is really US Eastern clock time."""
    naive = datetime.fromisoformat(value.removesuffix("Z").split(".")[0])
    return naive.replace(tzinfo=EASTERN).astimezone(UTC)


class EdgarSource:
    name = "sec_edgar"
    label = "SEC EDGAR filings"
    source_type = "filing"

    def __init__(self, fetch: Fetch, config: EdgarConfig, user_agent: str) -> None:
        self._fetch = fetch
        self._config = config
        self._user_agent_ok = looks_like_contact(user_agent)
        self._companies: dict[str, tuple[int, str]] = {}
        self._companies_loaded_at: datetime | None = None
        self._lock = threading.Lock()

    # --- ticker -> company -------------------------------------------------------

    def company(self, ticker: str) -> tuple[int, str] | None:
        """(CIK, SEC company name) for a ticker, or None if the SEC doesn't list it (most ETFs)."""
        self._check_set_up()
        with self._lock:
            stale = (
                self._companies_loaded_at is None
                or utc_now() - self._companies_loaded_at > _TICKER_LIST_MAX_AGE
            )
            if stale:
                self._companies = parse_ticker_list(self._fetch(TICKERS_URL))
                self._companies_loaded_at = utc_now()
            return self._companies.get(ticker)

    def company_name(self, ticker: str) -> str | None:
        """Short company name for news searches, e.g. 'Apple'."""
        found = self.company(ticker)
        return short_company_name(found[1]) if found else None

    # --- the Source interface ----------------------------------------------------

    def fetch(self, ticker: str, company: str | None, since: datetime) -> list[SourceItem]:
        found = self.company(ticker)
        if found is None:
            return []  # not an SEC-registered company (e.g. most ETFs): nothing to collect
        cik, sec_name = found
        # Insider trades are shown for a longer window than the report period (F4.8).
        insider_since = min(since, utc_now() - timedelta(days=self._config.insider_lookback_days))
        filings = parse_submissions(self._fetch(SUBMISSIONS_URL.format(cik=cik)))
        wanted = set(self._config.forms)
        items = []
        for filing in filings:
            form = base_form(filing["form"])
            if form not in wanted:
                continue
            cutoff = insider_since if form == "4" else since
            if filing["accepted_at"] < cutoff:
                continue
            items.append(_item_from_filing(ticker, cik, sec_name, filing))
        return items

    def enrich(self, item: SourceItem) -> SourceItem:
        """Download the filing: Form 4 -> the trades; anything else -> its text for the AI."""
        if base_form(item.form or "") == "4":
            xml_url = item.data["xml_url"]
            trades = parse_form4(self._fetch(xml_url))
            return replace(item, data={**item.data, **trades}, text=form4_text(trades))
        parts = [html_to_text(_decode(self._fetch(item.url)))]
        if base_form(item.form or "") == "8-K":
            # The news in an 8-K is usually in its press-release exhibit (99.1).
            for url in self._exhibit_urls(item):
                try:
                    parts.append(html_to_text(_decode(self._fetch(url))))
                except SourceError as exc:
                    log.info("Skipped exhibit %s: %s", url, exc)
        text = "\n\n".join(p for p in parts if p)[: self._config.max_text_chars]
        return replace(item, text=text)

    def _exhibit_urls(self, item: SourceItem) -> list[str]:
        index_url = item.data["folder_url"] + "/index.json"
        try:
            listing = json.loads(self._fetch(index_url))
            names = [entry["name"] for entry in listing["directory"]["item"]]
        except (SourceError, ValueError, KeyError, TypeError) as exc:
            log.info("No exhibit list for %s: %s", item.url, exc)
            return []
        return [f"{item.data['folder_url']}/{n}" for n in names if _EXHIBIT_99.search(n)]

    def _check_set_up(self) -> None:
        if not self._user_agent_ok:
            raise SourceError(
                "SEC filings need your contact details: set SEC_USER_AGENT in the .env file."
            )


# --- reading SEC files (pure functions, tested with recorded samples) ----------------


def parse_ticker_list(body: bytes) -> dict[str, tuple[int, str]]:
    """The SEC's company_tickers.json -> {ticker: (CIK, company name)}."""
    try:
        rows = json.loads(body).values()
        return {
            str(r["ticker"]).upper().replace(".", "-"): (int(r["cik_str"]), str(r["title"]))
            for r in rows
        }
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise SourceError("The SEC's ticker list wasn't in the expected format.") from exc


def parse_submissions(body: bytes) -> list[dict[str, Any]]:
    """A company's submissions file -> one dict per recent filing."""
    try:
        recent = json.loads(body)["filings"]["recent"]
        columns = ("accessionNumber", "form", "acceptanceDateTime", "primaryDocument")
        rows = zip(*(recent[c] for c in columns), strict=True)
        items_col = recent.get("items") or [""] * len(recent["form"])
        descs = recent.get("primaryDocDescription") or [""] * len(recent["form"])
        return [
            {
                "accession": accession,
                "form": form,
                "accepted_at": parse_acceptance_time(accepted),
                "document": document,
                "items": items or "",
                "description": desc or "",
            }
            for (accession, form, accepted, document), items, desc in zip(
                rows, items_col, descs, strict=True
            )
        ]
    except (ValueError, KeyError, TypeError) as exc:
        raise SourceError("The SEC's filing list wasn't in the expected format.") from exc


def _item_from_filing(ticker: str, cik: int, sec_name: str, filing: dict) -> SourceItem:
    form = filing["form"]
    folder_url = ARCHIVE_URL.format(
        cik=cik, folder=filing["accession"].replace("-", ""), name=""
    ).rstrip("/")
    document = filing["document"]
    data: dict[str, Any] = {"accession": filing["accession"], "folder_url": folder_url}
    if base_form(form) == "4":
        # "xslF345X05/form4.xml" is the readable page; "form4.xml" is the raw data.
        data["xml_url"] = f"{folder_url}/{document.split('/')[-1]}"
    title = f"{sec_name}: {form_title(form, filing['items'])}"
    return SourceItem(
        source=EdgarSource.name,
        source_type="filing",
        ticker=ticker,
        outlet=OUTLET,
        title=title,
        url=f"{folder_url}/{document}",
        published_at=filing["accepted_at"],
        text=title,
        form=form,
        data=data,
    )


def form_title(form: str, items: str) -> str:
    """'8-K', '2.02,9.01' -> 'Form 8-K: Current report — Results of operations and ...'."""
    label = FORM_LABELS.get(base_form(form), "Filing")
    amended = " (amended)" if form.endswith("/A") else ""
    title = f"Form {base_form(form)}{amended}: {label}"
    if base_form(form) == "8-K" and items:
        topics = [
            EIGHT_K_ITEMS[i.strip()]
            for i in items.split(",")
            if i.strip() in EIGHT_K_ITEMS and i.strip() != "9.01"
        ]
        if topics:
            title += " — " + "; ".join(topics)
    return title


def parse_form4(body: bytes) -> dict[str, Any]:
    """Read a Form 4's raw XML: who traded, and each open-market buy or sale.

    Transaction codes (SEC Form 4 instructions): P = open-market purchase, S = open-market sale.
    Other codes (grants, option exercises, shares withheld for tax, gifts) are counted
    separately and not treated as buys or sells (SPEC F4.8 is about buys and sells).
    """
    try:
        root = ET.fromstring(body)  # noqa: S314 (see the import note above)
    except ET.ParseError as exc:
        raise SourceError("A Form 4 from the SEC couldn't be read.") from exc
    if root.tag != "ownershipDocument":
        raise SourceError("A Form 4 from the SEC wasn't in the expected format.")

    owners = []
    for owner in root.findall("reportingOwner"):
        name = _text(owner, "reportingOwnerId/rptOwnerName") or "Unknown"
        rel = owner.find("reportingOwnerRelationship")
        roles = []
        if rel is not None:
            if _flag(_text(rel, "isOfficer")):
                roles.append(_text(rel, "officerTitle") or "Officer")
            if _flag(_text(rel, "isDirector")):
                roles.append("Director")
            if _flag(_text(rel, "isTenPercentOwner")):
                roles.append("10% owner")
            if _flag(_text(rel, "isOther")):
                roles.append(_text(rel, "otherText") or "Other")
        owners.append({"name": name, "role": ", ".join(roles) or "Insider"})

    trades = []
    other_codes = 0
    for tx in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = _text(tx, "transactionCoding/transactionCode")
        if code not in ("P", "S"):
            other_codes += 1
            continue
        shares = _number(_text(tx, "transactionAmounts/transactionShares/value"))
        if shares is None or shares <= 0:
            continue
        trades.append(
            {
                "date": _date(_text(tx, "transactionDate/value")),
                "kind": "buy" if code == "P" else "sell",
                "shares": shares,
                "price": _number(_text(tx, "transactionAmounts/transactionPricePerShare/value")),
                "shares_after": _number(
                    _text(tx, "postTransactionAmounts/sharesOwnedFollowingTransaction/value")
                ),
            }
        )
    return {
        "owners": owners,
        "trades": trades,
        "other_transactions": other_codes,
        "plan_10b5_1": _flag(_text(root, "aff10b5One")),
    }


def form4_text(parsed: dict[str, Any]) -> str:
    """One plain line per Form 4, e.g. 'Jane Doe (CFO) sold 1,000 shares at $10.00'."""
    who = "; ".join(f"{o['name']} ({o['role']})" for o in parsed["owners"]) or "An insider"
    if not parsed["trades"]:
        return f"{who}: no open-market buys or sells (grants, exercises or other changes only)."
    parts = []
    for t in parsed["trades"]:
        verb = "bought" if t["kind"] == "buy" else "sold"
        price = f" at ${t['price']:,.2f}" if t["price"] else ""
        parts.append(f"{verb} {t['shares']:,.0f} shares{price} on {t['date']}")
    plan = " (pre-arranged 10b5-1 trading plan)" if parsed["plan_10b5_1"] else ""
    return f"{who} " + "; ".join(parts) + plan + "."


def _text(node: ET.Element, path: str) -> str | None:
    found = node.find(path)
    if found is None or found.text is None:
        return None
    return found.text.strip() or None


def _flag(value: str | None) -> bool:
    return (value or "").lower() in ("1", "true")


def _number(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _date(value: str | None) -> str | None:
    """Form 4 dates are 'YYYY-MM-DD', sometimes with a time-zone tail. Stored as 'YYYY-MM-DD'."""
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return None


def _decode(body: bytes) -> str:
    return body.decode("utf-8", errors="replace")


def looks_like_contact(user_agent: str) -> bool:
    """The SEC wants a name and email. Reject the empty value and the .env.example placeholder."""
    ua = user_agent.strip()
    return "@" in ua and "example.com" not in ua and " " in ua
