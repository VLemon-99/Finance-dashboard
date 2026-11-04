"""Build research reports (SPEC F4): collect -> summarize -> compose -> store.

For each ticker:
1. Work out the period: the last `max_days` (7) days (F4.14, owner 2026-11-04).
2. Collect new items from every source (F4.11: failures are listed, not fatal).
3. Have the AI watch new YouTube videos (once per video, at most 10 per run; videos.py),
   then summarize and rate new items (public content only, F4.13).
4. Ask the AI for the "Major developments" and "Rumors & speculation" statements. Every
   statement must cite stories we gave it; anything uncited is dropped (F4.2).
5. Work out the sentiment score and insider totals in code (F4.6, F4.8).
6. Save the report as structured JSON. Pages render it; nothing is re-asked of the AI.

A whole watchlist run collects for every ticker first, then watches the run's new videos
together (a few at a time, newest first, so the per-run limit is shared fairly), then
finishes each ticker's report, and puts a short summary on top, built in code from the
ticker reports (so the list of tickers itself is never sent to the AI).
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Engine
from sqlmodel import Session, col, select

from app.ai.llm import LLM, AIError, Claim, PublicStory
from app.config import AppConfig, SourcesConfig
from app.db.models import Report, ReportRun, SourceItemRow
from app.services.collect import CollectResult, collect_for_ticker
from app.services.insider import insider_summary
from app.services.sentiment import sentiment_score
from app.services.summarize import is_form4, item_kind, summarize_pending
from app.services.videos import (
    AI_VIDEO_NOTE,
    VideoBudget,
    is_video,
    timestamp_label,
    unwatched_videos,
    watch_new_videos,
)
from app.sources.edgar import EdgarSource, base_form
from app.sources.registry import SourceSet
from app.sources.youtube import at_time
from app.timeutils import as_utc, format_eastern, utc_now

log = logging.getLogger(__name__)

HOLDER_FORMS = {"SC 13D", "SC 13G", "SCHEDULE 13D", "SCHEDULE 13G"}


@dataclass
class ReportServices:
    """Everything a report run needs from outside. Tests pass fakes."""

    sources: SourceSet
    llm: LLM | None  # None when the AI isn't set up
    llm_problem: str | None  # why there's no AI, shown in the report
    config: AppConfig
    sources_config: SourcesConfig


# --- runs (one ticker or a whole watchlist) ------------------------------------------


def start_run(
    session: Session,
    user_id: int,
    tickers: Sequence[str],
    label: str,
    watchlist_id: int | None = None,
) -> ReportRun:
    run = ReportRun(
        user_id=user_id,
        watchlist_id=watchlist_id,
        label=label[:100],
        total=len(tickers),
        started_at=utc_now(),
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def running_run(session: Session, user_id: int) -> ReportRun | None:
    return session.exec(
        select(ReportRun).where(ReportRun.user_id == user_id, ReportRun.status == "running")
    ).first()


def mark_interrupted_runs(session: Session) -> None:
    """At startup: runs still marked 'running' were cut off when the app stopped."""
    for run in session.exec(select(ReportRun).where(ReportRun.status == "running")).all():
        run.status = "failed"
        run.finished_at = utc_now()
        run.error = "The app stopped before this run finished."
    session.commit()


def execute_run(
    engine: Engine, run_id: int, tickers: Sequence[str], services: ReportServices
) -> None:
    """Make every ticker's report, then the summary. Safe to run in a background thread.

    Three passes: collect for every ticker, watch the run's new videos, finish each report.
    One ticker's surprise never loses the others."""
    with Session(engine) as session:
        run = session.get(ReportRun, run_id)
        if run is None:
            return
        reports: list[Report] = []
        try:
            prepared: list[PreparedTicker] = []
            for number, ticker in enumerate(tickers, start=1):
                _set_stage(
                    session, run, f"Collecting sources for {ticker} ({number} of {len(tickers)})"
                )
                try:
                    prepared.append(prepare_ticker(session, run, ticker, services))
                except Exception:
                    run = _ticker_failed(session, run_id, ticker)
                    run.done += 1
                    session.commit()

            notes = watch_videos(session, run, prepared, services)

            for item in prepared:
                _set_stage(session, run, "Writing reports")
                try:
                    reports.append(
                        finish_ticker_report(
                            session, run, item, services, notes.get(item.ticker, [])
                        )
                    )
                except Exception:
                    run = _ticker_failed(session, run_id, item.ticker)
                run.done += 1
                session.commit()
            summary = {k: v for k, v in run.summary.items() if k != "stage"}
            run.summary = {**summary, **watchlist_summary(reports)}
            run.status = "done"
        except Exception:
            session.rollback()
            log.exception("Report run %s failed", run_id)
            run = session.get(ReportRun, run_id)
            run.status = "failed"
            run.error = "Something unexpected went wrong (details in the app log)."
        run.finished_at = utc_now()
        session.commit()


