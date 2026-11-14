"""The firm's look for every ctzd dashboard: colours, type, the grid's theme, the chart template and the shell around
every page. Platform-owned: ctzd sync rewrites this file, so change a page, never this.

Everything a page's colours come from is made here, from `TOKENS`, a light and a dark value for each, forked from
basis-dash's theme:

- the CSS variables, `--ctzd-NAME`, under `data-mantine-color-scheme`, which Mantine sets from the person's system
  preference, with Mantine's and Dash's own variables pointed at them (`stylesheet`);
- the Mantine theme (`mantine`), whose primary palette is made from the tokens;
- the AG Grid theme (`GRID`), whose colours are `var(--ctzd-...)`, so the grid follows the scheme with no re-render;
- the Plotly template (`template`), transparent and small, since Plotly reads no CSS variable: the stylesheet colours
  a figure's text and lines, and the series, `SERIES`, read on both backgrounds.

It reads and writes nothing, and imports nothing of the app's.
"""

import json
from typing import Final

import dash
import dash_mantine_components as dmc
import plotly.graph_objects as go
import plotly.io as pio
from dash import html

NOTIFICATIONS: Final = "ctzd-notifications"
"""Mantine's notification container: `ui.on_error` tells the person here when a callback fails."""
SAMPLE_BANNER: Final = "ctzd-sample-banner"
"""The banner a page shows when what it shows came from sample rows (`ui.load`)."""

TOKENS: Final[dict[str, tuple[str, str]]] = {
    "background": ("oklch(0.971 0.0084 255)", "oklch(0.172 0.007 255)"),
    "foreground": ("oklch(0.215 0.0126 255)", "oklch(0.955 0.0126 255)"),
    "card": ("oklch(1 0 255)", "oklch(0.198 0.0084 255)"),
    "popover": ("oklch(1 0 255)", "oklch(0.232 0.0095 255)"),
    "muted": ("oklch(0.95 0.0095 255)", "oklch(0.264 0.0106 255)"),
    "muted-foreground": ("oklch(0.47 0.0168 255)", "oklch(0.79 0.0168 255)"),
    "secondary": ("oklch(0.928 0.0106 255)", "oklch(0.294 0.0118 255)"),
    "primary": ("oklch(0.475 0.2 262)", "oklch(0.72 0.1452 262)"),
    "primary-hover": ("oklch(0.43 0.1989 262)", "oklch(0.77 0.1169 262)"),
    "primary-foreground": ("oklch(1 0 0)", "oklch(0.172 0.007 255)"),
    "border": ("oklch(0.868 0.0129 255)", "oklch(0.336 0.0129 255)"),
    "edge": ("oklch(0.904 0.0118 255)", "oklch(0.294 0.0118 255)"),
    "input": ("oklch(0.615 0.0154 255)", "oklch(0.565 0.0154 255)"),
    "ring": ("oklch(0.605 0.152 262)", "oklch(0.6 0.152 262)"),
    "selection": ("oklch(0.934 0.0315 262)", "oklch(0.29 0.056 262)"),
    "destructive": ("oklch(0.47 0.189 25)", "oklch(0.8 0.1143 25)"),
    "success": ("oklch(0.45 0.1184 152)", "oklch(0.8 0.144 152)"),
    "warning": ("oklch(0.47 0.1031 68)", "oklch(0.81 0.1479 68)"),
    "warning-subtle": ("oklch(0.957 0.0302 68)", "oklch(0.26 0.034 68)"),
    "gain": ("oklch(0.45 0.1184 152)", "oklch(0.86 0.16 152)"),
    "loss": ("oklch(0.345 0.1398 25)", "oklch(0.75 0.1505 25)"),
}
"""Each token, by name without its `--ctzd-`: its light value and its dark one."""
FONT_SANS: Final = '"Segoe UI Variable", "Segoe UI", system-ui, -apple-system, sans-serif'
FONT_NUM: Final = '"Segoe UI Variable", "Segoe UI", system-ui, sans-serif'
"""The desktop's own fonts, with tabular figures switched on for numbers: nothing is fetched from another origin."""
SERIES: Final[tuple[str, ...]] = ("#3c7dde", "#d54f00", "#2fa172", "#c68610", "#008300", "#d04782", "#8c83e5", "#e46361")
"""A figure's series colours, in their fixed order: one palette that reads on both schemes' cards."""
PALETTE: Final[tuple[str, ...]] = ("#eef3fd", "#dbe6fb", "#b9cdf5", "#8fb0ef", "#6a96e8", "#4a7fe2", "#2f63d6", "#2856bd", "#224aa2", "#1c3d86")
"""Mantine's ten-step primary palette, `ctzd`, at the primary's hue."""

