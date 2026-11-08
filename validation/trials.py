"""Stage 3: agent trials against the local platform.

    uv run python -m validation.trials probe ROOT                      # the real flow, scripted, no agent
    uv run python -m validation.trials run ROOT SCENARIO[:PERSONA] ... [--model M] [--reps N] [--parallel P]

ROOT holds data.json from `validation.platform.serve data` (or `fake-data`), the
trial `ctzd` and pnpm in ROOT/tool/bin. Each trial gets its own platform, a fresh
folder holding only the bootstrap `ctzd` skill, and a headless Claude Code agent.
A second model plays the person, turn by turn.
"""

import argparse
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx

from validation.harness import REPO, Report, tool
from validation.scenarios import CANARY, CANARY_IN_APP, PERSONAS, SCENARIOS, Scenario

TOOL_BIN = "tool/bin"
BOOTSTRAP = REPO / "bootstrap" / "skills" / "ctzd" / "SKILL.md"
FIXTURES = REPO / "validation" / "fixtures"
AGENT_TIMEOUT = 2700.0
PERSON_TIMEOUT = 300.0
AGENT_BUDGET_USD = 4.0
TRIAL_BUDGET_USD = 12.0
SECOND_RELEASE = 2
DONE = "[[DONE]]"
GIVE_UP = "[[GIVE UP]]"
# A person at a laptop approves what their agent asks to run, so trials allow commands broadly. The deny
# rules keep agents away from the operator's own files and from destructive commands.
ALLOWED = ("Bash", "Read(./**)", "Edit(./**)", "Write(./**)", "Glob", "Grep", "Skill", "TodoWrite")
DENIED = (
    "Read(**/.env)",
    "Read(//**/.env)",
    *(f"{tool}(/{Path.home() / folder}/**)" for tool in ("Read", "Edit", "Write") for folder in (".ssh", ".claude", ".config", "dev")),
    *(f"Bash({command} {Path.home() / '.claude'}:*)" for command in ("mv", "rm", "cp", "sed -i")),
    "Bash(sudo:*)",
    "Bash(rm -rf /:*)",
    "Bash(rm -rf ~:*)",
    "Bash(git push --force:*)",
    "Bash(docker:*)",
    "Bash(pkill:*)",  # an agent may stop its own processes by id, not other trials' by name
    "Bash(killall:*)",
)
PERSON_PROMPT = """You are role-playing a business user who is talking to an AI assistant. The assistant builds dashboards and scheduled
reports for you on your company's internal platform, ctzd. {persona}
You are Ann Lee (ann.lee@contoso.com, GitHub login ann.lee). This is your own work laptop, and ctzd on it is already logged in as you.

What you want: {goal}
What only you know (share it when asked, or when it matters): {facts}

Rules:
- Reply as this person, in one to three sentences, in plain language. Never write code, commands, or file names you would not know.
- If the assistant asks you to run a command, open a terminal, use git, or edit a file, say you do not know how and ask it to do it.
- You can open every link the assistant gives you: what it says is live is what you see, unless it tells you something is broken.
  An email the assistant says it sent is in your inbox, as it describes it.
  Do not pause the work to check things yourself later; when one request is done, ask for the next one right away.
  When the assistant asks you to try or check something, do it now and say in this reply what you saw; never say you will
  look and report back.
- You are already logged in; if the assistant asks about logins or accounts, tell it to use the one it has.
- End your message with {done} only when the assistant's latest message says that what you want is already live or sent, with
  the link or result. Never in a message where you still ask for something, such as "ship it". If the assistant is stuck after
  several attempts, end with {give_up}.
"""


@dataclass
class Platform:
    root: Path
    process: subprocess.Popen[bytes]
    port: int
    env: dict[str, str]
    profile: dict[str, str]
    mailpit: str

    def api(self, path: str) -> object:
        response = httpx.get(f"http://127.0.0.1:{self.port}{path}", headers={"Authorization": f"Bearer {self.env['CTZD_GITHUB_TOKEN']}"}, timeout=30)
        return response.json() if response.is_success else {"status": response.status_code, "body": response.text[:500]}

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
            try:
                self.process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                self.process.kill()
        stop_processes_under(self.root)


