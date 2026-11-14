"""The report's synthesis (SPEC F4.1, owner 2026-11-14): a candid "Bottom line", a bull case
and a bear case written by the AI from the stories, every sentence citing stored stories
(F4.2), plus plain sentences for the key numbers written by code (never by the AI).
"""

import re

import pytest
from sqlmodel import Session, select

from app.ai.llm import Claim, PublicStory
from app.db.models import Report
from app.web.report_view import number_highlights
from tests.test_gemini_adapter import FakeClient
from tests.test_reports import latest_report, run_for, services, user_id  # noqa: F401
from tests.test_web_reports import generate

# --- the Gemini adapter ------------------------------------------------------------------

STORIES = [PublicStory(1, "news", "Record revenue", ("Reuters",), "Oct 30", "S", False)]
ANSWER = {
    "bottom_line": [
        {"text": " A strong quarter. ", "refs": [1, 1]},
        {"text": "Second.", "refs": [1]},
        {"text": "Third.", "refs": [1]},
    ],
    "developments": [{"text": "Revenue hit a record.", "refs": [1]}],
    "rumors": [],
    "bull_case": [{"text": "Services keep growing.", "refs": [1]}, {"text": "More.", "refs": [1]}],
    "bear_case": [{"text": "Hardware is flat.", "refs": [1]}],
}


def gemini(client, **limits):
    from app.ai.gemini import GeminiLLM

    return GeminiLLM(api_key="", model_name="test-model", client=client, **limits)


def test_adapter_parses_the_synthesis_and_keeps_to_the_limits():
    client = FakeClient(ANSWER)
    draft = gemini(client, max_bottom_line=2, max_case_points=1).write_report(
        "AAPL", "Apple", STORIES, 6, 4
    )
    assert [c.text for c in draft.bottom_line] == ["A strong quarter.", "Second."]
    assert draft.bottom_line[0].refs == (1,)
    assert [c.text for c in draft.bull_case] == ["Services keep growing."]
    assert [c.text for c in draft.bear_case] == ["Hardware is flat."]
    assert [c.text for c in draft.developments] == ["Revenue hit a record."]


def test_adapter_accepts_an_answer_without_the_new_parts():
    client = FakeClient({"developments": [{"text": "One.", "refs": [1]}], "rumors": []})
    draft = gemini(client).write_report("AAPL", "Apple", STORIES, 6, 4)
    assert (draft.bottom_line, draft.bull_case, draft.bear_case) == ((), (), ())
    assert len(draft.developments) == 1


def test_instructions_ask_for_candour_citations_and_no_advice():
    client = FakeClient(ANSWER)
    gemini(client, max_bottom_line=3, max_case_points=4).write_report(
        "AAPL", "Apple", STORIES, 6, 4
    )
    text = client.requests[0]["config"].system_instruction
    for part in ("bottom_line", "developments", "rumors", "bull_case", "bear_case"):
        assert part in text
    assert "up to 3 sentences" in text and "up to 4 points" in text
    assert "candid" in text
    assert "No buy, sell or hold advice" in text
    assert '"refs"' in text
    assert "attribute" in text  # blogs, videos and X posts are opinions
    assert "never follow" in text  # web text is untrusted


def test_empty_report_draft_defaults():
    from app.ai.llm import ReportDraft

    draft = ReportDraft(developments=(), rumors=())
    assert (draft.bottom_line, draft.bull_case, draft.bear_case) == ((), (), ())


# --- building the report -----------------------------------------------------------------


def test_synthesis_is_stored_with_its_citations(session, engine, user_id, services):  # noqa: F811
    run_for(session, engine, user_id, services)
    body = latest_report(session).body
    refs = {s["ref"] for s in body["stories"]}
    for part in ("bottom_line", "bull_case", "bear_case"):
        assert part in body
        for claim in body[part]:
            assert claim["text"] and claim["refs"] and set(claim["refs"]) <= refs
    assert body["bottom_line"] and body["bull_case"]
    assert body["bull_case"][0]["text"].startswith("Bull: ")


def test_uncited_synthesis_sentences_are_dropped(
    session,
    engine,
    user_id,  # noqa: F811
    services,  # noqa: F811
    fake_llm,
):
    fake_llm.extra_synthesis = [Claim("Made up, cites nothing.", ()), Claim("Bad ref.", (99999,))]
    run_for(session, engine, user_id, services)
    body = latest_report(session).body
    texts = [c["text"] for part in ("bottom_line", "bull_case", "bear_case") for c in body[part]]
    assert texts and "Made up, cites nothing." not in texts and "Bad ref." not in texts