MANTINE_VARIABLES: Final[dict[str, str]] = {
    "color-body": "card",
    "color-text": "foreground",
    "color-dimmed": "muted-foreground",
    "color-placeholder": "muted-foreground",
    "color-anchor": "primary",
    "color-default": "card",
    "color-default-hover": "secondary",
    "color-default-color": "foreground",
    "color-default-border": "input",
    "color-error": "destructive",
    "primary-color-filled": "primary",
    "primary-color-filled-hover": "primary-hover",
    "primary-color-contrast": "primary-foreground",
}
DASH_VARIABLES: Final[dict[str, str]] = {
    "Fill-Interactive-Strong": "primary",
    "Fill-Inverse-Strong": "card",
    "Fill-Primary-Hover": "muted",
    "Stroke-Strong": "input",
    "Stroke-Weak": "border",
    "Text-Strong": "foreground",
    "Text-Primary": "foreground",
    "Text-Weak": "muted-foreground",
    "Tooltip-Background-Color": "popover",
    "Tooltip-Border-Color": "border",
}

GRID_PARAMS: Final[dict[str, object]] = {
    "backgroundColor": "var(--ctzd-card)",
    "foregroundColor": "var(--ctzd-foreground)",
    "subtleTextColor": "var(--ctzd-muted-foreground)",
    "borderColor": "var(--ctzd-border)",
    "chromeBackgroundColor": "var(--ctzd-muted)",
    "headerBackgroundColor": "var(--ctzd-muted)",
    "headerTextColor": "var(--ctzd-muted-foreground)",
    "headerFontWeight": 600,
    "headerFontSize": 12,
    "accentColor": "var(--ctzd-primary)",
    "rowHoverColor": "var(--ctzd-muted)",
    "selectedRowBackgroundColor": "var(--ctzd-selection)",
    "fontFamily": FONT_NUM,
    "fontSize": 13,
    "spacing": 6,
    "rowHeight": 28,
    "headerHeight": 32,
    "columnBorder": False,
    "wrapperBorderRadius": 6,
    "browserColorScheme": "inherit",
}
GRID: Final[dict[str, str]] = {"function": "themeQuartz.withParams(" + json.dumps(GRID_PARAMS, separators=(",", ":")) + ")"}
"""A grid's `dashGridOptions["theme"]`: AG Grid's Quartz with the firm's parameters, never a CDN's theme."""


def stylesheet() -> str:
    """The tokens as CSS variables in each scheme, Mantine's and Dash's variables pointed at them, and Plotly's text and
    lines coloured from them."""
    light = "".join(f"  --ctzd-{name}: {values[0]};\n" for name, values in TOKENS.items())
    dark = "".join(f"  --ctzd-{name}: {values[1]};\n" for name, values in TOKENS.items())
    mantine = "".join(f"  --mantine-{name}: var(--ctzd-{token});\n" for name, token in MANTINE_VARIABLES.items())
    dash_own = "".join(f"  --Dash-{name}: var(--ctzd-{token});\n" for name, token in DASH_VARIABLES.items())
    return (
        f':root,\n:root[data-mantine-color-scheme="light"] {{\n  color-scheme: light;\n{light}}}\n'
        f':root[data-mantine-color-scheme="dark"] {{\n  color-scheme: dark;\n{dark}}}\n'
        f"html:root[data-mantine-color-scheme] {{\n{mantine}{dash_own}  --mantine-font-family: {FONT_SANS};\n}}\n"
        "body { background: var(--ctzd-background); color: var(--ctzd-foreground); }\n"
        ".ctzd-number { font-family: " + FONT_NUM + '; font-feature-settings: "tnum"; }\n'
        ".js-plotly-plot .main-svg text { fill: var(--ctzd-foreground) !important; }\n"
        ".js-plotly-plot .gridlayer path, .js-plotly-plot .zerolinelayer path { stroke: var(--ctzd-edge) !important; }\n"
        ".js-plotly-plot .hoverlayer .hovertext path { fill: var(--ctzd-popover) !important; stroke: var(--ctzd-border) !important; }\n"
        '.ag-cell.ag-right-aligned-cell { font-feature-settings: "tnum"; }\n'
    )