def stop_processes_under(folder: Path) -> int:
    """Stop every process working in `folder`: dashboards the platform left, previews an agent left running."""
    stopped = 0
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cwd = (entry / "cwd").readlink()
        except OSError:
            continue
        if cwd.is_relative_to(folder):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(int(entry.name), signal.SIGTERM)
                stopped += 1
    return stopped


def start_platform(root: Path, *, fail_first_build: bool = False) -> Platform:
    root.mkdir(parents=True, exist_ok=True)
    log = (root / "platform.log").open("wb")
    env = os.environ | ({"CTZD_TRIAL_FAIL_FIRST_BUILD": "1"} if fail_first_build else {})
    # The repo's Python itself, not `uv run`, so the platform gets SIGTERM and stops its dashboards.
    python = REPO / ".venv" / "bin" / "python"
    process = subprocess.Popen([str(python), "-m", "validation.platform.serve", "platform", str(root / "platform")], cwd=REPO, stdout=log, stderr=subprocess.STDOUT, env=env)
    settings = root / "platform" / "platform.json"
    deadline = time.monotonic() + 180
    while not settings.is_file():
        if process.poll() is not None or time.monotonic() > deadline:
            msg = f"the platform did not start; see {root / 'platform.log'}"
            raise RuntimeError(msg)
        time.sleep(1)
    saved = json.loads(settings.read_text())
    return Platform(root=root, process=process, port=int(saved["port"]), env=dict(saved["env"]), profile=dict(saved["profile"]), mailpit=str(saved["mailpit"]))


def person_env(base: Path, platform: Platform, home: Path) -> dict[str, str]:
    """The laptop: the trial ctzd on PATH, logged in, its own ctzd home, git pointed at the local repos.

    HOME is a fresh folder, so tools such as gh see none of the operator's accounts. Claude Code still finds
    the operator's login through CLAUDE_CONFIG_DIR, and uv keeps its shared cache and Python builds.
    """
    home.mkdir(parents=True, exist_ok=True)
    (home / "profile.json").write_text(json.dumps(platform.profile))
    user = home.parent / "user-home"
    user.mkdir(exist_ok=True)
    real = Path.home()
    keep = {key: value for key, value in os.environ.items() if not key.startswith(("CTZD_", "VIRTUAL_ENV", "UV_", "GIT_", "GH_", "GITHUB_", "XDG_"))}
    isolation = {
        "HOME": str(user),
        "CLAUDE_CONFIG_DIR": os.environ.get("CLAUDE_CONFIG_DIR", str(real / ".claude")),
        "UV_CACHE_DIR": str(real / ".cache" / "uv"),
        "UV_PYTHON_INSTALL_DIR": str(real / ".local" / "share" / "uv" / "python"),
    }
    return keep | platform.env | isolation | {"CTZD_HOME": str(home), "PATH": f"{base / TOOL_BIN}:{os.environ['PATH']}"}


def ctzd(argv: list[str], *, cwd: Path, env: dict[str, str], timeout: float = 900) -> dict[str, object]:
    executable = shutil.which("ctzd", path=env["PATH"])
    if executable is None:
        msg = "the trial ctzd is not installed; see validation/README.md"
        raise RuntimeError(msg)
    done = subprocess.run([executable, *argv, "--json"], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, check=False)
    try:
        parsed: object = json.loads(done.stdout)
    except json.JSONDecodeError:
        parsed = {"ok": False, "raw": done.stdout + done.stderr}
    return {str(key): value for key, value in parsed.items()} if isinstance(parsed, dict) else {}


# ---------------------------------------------------------------- the scripted probe