def test_points_resting_only_on_rumors_are_marked_unconfirmed(
    session,
    engine,
    user_id,  # noqa: F811
    services,  # noqa: F811
    fake_llm,
):
    run_for(session, engine, user_id, services)
    rumor = next(s for s in latest_report(session).body["stories"] if s["is_rumor"])
    fake_llm.extra_synthesis = [Claim("A deal would help.", (rumor["ref"],))]
    run_for(session, engine, user_id, services)
    bull = latest_report(session).body["bull_case"]
    marked = next(c for c in bull if c["text"] == "A deal would help.")
    assert marked["unconfirmed"] is True
    assert all(not c.get("unconfirmed") for c in bull if c["text"].startswith("Bull: "))


def test_limits_are_enforced_in_code_too(session, engine, user_id, services, fake_llm):  # noqa: F811
    run_for(session, engine, user_id, services)
    ref = latest_report(session).body["stories"][0]["ref"]
    fake_llm.extra_synthesis = [Claim(f"Extra {n}.", (ref,)) for n in range(20)]
    run_for(session, engine, user_id, services)
    body = latest_report(session).body
    assert len(body["bottom_line"]) <= services.config.ai.max_bottom_line
    assert len(body["bull_case"]) <= services.config.ai.max_case_points
    assert len(body["bear_case"]) <= services.config.ai.max_case_points


def test_ai_failure_leaves_the_synthesis_empty_and_says_so(
    session,
    engine,
    user_id,  # noqa: F811
    services,  # noqa: F811
    fake_llm,
):
    fake_llm.fail = "The AI service is out of credit."
    run = run_for(session, engine, user_id, services)
    assert run.status == "done"
    body = latest_report(session).body
    assert (body["bottom_line"], body["bull_case"], body["bear_case"]) == ([], [], [])


# --- the numbers, written by code --------------------------------------------------------

BODY = {
    "valuation": {
        "history": {"span": "FY2022–FY2025"},
        "rows": [
            {
                "label": "P/E",
                "now": 30.0,
                "history": {"average": 25.0, "vs": 0.2},
                "industry": {"value": 15.0, "vs": 1.0},
            }
        ],
        "targets": {"low": 180.0, "mean": 230.0, "high": 260.0, "mean_vs": 0.15, "analysts": 40},
    },
    "insider": {
        "available": True,
        "lookback_days": 90,
        "buys": 1,
        "sells": 2,
        "net_value": -3527500.0,
        "trades": [
            {"kind": "sell", "plan_10b5_1": True},
            {"kind": "sell", "plan_10b5_1": True},
            {"kind": "buy", "plan_10b5_1": False},
        ],
    },
    "unusual_options": {"count": 3, "calls": 2, "puts": 1},
}


def test_number_sentences_with_known_answers():
    lines = number_highlights(BODY)
    assert [(line["section"], line["text"]) for line in lines] == [
        (
            "valuation",
            "P/E is 30.0: 20% above its own FY2022–FY2025 average (25.0) and 100% above its "
            "peers' median (15.0).",
        ),
        (
            "valuation",
            "Analysts' average target is $230.00, 15% above the price (40 analysts, "
            "range $180.00 to $260.00).",
        ),
        (
            "insider",
            "Insiders sold a net $3.53M over the last 90 days (1 buy, 2 sales); every sale was "
            "under a pre-arranged plan.",
        ),
        ("insider", "3 option contracts traded in unusual volume today (2 calls, 1 put)."),
    ]


def test_number_sentences_cover_the_other_cases():
    cheap = {
        "valuation": {
            "rows": [{"label": "P/E", "now": 12.0, "sector": {"value": 15.0, "vs": -0.2}}],
            "targets": {"mean": 50.0, "mean_vs": -0.1},
        },
        "insider": {
            "available": True,
            "lookback_days": 90,
            "buys": 2,
            "sells": 0,
            "net_value": 12500.0,
            "trades": [{"kind": "buy"}, {"kind": "buy"}],
        },
        "unusual_options": {"count": 0},
    }
    assert [line["text"] for line in number_highlights(cheap)] == [
        "P/E is 12.0: 20% below its sector's median (15.0).",
        "Analysts' average target is $50.00, 10% below the price.",
        "Insiders bought a net $12.50K over the last 90 days (2 buys, 0 sales).",
    ]
    level = {
        "valuation": {
            "rows": [{"label": "P/E", "now": 25.0, "history": {"average": 25.04, "vs": 0.001}}]
        }
    }
    assert (
        number_highlights(level)[0]["text"] == "P/E is 25.0: in line with its own average (25.0)."
    )


