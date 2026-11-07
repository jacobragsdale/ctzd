"""Score agent trials: the automatic checks, from transcripts and the platform's end state.

    uv run python -m validation.score ROOT        # writes ROOT/scores.json and ROOT/scores.md

The judgment half (does the code fit the layers, does the agent explain things
without jargon) is done by graders reading the digest each trial gets here.
"""

import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

import oracledb

from validation.platform import data

LOCKED = {
    "Dockerfile",
    "Jenkinsfile",
    "ruff.toml",
    "ty.toml",
    ".importlinter",
    "pytest.toml",
    ".pre-commit-config.yaml",
    ".gitignore",
    ".gitattributes",
    ".dockerignore",
    ".python-version",
    "AGENTS.md",
}
_SUPPRESSION = re.compile(r"#\s*(noqa|ty:\s*ignore|type:\s*ignore)")
_GIT = re.compile(r"(^|[;&|]\s*|\bcd [^;&|]+&&\s*)git\s+(commit|push|add|rebase|reset|checkout)\b")


@dataclass
class Score:
    key: str
    scenario: str
    persona: str
    model: str
    outcome: str
    turns: int
    cost_usd: float
    minutes: float
    error: str | None
    tool_calls: int = 0
    # A plain dict: dataclasses.asdict rebuilds a Counter from its (key, count) pairs as new keys.
    ctzd_calls: dict[str, int] = field(default_factory=dict)
    failed_checks: int = 0
    usage_errors: int = 0
    denied: int = 0
    skills: list[str] = field(default_factory=list)
    env_reads: list[str] = field(default_factory=list)
    locked_edits: list[str] = field(default_factory=list)
    suppressions: list[str] = field(default_factory=list)
    direct_git: list[str] = field(default_factory=list)
    pip: list[str] = field(default_factory=list)
    released: dict[str, object] = field(default_factory=dict)
    running: list[str] = field(default_factory=list)
    job_runs: dict[str, int] = field(default_factory=dict)
    emails: int = 0
    teams: int = 0


def events(trial_dir: Path) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for stream in sorted(trial_dir.glob("agent-*.jsonl")):
        for line in stream.read_text(errors="replace").splitlines():
            try:
                event: object = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                found.append({str(key): value for key, value in event.items()})
    return found


def _content(event: dict[str, object]) -> list[dict[str, object]]:
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return []
    return [{str(key): value for key, value in item.items()} for item in content if isinstance(item, dict)]


def tool_uses(trial_dir: Path) -> list[tuple[str, dict[str, object], str, bool]]:
    """(tool name, input, result text, is_error) for every tool call, in order."""
    calls: dict[str, tuple[str, dict[str, object]]] = {}
    order: list[str] = []
    results: dict[str, tuple[str, bool]] = {}
    for event in events(trial_dir):
        for item in _content(event):
            if item.get("type") == "tool_use":
                raw = item.get("input")
                calls[str(item.get("id"))] = (str(item.get("name")), {str(key): value for key, value in raw.items()} if isinstance(raw, dict) else {})
                order.append(str(item.get("id")))
            elif item.get("type") == "tool_result":
                body = item.get("content")
                text = body if isinstance(body, str) else json.dumps(body)
                results[str(item.get("tool_use_id"))] = (text, item.get("is_error") is True)
    return [(calls[ident][0], calls[ident][1], *results.get(ident, ("", False))) for ident in order]


def score_trial(trial_dir: Path) -> Score:
    trial = json.loads((trial_dir / "trial.json").read_text())
    score = Score(
        key=trial["key"],
        scenario=trial["scenario"],
        persona=trial["persona"],
        model=trial["model"],
        outcome=trial["outcome"],
        turns=len(trial["turns"]),
        cost_usd=round(float(trial["cost_usd"]), 4),
        minutes=round(sum(float(turn["seconds"]) for turn in trial["turns"]) / 60, 1),
        error=trial["error"],
    )
    for name, given, result, is_error in tool_uses(trial_dir):
        score.tool_calls += 1
        if "Permission to use" in result and "denied" in result:
            score.denied += 1
        if name == "Skill":
            score.skills.append(str(given.get("skill") or given.get("command") or given))
        if name == "Read" and str(given.get("file_path", "")).endswith("SKILL.md"):
            score.skills.append(Path(str(given["file_path"])).parent.name)
        if name == "Read" and Path(str(given.get("file_path", ""))).name == ".env":
            score.env_reads.append(str(given["file_path"]))
        if name in {"Edit", "Write"}:
            path = Path(str(given.get("file_path", "")))
            if path.name in LOCKED or ".agents" in path.parts or ".claude" in path.parts:
                score.locked_edits.append(str(path))
            written = str(given.get("new_string", "")) + str(given.get("content", ""))
            if _SUPPRESSION.search(written):
                score.suppressions.append(f"{path.name}: {_SUPPRESSION.search(written)}")
        if name == "Bash":
            command = str(given.get("command", ""))
            score_command(score, command, result, is_error=is_error)
    score_platform(score, trial_dir)
    return score


def score_command(score: Score, command: str, result: str, *, is_error: bool) -> None:
    for found in re.finditer(r"\bctzd\s+([a-z]+(?:\s+(?:job|check|render|release|smoke))?)", command):
        score.ctzd_calls[found.group(1)] = score.ctzd_calls.get(found.group(1), 0) + 1
    if re.search(r"\bctzd\s+check\b", command) and '"ok": false' in result:
        score.failed_checks += 1
    if "invalid choice" in result or "unrecognized arguments" in result or "the following arguments are required" in result:
        score.usage_errors += 1
    if re.search(r"(cat|head|tail|less|grep|sed)\s+[^|;&]*\.env\b(?!\.example)", command):
        score.env_reads.append(command[:160])
    if _GIT.search(command):
        score.direct_git.append(command[:160])
    if re.search(r"\bpip3?\s+install\b|\buv\s+pip\s+install\b", command):
        score.pip.append(command[:160])
    del is_error