def probe(base: Path) -> int:
    """The flow of plan section 1 on a fresh local platform, scripted: what an agent should manage."""
    report = Report("stage 3 probe (local platform, scripted)")
    root = base / "probe"
    shutil.rmtree(root, ignore_errors=True)
    platform = start_platform(root)
    try:
        env = person_env(base, platform, root / "home")
        work = root / "work"
        work.mkdir()
        setup = ctzd(["setup"], cwd=work, env=env)
        report.expect("setup is ready and hands over the served start guide", ok=setup.get("ok") is True and "Start a new app" in str(setup.get("guide")), seen=setup)
        created = ctzd(["new", "probe-app", "--sbu", "fi"], cwd=work, env=env)
        app = work / "probe-app"
        report.expect("new creates the repo and the app on the channel's release", ok=created.get("ok") is True and (app / ".ctzd" / "version").read_text() == "2\n", seen=created)
        first = ctzd(["status", "--wait"], cwd=app, env=env)
        report.expect("the starter app builds, deploys, and is released as v1", ok=first.get("ok") is True and first.get("release") == 1, seen=first)
        dashboards = platform.api("/local/dashboards")
        report.expect("the dashboard runs", ok=isinstance(dashboards, dict) and "fi-probe-app" in dashboards, seen=dashboards)
        home = app / "web" / "src" / "pages" / "home.tsx"
        home.write_text(home.read_text().replace("<TypographyH1>", "<TypographyH1>Probe: "))
        deployed = ctzd(["deploy"], cwd=app, env=env)
        report.expect("a change deploys as v2", ok=deployed.get("ok") is True and deployed.get("release") == SECOND_RELEASE, seen=deployed)
        added = ctzd(["add", "job", "refresh", "--schedule", "0 6 * * 1-5"], cwd=app, env=env)
        job = app / "src" / "app" / "jobs" / "refresh.py"
        report.expect("add job writes a module", ok=added.get("ok") is True and job.is_file(), seen=added)
        shipped = ctzd(["deploy"], cwd=app, env=env)
        report.expect("the job deploys", ok=shipped.get("ok") is True and "refresh" in str(shipped.get("dags")), seen=shipped)
        ran = ctzd(["run", "refresh", "--wait"], cwd=app, env=env)
        report.expect("the job runs in the local Airflow", ok=ran.get("ok") is True and ran.get("state") == "success", seen=ran)
        rolled = ctzd(["rollback"], cwd=app, env=env, timeout=400)
        report.expect("rollback restores the release before the live one", ok=rolled.get("ok") is True and rolled.get("done") is True, seen=rolled)
        (app / "README.md").write_text("# Probe\n\nFixed.\n")
        resumed = ctzd(["deploy"], cwd=app, env=env)
        report.expect("the next deploy clears the pause and goes live", ok=resumed.get("ok") is True and isinstance(resumed.get("release"), int), seen=resumed)
        state = platform.api("/v1/apps/fi/probe-app")
        report.expect("auto-deploy is no longer paused", ok=isinstance(state, dict) and state.get("paused") is False, seen=state)
        # A dataset that answers 500: its frame lacks a column its row type names.
        datasets = app / "src" / "app" / "api" / "datasets.py"
        datasets.write_text(datasets.read_text().replace("return with_share(load_demo())", 'return with_share(load_demo()).drop(columns="share")'))
        (app / "tests" / "test_datasets.py").write_text("def test_nothing() -> None:\n    pass\n")
        broken = ctzd(["deploy"], cwd=app, env=env)
        report.expect(
            "a page whose data fails fails the smoke test and production keeps the previous release", ok=broken.get("ok") is False and "smoke" in str(broken.get("summary")).lower(), seen=broken
        )
    finally:
        platform.stop()
    return report.finish()


# ---------------------------------------------------------------- trials


@dataclass
class Turn:
    agent: str
    person: str
    cost_usd: float
    seconds: float
    agent_turns: int
    stopped: str | None = None
    # How long the agent worked before its message reached the person; `seconds` adds the person's reply.
    agent_seconds: float = 0.0
    # The agent's tokens this turn, as Claude Code reports them: input, output, and cache reads and writes.
    usage: dict[str, int] = field(default_factory=dict)


