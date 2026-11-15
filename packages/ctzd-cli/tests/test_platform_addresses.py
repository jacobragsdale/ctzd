"""Moving the platform to another firm is a change to its settings alone: every address in what the CLI ships comes
from platform.toml and build.toml, and the site's installer and bootstrap skill are written from them."""

import re
from pathlib import Path

import pytest

from ctzd_cli import assets, config, kitwork, main, sync

from .conftest import app_config, make_app

BUNDLED = (Path(config.__file__).parent / "platform.toml").read_text(encoding="utf-8")
# A host or address on the placeholder firm's domains: contoso.com, contosoctzd.azurecr.io, ann.lee@contoso.com.
PLACEHOLDER = re.compile(r"(?i)[a-z0-9-]*contoso[a-z0-9-]*\.[a-z]|@contoso")
# The one setting the CLI's assets hold themselves: the package index apps install from.
SETTINGS = frozenset({"build.toml"})
WORK = (
    BUNDLED.replace('site = "https://ctzd.contoso.com"', 'site = "https://ctzd.example.net"')
    .replace("ctzd-dev.contoso.com", "apps-dev.example.net")
    .replace("ctzd.contoso.com", "apps.example.net")
    .replace("contoso-ctzd-apps", "example-apps")
    + '\n[build_overrides]\npython_index = [{name = "internal", url = "https://index.example.net/simple", default = true}]\n'
)


def test_nothing_the_cli_ships_names_a_host_itself() -> None:
    named = sorted(f"{path}: {found.group()}" for path, text in assets.bundle().items() if path not in SETTINGS for found in PLACEHOLDER.finditer(text))
    assert named == [], "use a {{token}} from sync.platform_tokens"


def test_the_guidance_the_site_and_the_kit_scaffold_take_the_platforms_addresses(tmp_path: Path) -> None:
    platform = config.parse(WORK)
    app = app_config().model_copy(update={"app": app_config().app.model_copy(update={"owners": ("ann.lee@example.net",)})})
    filled = sync.tokens(platform, app)
    written = {
        **assets.guidance_files(filled),
        "start/GUIDE.md": assets.start_guide(sync.platform_tokens(platform)),
        **assets.site_files(sync.platform_tokens(platform)),
        **assets.kit_scaffold("new", filled),
    }
    for path, text in written.items():
        assert PLACEHOLDER.search(text) is None, path
        assert "{{" not in text, path
    assert "https://SBU-NAME.apps.example.net" in written["start/GUIDE.md"]
    assert "https://SBU-NAME.apps-dev.example.net" in written["skills/ctzd/SKILL.md"]
    assert "irm https://ctzd.example.net/install.ps1" in written["skills/ctzd/SKILL.md"]
    assert "$Downloads = 'https://ctzd.example.net/downloads'" in written["install.ps1"]
    assert "$Index = 'https://index.example.net/simple'" in written["install.ps1"]
    assert 'DASHBOARD = "https://fi-bond-prices.apps.example.net"' in written[".agents/skills/ctzd-jobs/SKILL.md"]
    assert 'TO = ("ann.lee@example.net",)' in written[".agents/skills/ctzd-jobs/SKILL.md"]
    assert platform.platform.mingit_url == "https://ctzd.example.net/downloads/MinGit-64-bit.zip"
    make_app(tmp_path, app_config())
    assert (tmp_path / ".claude" / "skills" / "ctzd-jobs" / "SKILL.md").read_text().count("{{") == 0


def test_the_installer_trusts_windows_certificates_and_writes_toml_without_a_byte_order_mark() -> None:
    installer = assets.site_files(sync.platform_tokens(config.load()))["install.ps1"]
    assert "system-certs = true" in installer
    assert "[IO.File]::WriteAllText" in installer
    assert "-Encoding utf8" not in installer
    assert installer.isascii()


def test_ci_site_writes_the_installer_and_the_bootstrap_skill(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main.main(["ci", "site", "--out", str(tmp_path), "--json"]) == 0
    assert '"ok": true' in capsys.readouterr().out
    assert b"\r\n" in (tmp_path / "install.ps1").read_bytes()
    assert (tmp_path / "skills" / "ctzd" / "SKILL.md").read_text(encoding="utf-8").startswith("---\nname: ctzd\n")


def test_a_test_index_replaces_the_internal_one_only_where_nothing_else_does(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(config.PYTHON_INDEX, "https://pypi.org/simple")
    config.load.cache_clear()
    try:
        assert [index.url for index in sync.python_index(config.load())] == ["https://pypi.org/simple"]
        overridden = tmp_path / "platform.toml"
        overridden.write_text(BUNDLED + '\n[build_overrides]\npython_index = [{name = "local", url = "/wheels", format = "flat"}]\n', encoding="utf-8")
        monkeypatch.setenv("CTZD_PLATFORM_CONFIG", str(overridden))
        config.load.cache_clear()
        assert [index.url for index in sync.python_index(config.load())] == ["/wheels"]
    finally:
        config.load.cache_clear()


def test_the_kit_checks_resolution_uses_the_apps_indexes() -> None:
    local = config.PackageIndex(name="local", url="/wheels", format="flat")
    internal = config.PackageIndex(name="internal", url="https://index.example.net/simple", default=True)
    extra = config.PackageIndex(name="extra", url="https://extra.example.net/simple")
    assert kitwork.index_args([local, internal, extra]) == ["--find-links", "/wheels", "--default-index", "https://index.example.net/simple", "--index", "https://extra.example.net/simple"]
