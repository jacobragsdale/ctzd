"""A kit's sources as their source.toml says, and each source's secret delivered the way its code reads it."""

import json
import os
from pathlib import Path

import pytest

from ctzd_common import samples
from ctzd_kit import SourceError
from ctzd_kit.connections import OracleSource, RestSource, connection
from ctzd_kit.sources import Secret, SecretFile, Source, SourceSpec, delivery, has_credentials, load_kit, source_of

FI = load_kit("ctzd_fi")
RISK_SECRET = json.dumps({"user": "fi_app", "password": "s3cret", "unused": 1})


@pytest.fixture(autouse=True)
def desktop(monkeypatch: pytest.MonkeyPatch) -> None:
    """A desktop with nothing faked, no source's secret, and no sample rows served yet."""
    for name in list(os.environ):
        if name.startswith("CTZD_"):
            monkeypatch.delenv(name)
    samples.reset()


def package_source(secret: Secret | None) -> Source:
    return Source("vendor_feed", SourceSpec(description="A made-up vendor feed", kind="package", secret=secret), "ctzd_tk.sources.vendor_feed")


def test_a_kit_lists_sample_beside_its_own_sources() -> None:
    assert list(FI.sources) == ["sample", "fi_curves", "fi_pricing", "fi_risk"]
    assert FI.source("fi_pricing").spec.dialect == "oracle"
    assert FI.source("fi_risk").spec.io == ("fi_risklib",)
    assert source_of("ctzd_fi.sources.fi_risk") == FI.source("fi_risk")
    assert source_of("ctzd_kit.sample").spec.kind == "sample"


def test_an_unknown_source_names_the_ones_there_are() -> None:
    with pytest.raises(SourceError, match="has no source 'fi_trades'; it has fi_curves, fi_pricing, fi_risk, sample"):
        FI.source("fi_trades")


def test_a_package_outside_a_kits_sources_is_not_a_source() -> None:
    with pytest.raises(SourceError, match="not a source's package"):
        source_of("ctzd_fi.helpers")


def test_a_driver_source_gets_its_whole_secret_as_one_variable(tmp_path: Path) -> None:
    whole = '{"kind": "oracle", "dsn": "db.contoso.com:1521/FI", "user": "u", "password": "p"}'
    found = delivery(FI.source("fi_pricing"), whole, folder=tmp_path)
    assert dict(found.env) == {"CTZD_SOURCE_FI_PRICING": whole}
    assert dict(found.files) == {}


def test_a_package_source_gets_the_variables_its_library_reads(tmp_path: Path) -> None:
    found = delivery(FI.source("fi_risk"), RISK_SECRET, folder=tmp_path)
    assert dict(found.env) == {"FI_RISK_USER": "fi_app", "FI_RISK_PASSWORD": "s3cret"}
    assert dict(found.files) == {}


def test_a_package_source_can_get_its_secret_as_a_file(tmp_path: Path) -> None:
    source = package_source(Secret(env={"VENDOR_USER": "user"}, file=SecretFile(variable="VENDOR_CREDENTIALS")))
    found = delivery(source, RISK_SECRET, folder=tmp_path)
    path = (tmp_path / "vendor_feed" / "secret.json").as_posix()
    assert dict(found.files) == {path: RISK_SECRET}
    assert dict(found.env) == {"VENDOR_USER": "fi_app", "VENDOR_CREDENTIALS": str(tmp_path / "vendor_feed" / "secret.json")}


@pytest.mark.parametrize(("secret", "message"), [('{"user": "u"}', "no field 'password'"), ("[1, 2]", "not a JSON object"), ("not json", "not a JSON object")])
def test_a_secret_without_the_fields_it_needs_is_refused(secret: str, message: str, tmp_path: Path) -> None:
    with pytest.raises(SourceError, match=message):
        delivery(FI.source("fi_risk"), secret, folder=tmp_path)


def test_has_credentials_for_a_driver_source() -> None:
    pricing = FI.source("fi_pricing")
    assert has_credentials(pricing, {"CTZD_SOURCE_FI_PRICING": "{}"})
    assert not has_credentials(pricing, {"CTZD_SOURCE_FI_PRICING": ""})
    assert not has_credentials(pricing, {})


def test_has_credentials_for_package_sources(tmp_path: Path) -> None:
    risk = FI.source("fi_risk")
    assert has_credentials(risk, {"FI_RISK_USER": "u", "FI_RISK_PASSWORD": "p"})
    assert not has_credentials(risk, {"FI_RISK_USER": "u"})
    assert has_credentials(package_source(None), {}), "a package with no secret connects as it always has"
    as_file = package_source(Secret(file=SecretFile(variable="VENDOR_CREDENTIALS")))
    written = tmp_path / "secret.json"
    assert not has_credentials(as_file, {"VENDOR_CREDENTIALS": str(written)}), "the file is not there yet"
    written.write_text("{}", encoding="utf-8")
    assert has_credentials(as_file, {"VENDOR_CREDENTIALS": str(written)})


def test_the_sample_source_never_has_credentials() -> None:
    assert not has_credentials(FI.source("sample"), {"CTZD_SOURCE_SAMPLE": "{}"})


def test_has_credentials_reads_the_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CTZD_SOURCE_FI_PRICING", "{}")
    assert has_credentials(FI.source("fi_pricing"))


def test_a_secret_variable_must_be_a_variable_name() -> None:
    with pytest.raises(ValueError, match="not an environment variable name"):
        Secret(env={"fi-user": "user"})


def test_a_connection_is_read_from_its_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CTZD_SOURCE_FI_PRICING", '{"kind": "oracle", "dsn": "db.contoso.com:1521/FI", "user": "u", "password": "hunter2"}')
    monkeypatch.setenv("CTZD_SOURCE_FI_CURVES", '{"kind": "rest", "base_url": "https://curves.contoso.com", "auth": {"type": "none"}}')
    pricing = connection("fi_pricing")
    assert isinstance(pricing, OracleSource)
    assert "hunter2" not in repr(pricing)
    assert isinstance(connection("fi_curves"), RestSource)


def test_a_missing_or_broken_connection_says_what_to_do_and_hides_the_password(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SourceError, match="ctzd env"):
        connection("fi_pricing")
    monkeypatch.setenv("CTZD_SOURCE_FI_PRICING", '{"kind": "oracle", "dsn": "", "password": "hunter2"}')
    with pytest.raises(SourceError, match="not a valid connection") as caught:
        connection("fi_pricing")
    assert "hunter2" not in str(caught.value)