@dataclass
class Trial:
    key: str
    scenario: str
    persona: str
    model: str
    turns: list[Turn] = field(default_factory=list)
    outcome: str = "incomplete"
    error: str | None = None

    @property
    def cost_usd(self) -> float:
        return sum(turn.cost_usd for turn in self.turns)


_USAGE = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def _result(stream: Path) -> dict[str, object]:
    found: dict[str, object] = {}
    for line in stream.read_text(errors="replace").splitlines():
        with contextlib.suppress(json.JSONDecodeError):
            event: object = json.loads(line)
            if isinstance(event, dict) and event.get("type") == "result":
                found = {str(key): value for key, value in event.items()}
    return found


def sandboxed(command: list[str], *, writable: list[Path], readable: list[Path], cwd: Path) -> list[str]:
    """`command` inside bubblewrap: the system, the trial's own folders, and no other file of the operator's.

    The network is shared, so the agent still reaches the local platform, the data sources, and PyPI.
    Without bubblewrap the command runs as it is, and only the deny rules keep the agent out.
    """
    bwrap = shutil.which("bwrap")
    if bwrap is None:
        return command
    home = Path.home()
    private_tmp = "/tmp"  # noqa: S108 - an empty tmpfs of the sandbox's own, not the host's /tmp
    args = [bwrap, "--die-with-parent", "--ro-bind", "/usr", "/usr", "--ro-bind", "/etc", "/etc", "--proc", "/proc", "--dev", "/dev", "--tmpfs", private_tmp]
    args += [part for link in ("bin", "lib", "lib64", "sbin") if Path(f"/{link}").is_symlink() for part in ("--symlink", f"usr/{link}", f"/{link}")]
    args += ["--ro-bind-try", "/run/systemd/resolve", "/run/systemd/resolve", "--tmpfs", str(home)]
    claude_rw = [home / ".claude", home / ".cache" / "uv"]
    claude_ro = [home / ".local" / "bin", home / ".local" / "share" / "claude", home / ".local" / "share" / "uv"]
    for path in [*claude_ro, *readable]:
        args += ["--ro-bind-try", str(path), str(path)]
    for path in [*claude_rw, *writable]:
        args += ["--bind", str(path), str(path)]
    # Claude Code's own history stays out of view: the trial keeps its sessions in a folder of its own.
    private = cwd.parent / "claude-state"
    for folder in ("projects", "sessions", "session-env", "file-history", "plans", "paste-cache", "shell-snapshots"):
        (private / folder).mkdir(parents=True, exist_ok=True)
        args += ["--bind", str(private / folder), str(home / ".claude" / folder)]
    (private / "history.jsonl").touch()
    args += ["--bind", str(private / "history.jsonl"), str(home / ".claude" / "history.jsonl")]
    return [*args, "--chdir", str(cwd), *command]


