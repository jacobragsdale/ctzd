"""The dashboard: its title, the firm's shell, and its pages, one module each in pages/."""

from app import theme, ui

TITLE = "{{title}}"

app = ui.dash_app(TITLE)
app.layout = theme.shell(TITLE)