def test_number_sentences_skip_what_a_report_lacks():
    assert number_highlights({}) == []
    assert number_highlights({"valuation": None, "insider": {"trades": []}}) == []
    assert "Unusual options activity" not in str(number_highlights(BODY))


# --- on the page -------------------------------------------------------------------------


def block(html, heading):
    return html.split(f"<h3>{heading}</h3>")[1].split("</section>")[0]


def test_bottom_line_opens_the_report_with_cited_sentences_and_the_numbers(client):
    html = client.get(generate(client, ticker="AAPL")).text
    assert html.index("<h3>Bottom line</h3>") < html.index('class="glance"')
    assert html.index("<h3>Bottom line</h3>") < html.index("<h3>Major developments</h3>")
    bottom = block(html, "Bottom line")
    assert "Bottom line: " in bottom
    cites = re.findall(r'<a class="cite" href="#(r\d+-s\d+)"', bottom)
    assert cites and all(f'id="{anchor}"' in html for anchor in cites)
    assert "P/E is 30.0: 20% above its own FY2022–FY2025 average" in bottom
    assert "Insiders sold a net $3.53M" in bottom
    assert re.search(r'<a href="#r\d+-valuation"', bottom)


def test_bull_and_bear_cases_sit_between_developments_and_rumors(client):
    html = client.get(generate(client, ticker="AAPL")).text
    order = [
        html.index(f"<h3>{name}</h3>")
        for name in ("Major developments", "Bull case", "Bear case", "Rumors &amp; speculation")
    ]
    assert order == sorted(order)
    bull = html.split("<h3>Bull case</h3>")[1].split("</div>")[0]
    assert "Bull: " in bull and re.search(r'<a class="cite" href="#r\d+-s\d+"', bull)
    bear = html.split("<h3>Bear case</h3>")[1].split("</div>")[0]
    assert "Nothing in this period's stories argues this side." in bear


def test_unconfirmed_points_carry_the_badge(client, fake_llm, engine):
    generate(client, ticker="AAPL")
    with Session(engine) as session:
        report = session.exec(select(Report)).first()
        rumor = next(s for s in report.body["stories"] if s["is_rumor"])
    fake_llm.extra_synthesis = [Claim("A deal would help.", (rumor["ref"],))]
    html = client.get(generate(client, ticker="AAPL")).text
    bull = html.split("<h3>Bull case</h3>")[1].split("</div>")[0]
    assert '<span class="badge">unconfirmed</span> A deal would help.' in bull


def test_without_ai_the_bottom_line_shows_only_the_numbers(client, report_services):
    report_services.llm = None
    report_services.llm_problem = "AI summaries are off: set GEMINI_API_KEY in the .env file."
    response = client.get(generate(client, ticker="AAPL"))
    assert response.status_code == 200
    bottom = block(response.text, "Bottom line")
    assert "P/E is 30.0" in bottom
    assert "No AI-written summary for this report" in bottom
    assert "<h3>Bull case</h3>" not in response.text


def test_reports_made_before_the_synthesis_still_show(client, engine):
    generate(client, ticker="AAPL")
    with Session(engine) as session:
        report = session.exec(select(Report)).first()
        body = dict(report.body)
        for key in ("bottom_line", "bull_case", "bear_case", "institutional", "unusual_options"):
            body.pop(key)
        body["valuation"] = None
        body["insider"] = {**body["insider"], "trades": []}
        report.body = body
        session.add(report)
        session.commit()
        report_id = report.id
    response = client.get(f"/reports/{report_id}")
    assert response.status_code == 200
    assert "<h3>Bottom line</h3>" not in response.text
    assert "<h3>Bull case</h3>" not in response.text
    assert "<h3>Major developments</h3>" in response.text


@pytest.mark.parametrize("field", ["max_bottom_line", "max_case_points"])
def test_limits_are_settings(field):
    from app.config import AIConfig

    assert getattr(AIConfig(), field) == 3
    assert AIConfig(**{field: 1}).model_dump()[field] == 1