@dataclass(frozen=True)
class Conversation:
    """One side of a trial's conversation: a headless Claude Code session resumed turn by turn."""

    trial_dir: Path
    session: str
    model: str
    cwd: Path
    # Folders the agent's sandbox can write and read besides its own trial folder; empty for the person.
    writable: tuple[Path, ...] = ()
    readable: tuple[Path, ...] = ()

    def agent(self, message: str, *, env: dict[str, str], number: int) -> tuple[str, float, int, str | None, dict[str, int]]:
        settings = self.trial_dir / "agent-settings.json"
        if number == 1:
            settings.write_text(json.dumps({"permissions": {"allow": list(ALLOWED), "deny": list(DENIED), "defaultMode": "dontAsk"}}))
        args = [tool("claude"), "-p", message, "--model", self.model, "--output-format", "stream-json", "--verbose", "--setting-sources", "project,local", "--strict-mcp-config"]
        args += ["--settings", str(settings), "--permission-mode", "dontAsk", "--max-budget-usd", str(AGENT_BUDGET_USD)]
        args += ["--session-id", self.session] if number == 1 else ["--resume", self.session]
        stream = self.trial_dir / f"agent-{number:02d}.jsonl"
        command = sandboxed(args, writable=[self.trial_dir, *self.writable], readable=list(self.readable), cwd=self.cwd)
        with stream.open("wb") as out:
            try:
                subprocess.run(command, cwd=self.cwd, env=env, stdout=out, stderr=subprocess.STDOUT, timeout=AGENT_TIMEOUT, check=False)
            except subprocess.TimeoutExpired:
                return "", 0.0, 0, "the agent turn timed out", {}
        result = _result(stream)
        cost = result.get("total_cost_usd")
        turns = result.get("num_turns")
        stopped = None if result.get("subtype") == "success" else str(result.get("subtype") or "no result")
        reported = result.get("usage")
        usage = {str(key): value for key, value in reported.items() if key in _USAGE and isinstance(value, int)} if isinstance(reported, dict) else {}
        return str(result.get("result") or ""), float(cost) if isinstance(cost, (int, float)) else 0.0, int(turns) if isinstance(turns, int) else 0, stopped, usage

    def person(self, message: str, *, prompt: str, number: int) -> str:
        folder = self.cwd
        folder.mkdir(parents=True, exist_ok=True)
        framed = (
            f"Ann, this is the assistant's latest message to you:\n\n<assistant_message>\n{message}\n</assistant_message>\n\n"
            "Write Ann's reply to the assistant: answer its questions as Ann would. Do not repeat or rephrase its message."
        )
        args = [tool("claude"), "-p", framed if message else "(The assistant said nothing.) Write Ann's reply.", "--model", self.model, "--output-format", "json"]
        args += ["--setting-sources", "project", "--strict-mcp-config", "--tools", "", "--system-prompt", prompt]
        args += ["--session-id", self.session] if number == 1 else ["--resume", self.session]
        done = subprocess.run(args, cwd=folder, capture_output=True, text=True, timeout=PERSON_TIMEOUT, check=False)
        (folder / f"person-{number:02d}.json").write_text(done.stdout + done.stderr)
        try:
            parsed: object = json.loads(done.stdout)
        except json.JSONDecodeError:
            return GIVE_UP
        return str(parsed.get("result", GIVE_UP)) if isinstance(parsed, dict) else GIVE_UP


_ASKING = ("ship it", "deploy it", "please deploy", "go ahead", "put it live", "make it live", "publish it")


def _still_asking(reply: str) -> bool:
    lowered = reply.lower()
    return any(phrase in lowered for phrase in _ASKING)


@dataclass(frozen=True)
class Job:
    scenario: Scenario
    persona: str
    rep: int
    model: str
    person_model: str


def colleague_app(base: Path, aux: Path, platform: Platform, name: str, source: str) -> Path:
    """An app someone else made and deployed: the starter, replaced by a fixture, live as v2."""
    env = person_env(base, platform, aux / "colleague-home")
    work = aux / "colleague"
    work.mkdir(parents=True, exist_ok=True)
    created = ctzd(["new", name, "--sbu", "fi"], cwd=work, env=env)
    if created.get("ok") is not True:
        msg = f"could not create {name}: {created}"
        raise RuntimeError(msg)
    app = work / name
    shutil.copytree(FIXTURES / name, app, dirs_exist_ok=True)
    (app / "app.toml").write_text((app / "app.toml").read_text().replace("use = []", f'use = ["{source}"]'))
    deployed = ctzd(["deploy", "--message", f"First version of {name}"], cwd=app, env=env)
    if deployed.get("ok") is not True:
        msg = f"could not deploy {name}: {deployed}"
        raise RuntimeError(msg)
    return app


