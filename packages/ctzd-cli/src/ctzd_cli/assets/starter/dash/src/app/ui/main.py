"""The dashboard page. `ctzd dev` runs it locally; the platform serves `server` with gunicorn."""

import dash_ag_grid as dag
import plotly.express as px
from ctzd.dash import create_app
from dash import dcc, html

from app.data.loaders import load_demo
from app.logic.summary import with_share

app = create_app(__name__, title="{{title}}")
server = app.server


def layout() -> html.Div:
    frame = with_share(load_demo())
    return html.Div(
        [html.H1("{{title}}"), dcc.Graph(figure=px.bar(frame, x="category", y="value")), dag.AgGrid(rowData=frame.to_dict("records"), columnDefs=[{"field": column} for column in frame.columns])]
    )


app.layout = layout

if __name__ == "__main__":
    app.run(debug=True, port=8050)
