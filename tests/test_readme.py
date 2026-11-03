"""The owner follows the README step by step, so its command lists must be complete.

Bug this guards against (2026-11-02): the "Updating to a new version" steps started with
"stop the app" but never gave the command to start it again, so after updating the owner's
browser showed "localhost refused to connect".
"""

import re

from app.config import PROJECT_ROOT

START_COMMAND = "uv run uvicorn app.main:app --reload"


def _section(title: str) -> str:
    text = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    start = text.index(f"**{title}**")
    following = re.search(r"\n\*\*[^*\n]+\*\*", text[start + len(title) + 4 :])
    end = start + len(title) + 4 + following.start() if following else len(text)
    return text[start:end]


def _commands(section: str) -> list[str]:
    return [block.strip() for block in re.findall(r"```bash\n(.*?)```", section, re.DOTALL)]


def test_update_steps_end_by_starting_the_app_again():
    commands = _commands(_section("Updating to a new version"))
    assert commands[-1] == START_COMMAND
    assert "uv run alembic upgrade head" in commands  # new tables before starting


def test_readme_start_command_matches_claude_md():
    claude_md = (PROJECT_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert START_COMMAND in claude_md
    assert _commands(_section("Every time")) == [START_COMMAND]