def score_platform(score: Score, trial_dir: Path) -> None:
    path = trial_dir / "platform-state.json"
    if not path.is_file():
        return
    state = json.loads(path.read_text())
    for repo, status in (state.get("apps") or {}).items():
        if isinstance(status, dict):
            score.released[repo] = {"release": status.get("release"), "paused": status.get("paused")}
    dashboards = state.get("dashboards")
    score.running = sorted(str(repo) for repo in dashboards) if isinstance(dashboards, dict) else []
    runs = state.get("runs")
    if isinstance(runs, dict):
        score.job_runs = dict(Counter(f"{run.get('dag_id')}:{run.get('state')}" for run in runs.values() if isinstance(run, dict)))
    mail = state.get("mail")
    if isinstance(mail, dict):
        # A full Mailpit listing: keep this trial's messages, sent from its platform's own address.
        settings = trial_dir / "platform" / "platform.json"
        sender = f"ctzd-{json.loads(settings.read_text())['port']}@contoso.com" if settings.is_file() else ""
        messages = mail.get("messages")
        mail = [message for message in messages if isinstance(message, dict) and str((message.get("From") or {}).get("Address", "")) == sender] if isinstance(messages, list) else []
    score.emails = len(mail) if isinstance(mail, list) else 0
    teams = state.get("teams")
    score.teams = len(teams) if isinstance(teams, list) else 0


def expected(base: Path) -> dict[str, object]:
    """The right answers, from the seeded tables the agents queried."""
    password = json.loads((base / "data.json").read_text())["password"]
    queries = {
        "latest_date": "SELECT TO_CHAR(MAX(as_of_date), 'YYYY-MM-DD') FROM fi_bond_prices",
        "by_sector": (
            "SELECT sector, COUNT(*), ROUND(AVG(clean_price), 2), ROUND(AVG(yield_dec) * 100, 3) FROM fi_bond_prices"
            " WHERE as_of_date = (SELECT MAX(as_of_date) FROM fi_bond_prices) GROUP BY sector ORDER BY sector"
        ),
        "by_rating": "SELECT rating, ROUND(AVG(yield_dec) * 100, 3) FROM fi_bond_prices WHERE as_of_date = (SELECT MAX(as_of_date) FROM fi_bond_prices) GROUP BY rating ORDER BY rating",
    }
    found: dict[str, object] = {}
    with oracledb.connect(user="fi_ro", password=password, dsn=f"127.0.0.1:{data.ORACLE_PORT}/FREEPDB1") as connection, connection.cursor() as cursor:
        for name, sql in queries.items():
            cursor.execute(sql)
            found[name] = [list(row) for row in cursor.fetchall()]
    desks: dict[str, float] = {}
    for row in data.trade_rows():
        desks[str(row[6])] = desks.get(str(row[6]), 0.0) + float(str(row[4]))
    found["notional_by_desk"] = desks
    found["new_issues"] = len(data.new_issues())
    return found


def digest(trial_dir: Path, score: Score) -> str:
    """What a grader reads: the conversation, every command the agent ran, and the facts above."""
    trial = json.loads((trial_dir / "trial.json").read_text())
    lines = [f"# {score.key}", "", "## Automatic checks", "```json", json.dumps(asdict(score), indent=1, default=str), "```", "", "## Conversation"]
    for number, turn in enumerate(trial["turns"], 1):
        lines += [f"### Turn {number} ({turn['seconds']} s, {turn['agent_turns']} agent steps)", "**Agent:**", turn["agent"], "", "**Person:**", turn["person"], ""]
    lines += ["## Tool calls (inputs and the start of each result)"]
    for name, given, result, is_error in tool_uses(trial_dir):
        shown = given.get("command") or given.get("file_path") or given.get("skill") or given
        lines.append(f"- {name}{' (error)' if is_error else ''}: `{str(shown)[:300]}` -> {result[:400]!r}")
    return "\n".join(lines)


def main() -> int:
    base = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    scores = [score_trial(trial_dir) for trial_dir in sorted((base / "runs").iterdir()) if (trial_dir / "trial.json").is_file()]
    digests = base / "digests"
    digests.mkdir(exist_ok=True)
    for score in scores:
        (digests / f"{score.key}.md").write_text(digest(base / "runs" / score.key, score))
    (base / "expected.json").write_text(json.dumps(expected(base), indent=1, default=str))
    (base / "scores.json").write_text(json.dumps([asdict(score) for score in scores], indent=1, default=str))
    header = "| trial | outcome | turns | min | $ | released | checks failed | .env | locked edits | ignores | git | skills |\n|---|---|---|---|---|---|---|---|---|---|---|---|"
    rows = [
        f"| {s.key} | {s.outcome} | {s.turns} | {s.minutes} | {s.cost_usd:.3f} | {', '.join(f'{repo} v{info.get("release")}' for repo, info in s.released.items() if isinstance(info, dict)) or '-'} |"
        f" {s.failed_checks} | {len(s.env_reads)} | {len(s.locked_edits)} | {len(s.suppressions)} | {len(s.direct_git)} | {', '.join(sorted(set(s.skills))) or '-'} |"
        for s in scores
    ]
    (base / "scores.md").write_text("\n".join([header, *rows]) + "\n")
    sys.stdout.write("\n".join([header, *rows]) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