def mantine() -> dict[str, object]:
    """The theme `dmc.MantineProvider` takes: the firm's primary palette, fonts and radius."""
    return {
        "primaryColor": "ctzd",
        "primaryShade": {"light": 6, "dark": 4},
        "colors": {"ctzd": list(PALETTE)},
        "fontFamily": FONT_SANS,
        "headings": {"fontFamily": FONT_SANS, "fontWeight": "600"},
        "defaultRadius": "md",
    }


def template() -> go.layout.Template:
    """The chart template: transparent, in the desktop's font, with the series' colours, small margins and quiet axes.
    It holds only what differs from Plotly's own, since each figure carries its template."""
    return go.layout.Template(
        layout={
            "font": {"family": FONT_NUM, "size": 12},
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(0,0,0,0)",
            "colorway": list(SERIES),
            "margin": {"l": 8, "r": 8, "t": 8, "b": 8},
            "xaxis": {"automargin": True, "showgrid": False, "showline": True, "ticks": "outside"},
            "yaxis": {"automargin": True},
            "legend": {"orientation": "h", "x": 0, "y": 1, "yanchor": "bottom"},
        }
    )


TEMPLATE: Final = "ctzd"
pio.templates[TEMPLATE] = template()
pio.templates.default = TEMPLATE


def index_string(script: str) -> str:
    """The page Dash serves, with the stylesheet and `script` inline: nothing is fetched from another origin."""
    return (
        "<!DOCTYPE html>\n<html>\n<head>\n{%metas%}\n<title>{%title%}</title>\n{%favicon%}\n{%css%}\n"
        f"<style>\n{stylesheet()}</style>\n<script>\n{script}\n</script>\n"
        "</head>\n<body>\n{%app_entry%}\n<footer>\n{%config%}\n{%scripts%}\n{%renderer%}\n</footer>\n</body>\n</html>\n"
    )


def shell(title: str) -> dmc.MantineProvider:
    """What every page shows inside: the title bar with a link to each page, the sample-data banner, the page, and
    the notifications."""
    links = [dmc.Anchor(page["name"], href=page["relative_path"], size="sm") for page in dash.page_registry.values()]
    header = dmc.AppShellHeader(dmc.Group([dmc.Title(title, order=3), dmc.Group(links, gap="md")], justify="space-between", h="100%", px="md"))
    banner = dmc.Alert(
        "Some of what this page shows is sample rows, not real data: a source it reads has no credentials here, or cannot be read from this computer.",
        id=SAMPLE_BANNER,
        title="Sample data",
        color="yellow",
        style={"display": "none"},
        mb="md",
    )
    main = dmc.AppShellMain(html.Div([banner, dash.page_container], style={"maxWidth": "1600px", "margin": "0 auto"}), p="md")
    return dmc.MantineProvider(
        [dmc.NotificationContainer(id=NOTIFICATIONS, position="top-right"), dmc.AppShell([header, main], header={"height": 52}, padding="md")],
        theme=mantine(),  # ty: ignore[invalid-argument-type] - DMC 3.0.0's generated signature says a list; Mantine takes the theme object
        defaultColorScheme="auto",
    )