def _set_stage(session: Session, run: ReportRun, text: str) -> None:
    """What the progress box says the run is doing."""
    if run.summary.get("stage") != text:
        run.summary = {**run.summary, "stage": text}
        session.commit()


def _ticker_failed(session: Session, run_id: int, ticker: str) -> ReportRun:
    session.rollback()
    log.exception("Report for %s failed", ticker)
    run = session.get(ReportRun, run_id)
    run.summary = {**run.summary, "failed": [*run.summary.get("failed", []), ticker]}
    return run


def watch_videos(
    session: Session,
    run: ReportRun,
    prepared: Sequence["PreparedTicker"],
    services: ReportServices,
    budget: VideoBudget | None = None,
) -> dict[str, list[str]]:
    """Have the AI watch the new videos of these tickers. Returns notes per ticker."""
    if services.llm is None or not prepared:
        return {}  # AI off: the report already says so; videos wait for a run with AI
    cfg = services.config.ai
    budget = budget or VideoBudget(cfg.max_videos_per_run)
    periods = {p.ticker: p.start for p in prepared}
    waiting = unwatched_videos(session, periods)
    if waiting:
        _set_stage(session, run, f"Watching new YouTube videos (up to {budget.remaining})")
    try:
        return watch_new_videos(session, services.llm, periods, cfg, budget)
    except Exception:  # videos stay unwatched and are tried again next run
        session.rollback()
        log.exception("Watching videos failed unexpectedly")
        return {
            p.ticker: [f"{AI_VIDEO_NOTE} unexpected error (details in the app log)."]
            for p in prepared
        }


def watchlist_summary(reports: Sequence[Report]) -> dict[str, Any]:
    """The short overview shown on top of a run (code-built, from the ticker reports)."""
    rows = []
    for report in reports:
        body = report.body
        top = body["developments"][0] if body["developments"] else None
        rows.append(
            {
                "ticker": report.ticker,
                "report_id": report.id,
                "score": body["sentiment"]["score"],
                "counts": body["sentiment"]["counts"],
                "top": top,
                "missing": len(report.missing),
            }
        )
    return {"tickers": rows}


# --- one ticker -----------------------------------------------------------------------


AI_NOTE_PREFIXES = ("AI summaries:", "AI report writing:", AI_VIDEO_NOTE)


def report_period(now: datetime, max_days: int) -> tuple[datetime, datetime]:
    """The last `max_days` days (owner 2026-11-04: every report covers the whole week, so
    nothing found late is missed, e.g. a video from a channel added after the last report).

    Nothing is paid for twice: stored items aren't downloaded again, items the AI has
    already summarized aren't sent again, and a video is never watched twice. Only the
    report's written statements are asked for again, from the stored summaries."""
    return now - timedelta(days=max_days), now


@dataclass
class PreparedTicker:
    """A ticker whose period is set and whose new items are collected."""

    ticker: str
    start: datetime
    end: datetime
    collected: CollectResult


def prepare_ticker(
    session: Session, run: ReportRun, ticker: str, services: ReportServices
) -> PreparedTicker:
    now = utc_now()
    start, end = report_period(now, services.config.reports.max_days)
    collected = collect_for_ticker(
        session, services.sources, ticker, start, services.config.reports
    )
    return PreparedTicker(ticker=ticker, start=start, end=end, collected=collected)


def build_ticker_report(
    session: Session, run: ReportRun, ticker: str, services: ReportServices
) -> Report:
    """One ticker from start to finish (collect, watch its videos, summarize, write)."""
    prepared = prepare_ticker(session, run, ticker, services)
    notes = watch_videos(session, run, [prepared], services)
    return finish_ticker_report(session, run, prepared, services, notes.get(ticker, []))


