"""git, driven by ctzd so business users never touch it. Power users may use git directly."""

import base64
import os
import shutil
from pathlib import Path

from ctzd_cli import config, proc
from ctzd_cli.errors import CliError
from ctzd_cli.proc import Completed

REMOTE = "origin"
BRANCH = "main"
_RECORD = "\x1e"
_FIELD = "\x1f"


def find_git() -> str:
    found = shutil.which("git")
    if found is not None:
        return found
    mingit = config.user_dir() / "mingit" / "cmd" / ("git.exe" if os.name == "nt" else "git")
    if mingit.is_file():
        return str(mingit)
    msg = "git is not installed."
    raise CliError(msg, hint="Run `ctzd setup`; it installs a private copy of git.")


def auth_header(token: str) -> str:
    credentials = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return f"AUTHORIZATION: basic {credentials}"


class Git:
    def __init__(self, root: Path, *, executable: str, host: str, token: str | None, identity: tuple[str, str] | None) -> None:
        self.root = root
        self._executable = executable
        self._host = host
        self._token = token
        self._identity = identity

    def run(self, *args: str, check: bool = True) -> Completed:
        options: list[str] = ["-c", "credential.helper=", "-c", "core.autocrlf=false"]
        if self._token is not None:
            options += ["-c", f"http.https://{self._host}/.extraheader={auth_header(self._token)}"]
        if self._identity is not None:
            options += ["-c", f"user.name={self._identity[0]}", "-c", f"user.email={self._identity[1]}"]
        result = proc.run([self._executable, *options, *args], cwd=self.root, env={"GIT_TERMINAL_PROMPT": "0"}, timeout=600)
        if check and not result.ok:
            msg = f"git {args[0]} failed."
            raise CliError(msg, hint=proc.tail(result.output, 20))
        return result

    def init(self, remote_url: str | None) -> None:
        self.run("init", "--quiet", f"--initial-branch={BRANCH}")
        if remote_url is not None:
            self.run("remote", "add", REMOTE, remote_url)

    def head(self) -> str | None:
        result = self.run("rev-parse", "--verify", "--quiet", "HEAD", check=False)
        return result.output.strip() if result.ok else None

    def changed_paths(self) -> list[str]:
        output = self.run("status", "--porcelain=v1", "--untracked-files=all").output
        return sorted(line[3:] for line in output.splitlines() if line.strip())

    def commit_all(self, message: str, *, allow_empty: bool = False) -> bool:
        """Stage and commit everything; return False when there was nothing to commit and no empty commit was wanted."""
        self.run("add", "--all")
        if not self.run("diff", "--cached", "--quiet", check=False).code:
            if not allow_empty:
                return False
            self.run("commit", "--quiet", "--allow-empty", "--message", message)
            return True
        self.run("commit", "--quiet", "--message", message)
        return True

    def remote_has_main(self) -> bool:
        result = self.run("ls-remote", "--exit-code", "--heads", REMOTE, BRANCH, check=False)
        return result.ok

    def rebase_onto_remote(self) -> None:
        """Bring in changes others pushed; on a conflict, undo the rebase and name the files."""
        if not self.remote_has_main():
            return
        self.run("fetch", "--quiet", REMOTE, BRANCH)
        result = self.run("rebase", f"{REMOTE}/{BRANCH}", check=False)
        if result.ok:
            return
        conflicts = self.run("diff", "--name-only", "--diff-filter=U", check=False).output.split()
        self.run("rebase", "--abort", check=False)
        msg = "Someone else changed this app and their changes conflict with yours."
        hint = (
            f"Conflicting files: {', '.join(conflicts) or 'unknown'}. Their changes are already fetched: run "
            f"`git rebase {REMOTE}/{BRANCH}`, resolve each conflict, `git add` the files, `git rebase --continue`, then `ctzd deploy` again."
        )
        raise CliError(msg, hint=hint)

    def push_main(self) -> None:
        self.run("push", "--quiet", REMOTE, f"HEAD:refs/heads/{BRANCH}")

    def clone(self, url: str, destination: Path) -> None:
        self.run("clone", "--quiet", url, str(destination))

    def fetch_tags(self) -> None:
        self.run("fetch", "--quiet", "--tags", "--force", REMOTE)

    def tags(self, prefix: str) -> list[tuple[str, str]]:
        output = self.run("for-each-ref", f"refs/tags/{prefix}", f"--format=%(refname:strip=2){_FIELD}%(contents){_RECORD}").output
        entries: list[tuple[str, str]] = []
        for record in output.split(_RECORD):
            if _FIELD in record:
                name, body = record.split(_FIELD, 1)
                entries.append((name.strip(), body))
        return entries

    def tag(self, name: str, message: str, commit: str) -> None:
        self.run("tag", "--annotate", name, commit, "--message", message)

    def push_tag(self, name: str) -> None:
        self.run("push", "--quiet", REMOTE, f"refs/tags/{name}")


def open_repo(root: Path, *, token: str | None, identity: tuple[str, str] | None) -> Git:
    return Git(root, executable=find_git(), host=config.load().github.host, token=token, identity=identity)
