"""Does a blog post or video mention a ticker? Used by the blog and YouTube plug-ins
(SPEC F4.5: "mention the ticker or company in the title or description").

Counts as a mention, as a whole word:
- the ticker in capitals, with or without a "$" ("AMZN", "$AMZN"). Tickers of one or two
  letters only count with the "$" ("$F"), because "ON", "IT" or "AI" appear in many titles.
  "BRK-B" also matches "BRK.B".
- the company's short SEC name, in any case ("Apple", "AMAZON COM" -> also "Amazon").
- any extra name from `also_known_as` in config/sources.yaml (e.g. "Google" for GOOGL).
"""

import re
from collections.abc import Iterable

_EDGE_BEFORE = r"(?<![A-Za-z0-9])"
_EDGE_AFTER = r"(?![A-Za-z0-9])"


def _ticker_pattern(ticker: str) -> re.Pattern[str]:
    escaped = re.escape(ticker.upper()).replace(r"\-", "[-.]")
    dollar = r"\$" if len(ticker) <= 2 else r"\$?"
    return re.compile(f"{_EDGE_BEFORE}{dollar}{escaped}{_EDGE_AFTER}")


def _name_pattern(name: str) -> re.Pattern[str]:
    words = [re.escape(w) for w in name.split()]
    return re.compile(_EDGE_BEFORE + r"\s+".join(words) + _EDGE_AFTER, re.IGNORECASE)


def company_names(company: str | None, extra: Iterable[str] = ()) -> list[str]:
    """'AMAZON COM' -> ['AMAZON COM', 'AMAZON']: the SEC writes Amazon.com as 'AMAZON COM'."""
    names = []
    if company:
        names.append(company)
        shorter = re.sub(r"[\s.]+com$", "", company, flags=re.IGNORECASE).strip()
        if shorter and shorter != company:
            names.append(shorter)
    names += [n.strip() for n in extra if n and n.strip()]
    return list(dict.fromkeys(names))


def mentions(text: str, ticker: str, names: Iterable[str]) -> bool:
    if _ticker_pattern(ticker).search(text):
        return True
    return any(_name_pattern(name).search(text) for name in names)