def arrange(base: Path, trial_dir: Path, aux: Path, platform: Platform, scenario: Scenario) -> Path:
    """Put the platform in the scenario's starting state; return the folder the agent starts in."""
    work = trial_dir / "work"
    if scenario.setup == "app-folder":
        ctzd(["new", "canary-app", "--sbu", "fi"], cwd=work, env=person_env(base, platform, trial_dir / "home"))
        return work / "canary-app"
    if scenario.setup == "colleague-app":
        shutil.rmtree(colleague_app(base, aux, platform, "desk-trades", "fi_trading"))
    if scenario.setup == "deployed-chart":
        app = colleague_app(base, aux, platform, "sector-prices", "fi_pricing")
        env = person_env(base, platform, aux / "colleague-home")
        logic, test = app / "src" / "app" / "logic" / "summary.py", app / "tests" / "test_logic.py"
        logic.write_text(logic.read_text().replace('("clean_price", "mean")', '("clean_price", "sum")').replace("its average clean price", "its total clean price"))
        test.write_text(test.read_text().replace('["Utilities", "Energy"]', '["Energy", "Utilities"]').replace("[99.0, 95.0]", "[190.0, 99.0]"))
        served = app / "tests" / "test_datasets.py"
        served.write_text(
            served.read_text().replace(
                '[{"sector": "Utilities", "clean_price": 99.0}, {"sector": "Energy", "clean_price": 95.0}]',
                '[{"sector": "Energy", "clean_price": 190.0}, {"sector": "Utilities", "clean_price": 99.0}]',
            )
        )
        broken = ctzd(["deploy", "--message", "Show totals per sector"], cwd=app, env=env)
        if broken.get("ok") is not True:
            msg = f"could not deploy the bad change: {broken}"
            raise RuntimeError(msg)
        shutil.rmtree(app)
    return work


def run_trial(base: Path, job: Job) -> Trial:
    scenario = job.scenario
    key = f"{scenario.key}-{job.persona}-{job.model.removeprefix('claude-')}-{job.rep}"
    trial_dir = base / "runs" / key
    shutil.rmtree(trial_dir, ignore_errors=True)
    (trial_dir / "work" / ".claude" / "skills" / "ctzd").mkdir(parents=True)
    shutil.copy(BOOTSTRAP, trial_dir / "work" / ".claude" / "skills" / "ctzd" / "SKILL.md")
    trial = Trial(key=key, scenario=scenario.key, persona=job.persona, model=job.model)
    # Everything the agent must not find by searching upward lives outside its trial folder: the platform's
    # repositories and builds, the person's instructions, and the colleague's copy of an app.
    aux = base / "aux" / key
    shutil.rmtree(aux, ignore_errors=True)
    platform = start_platform(aux, fail_first_build=scenario.fail_first_build)
    try:
        env = person_env(base, platform, trial_dir / "home")
        prompt = PERSON_PROMPT.format(persona=PERSONAS[job.persona], goal=scenario.goal, facts=scenario.facts, done=DONE, give_up=GIVE_UP)
        cwd = arrange(base, trial_dir, aux, platform, scenario)
        # The agent sees the repositories it pushes to (as on GitHub), the platform's settings, the wheels and npm packages
        # its apps install, and the browser `ctzd shot` takes pictures with.
        settings = aux / "platform"
        browsers = Path.home() / ".cache" / "ms-playwright"
        readable = (settings / "platform.toml", settings / "gitconfig", base / "wheels", base / "npm", base / TOOL_BIN.split("/", maxsplit=1)[0], browsers)
        agent = Conversation(trial_dir, str(uuid.uuid4()), job.model, cwd, writable=(settings / "github",), readable=readable)
        person = Conversation(trial_dir, str(uuid.uuid4()), job.person_model, aux / "person")
        message = scenario.opening
        for number in range(1, scenario.max_turns + 1):
            began = time.monotonic()
            text, cost, turns, stopped, usage = agent.agent(message, env=env, number=number)
            agent_seconds = round(time.monotonic() - began, 1)
            reply = person.person(text, prompt=prompt, number=number)
            if DONE in reply and _still_asking(reply):
                reply = reply.replace(DONE, "").strip()  # "ship it" and "done" in one breath: the work is not done yet
            trial.turns.append(Turn(agent=text, person=reply, cost_usd=cost, seconds=round(time.monotonic() - began, 1), agent_turns=turns, stopped=stopped, agent_seconds=agent_seconds, usage=usage))
            if DONE in reply or GIVE_UP in reply or trial.cost_usd > TRIAL_BUDGET_USD:
                trial.outcome = "done" if DONE in reply else "gave up" if GIVE_UP in reply else "over budget"
                break
            message = reply
        collect(trial_dir, platform)
    except (OSError, RuntimeError, subprocess.SubprocessError, httpx.HTTPError) as err:
        trial.error = repr(err)
    finally:
        platform.stop()
        stop_processes_under(trial_dir)
        (trial_dir / "trial.json").write_text(json.dumps(asdict(trial) | {"cost_usd": trial.cost_usd}, indent=2))
    return trial