def finish_ticker_report(
    session: Session,
    run: ReportRun,
    prepared: PreparedTicker,
    services: ReportServices,
    video_notes: Sequence[str] = (),
) -> Report:
    cfg = services.config
    ticker, start, end = prepared.ticker, prepared.start, prepared.end
    now = end
    collected = prepared.collected
    missing = list(collected.missing) + list(video_notes)
    company = collected.company

    if services.llm is None:
        missing.append(f"AI summaries: {services.llm_problem or 'not set up.'}")
    else:
        missing += summarize_pending(session, services.llm, ticker, company, start, cfg.ai)

    stories = load_stories(session, ticker, start, end)
    developments, rumors = [], []
    if services.llm is not None:
        try:
            developments, rumors = write_statements(services.llm, ticker, company, stories, cfg)
        except AIError as exc:
            missing.append(f"AI report writing: {exc}")

    rated = [(s["source_type"], s["sentiment"]) for s in stories if s["sentiment"]]
    score = sentiment_score(rated, cfg.reports.sentiment_weights)
    lookback = services.sources_config.sec_edgar.insider_lookback_days

    body = {
        "ticker": ticker,
        "company": company,
        "developments": developments,
        "rumors": rumors,
        "insider": {
            **insider_section(session, ticker, now, lookback),
            # False when SEC filings weren't collected this time (not set up, down, or
            # switched off): the page then says "not available" instead of "none".
            "available": _sec_collected(services, collected.failed_sources),
        },
        "valuation": None,  # Phase 7 (F4.7)
        "sentiment": {
            "score": score.score,
            "counts": score.counts,
            "weights": dict(cfg.reports.sentiment_weights),
            "rated": len(rated),
        },
        "stories": stories,
        "ai_model": services.llm.model_name if services.llm else None,
        # False if any AI step failed or AI is off (kept for the record; every report
        # covers the last 7 days anyway, so the next one catches up).
        "ai_complete": not any(str(note).startswith(AI_NOTE_PREFIXES) for note in missing),
    }
    report = Report(
        user_id=run.user_id,
        run_id=run.id,
        ticker=ticker,
        generated_at=now,
        period_start=start,
        period_end=end,
        body=body,
        missing=missing,
    )
    session.add(report)
    session.commit()
    session.refresh(report)
    return report


def _sec_collected(services: ReportServices, failed_sources: set[str]) -> bool:
    names = {source.name for source in services.sources.sources}
    return EdgarSource.name in names and EdgarSource.name not in failed_sources


