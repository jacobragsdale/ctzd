"""The generator's pieces: client names, parallax formats, and linking a page from the header."""

from pathlib import Path

import pytest

from ctzd_cli import generate


@pytest.mark.parametrize(("name", "camel", "pascal"), [("sector_prices", "sectorPrices", "SectorPrices"), ("more", "more", "More"), ("rich-cheap", "richCheap", "RichCheap")])
def test_names_follow_the_client_and_the_components(name: str, camel: str, pascal: str) -> None:
    assert generate.camel(name) == camel
    assert generate.pascal(name) == pascal


@pytest.mark.parametrize(
    ("display", "shown"),
    [
        ({"kind": "number", "decimals": 2}, {"decimals": 2}),
        ({"kind": "pct"}, {"percent": True}),
        ({"kind": "bps", "decimals": 1}, {"decimals": 1, "bps": True}),
        ({"kind": "price32", "step": "half"}, {"thirty_seconds": "halves"}),
        ({"kind": "money", "currency": "USD", "decimals": 0}, {"decimals": 0}),
    ],
)
def test_each_display_format_becomes_parallax_s(display: dict[str, object], shown: dict[str, object]) -> None:
    assert generate.parallax_format(display) == shown


@pytest.mark.parametrize("pages", ['[{ path: "/", title: "Home" }]', '[\n  { path: "/", title: "Home" },\n]'])
def test_a_new_page_is_linked_after_the_others(tmp_path: Path, pages: str) -> None:
    web = tmp_path / "web" / "src"
    web.mkdir(parents=True)
    (web / "routes.ts").write_text('import { createRoute } from "x";\n\nconst routeTree = rootRoute.addChildren([homeRoute]);\n', encoding="utf-8")
    (web / "nav.ts").write_text(f"const pages = {pages} as const;\n", encoding="utf-8")
    assert generate.add_route(tmp_path, "sector-prices")
    nav = (web / "nav.ts").read_text(encoding="utf-8")
    assert nav.count("{ path:") == 2
    assert ',\n  { path: "/sector-prices", title: "Sector prices" },\n] as const;' in nav
    routes = (web / "routes.ts").read_text(encoding="utf-8")
    assert "rootRoute.addChildren([homeRoute, sectorPricesRoute])" in routes
    assert 'import { SectorPricesPage } from "./pages/sector-prices.tsx";' in routes