def collect(trial_dir: Path, platform: Platform) -> None:
    """What the scorer needs from the platform before it stops: apps, releases, runs, and messages."""
    apps = sorted(path.name.removesuffix(".git") for path in (platform.root / "platform" / "github").glob("*.git"))
    statuses: dict[str, object] = {}
    state: dict[str, object] = {"apps": statuses, "dashboards": platform.api("/local/dashboards"), "runs": platform.api("/local/runs"), "teams": platform.api("/local/teams")}
    for repo in apps:
        sbu, _, name = repo.partition("-")
        statuses[repo] = platform.api(f"/v1/apps/{sbu}/{name}")
    with contextlib.suppress(httpx.HTTPError, ValueError):
        listing = httpx.get(f"{platform.mailpit}/api/v1/messages", params={"limit": 1000}, timeout=10).json()
        sender = f"ctzd-{platform.port}@contoso.com"
        mine = [message for message in listing.get("messages", []) if str(message.get("From", {}).get("Address", "")) == sender]
        state["mail"] = [
            {"subject": message.get("Subject"), "to": [to.get("Address") for to in message.get("To", [])], "attachments": message.get("Attachments"), "at": message.get("Created")} for message in mine
        ]
    (trial_dir / "platform-state.json").write_text(json.dumps(state, indent=2, default=str))


def main() -> int:
    parser = argparse.ArgumentParser(prog="validation.trials")
    parser.add_argument("command", choices=["probe", "run"])
    parser.add_argument("root", type=Path)
    parser.add_argument("scenarios", nargs="*", help="SCENARIO or SCENARIO:PERSONA")
    parser.add_argument("--model", default="claude-haiku-5-5")
    parser.add_argument("--person-model", default="claude-haiku-5-5")
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--parallel", type=int, default=3)
    args = parser.parse_args()
    base = Path(str(args.root)).resolve()
    if str(args.command) == "probe":
        return probe(base)
    model, person_model, reps, parallel = str(args.model), str(args.person_model), int(args.reps), int(args.parallel)
    # The agent's sandbox hides the operator's home, so the pnpm its pages build with must be next to the trial ctzd.
    if shutil.which("pnpm", path=str(base / TOOL_BIN)) is None:
        sys.stderr.write(f"pnpm is not in {base / TOOL_BIN}; see validation/README.md\n")
        return 2
    known = {scenario.key: scenario for scenario in (*SCENARIOS, CANARY, CANARY_IN_APP)}
    jobs: list[Job] = []
    for index, entry in enumerate(str(item) for item in args.scenarios):
        key, _, persona = entry.partition(":")
        matches = [scenario for name, scenario in known.items() if name == key] or [scenario for name, scenario in known.items() if name.startswith(key)]
        if len(matches) != 1:
            sys.stderr.write(f"unknown scenario {key}\n")
            return 2
        jobs += [Job(matches[0], persona or list(PERSONAS)[(index + rep) % len(PERSONAS)], rep, model, person_model) for rep in range(1, reps + 1)]
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        results = list(pool.map(lambda job: run_trial(base, job), jobs))
    for trial in results:
        sys.stdout.write(f"{trial.key}: {trial.outcome}, {len(trial.turns)} turns, ${trial.cost_usd:.2f}{'' if trial.error is None else ' ERROR ' + trial.error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