def load_stories(
    session: Session, ticker: str, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    """Stories first published in the period, newest first, each with all its sources (F4.12).

    Left out: Form 4s (they're in the insider section) and items the AI judged not to be
    about this ticker.
    """
    leaders = [
        row
        for row in session.exec(
            select(SourceItemRow)
            .where(
                SourceItemRow.ticker == ticker,
                SourceItemRow.story_id == SourceItemRow.id,
                SourceItemRow.published_at >= start,
                SourceItemRow.published_at <= end,
            )
            .order_by(col(SourceItemRow.published_at).desc())
        ).all()
        if not is_form4(row) and not (row.summarized_at is not None and row.summary is None)
    ]
    members: dict[int, list[SourceItemRow]] = {row.id: [] for row in leaders}
    if members:
        for row in session.exec(
            select(SourceItemRow)
            .where(col(SourceItemRow.story_id).in_(list(members)))
            .order_by(SourceItemRow.published_at)
        ).all():
            members[row.story_id].append(row)
    return [_story(leader, members[leader.id]) for leader in leaders]


def _story(leader: SourceItemRow, items: list[SourceItemRow]) -> dict[str, Any]:
    story = {
        "ref": leader.id,
        "kind": item_kind(leader.source_type),
        "source_type": leader.source_type,
        "title": leader.title,
        "form": leader.form,
        "published_at": as_utc(leader.published_at).isoformat(),
        "summary": leader.summary,
        "sentiment": leader.sentiment,
        "reason": leader.sentiment_reason,
        "is_rumor": bool(leader.is_rumor),
        "items": [_source_link(i) for i in items],
    }
    if is_video(leader):
        story["points"] = video_points(leader)
        story["watch_error"] = (leader.data or {}).get("watch_error")
    return story


def video_points(row: SourceItemRow) -> list[dict[str, Any]]:
    """The video's 3-5 points, each linked to its moment in the video (F4.5)."""
    return [
        {
            "text": p["text"],
            "time": timestamp_label(p["seconds"]) if p.get("seconds") is not None else None,
            "url": at_time(row.url, p.get("seconds")),
        }
        for p in (row.data or {}).get("points", [])
    ]


def _source_link(row: SourceItemRow) -> dict[str, Any]:
    """Title, outlet, date and URL: what every citation shows (F4.2)."""
    return {
        "id": row.id,
        "outlet": row.outlet,
        "title": row.title,
        "url": row.url,
        "published_at": as_utc(row.published_at).isoformat(),
    }


def write_statements(
    llm: LLM,
    ticker: str,
    company: str | None,
    stories: list[dict[str, Any]],
    cfg: AppConfig,
) -> tuple[list[dict], list[dict]]:
    """Ask the AI for the two written sections, keeping only properly cited statements."""
    summarized = [s for s in stories if s["summary"]]
    if not summarized:
        return [], []
    public = [
        PublicStory(
            ref=s["ref"],
            kind=s["kind"],
            title=s["title"],
            outlets=tuple(dict.fromkeys(i["outlet"] for i in s["items"])),
            published=format_eastern(datetime.fromisoformat(s["published_at"])),
            summary=s["summary"],
            is_rumor=s["is_rumor"],
        )
        for s in summarized
    ]
    draft = llm.write_report(ticker, company, public, cfg.ai.max_developments, cfg.ai.max_rumors)
    rumor_refs = {s["ref"] for s in summarized if s["is_rumor"]}
    fact_refs = {s["ref"] for s in summarized if not s["is_rumor"]}
    developments = cited(draft.developments, fact_refs, rumor_refs | fact_refs)
    rumors = cited(draft.rumors, rumor_refs, rumor_refs | fact_refs)
    return developments, rumors


def cited(claims: Sequence[Claim], must_include: set[int], allowed: set[int]) -> list[dict]:
    """Keep statements that cite only stories we sent, at least one of the right kind (F4.2).

    "Right kind": a development must rest on a confirmed story, a rumor on an unconfirmed one.
    """
    kept = []
    for claim in claims:
        refs = [r for r in claim.refs if r in allowed]
        if not claim.text or not refs or not set(refs) & must_include:
            log.info("Dropped an uncited or mis-cited statement: %r", claim.text[:80])
            continue
        kept.append({"text": claim.text, "refs": refs})
    return kept


def insider_section(
    session: Session, ticker: str, now: datetime, lookback_days: int
) -> dict[str, Any]:
    """Form 4 buys/sells and 13D/13G filings over the look-back window (F4.8)."""
    since = now - timedelta(days=lookback_days)
    rows = session.exec(
        select(SourceItemRow)
        .where(
            SourceItemRow.ticker == ticker,
            SourceItemRow.source_type == "filing",
            SourceItemRow.published_at >= since,
        )
        .order_by(col(SourceItemRow.published_at).desc())
    ).all()
    form4s = [r for r in rows if is_form4(r)]
    by_id = {r.id: r for r in form4s}
    summary = insider_summary(form4s)
    return {
        "lookback_days": lookback_days,
        "buys": summary.buys,
        "sells": summary.sells,
        "shares_bought": summary.shares_bought,
        "shares_sold": summary.shares_sold,
        "value_bought": summary.value_bought,
        "value_sold": summary.value_sold,
        "net_value": summary.net_value,
        "missing_prices": summary.missing_prices,
        "filings_without_trades": summary.filings_without_trades,
        "trades": [
            {
                "date": t.date,
                "name": t.name,
                "role": t.role,
                "kind": t.kind,
                "shares": t.shares,
                "price": t.price,
                "value": t.value,
                "plan_10b5_1": t.plan_10b5_1,
                "source": _source_link(by_id[t.ref]),
            }
            for t in summary.trades
        ],
        "holder_filings": [
            {"form": r.form, "source": _source_link(r)}
            for r in rows
            if base_form(r.form or "") in HOLDER_FORMS
        ],
    }
