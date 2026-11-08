# ctzd-core

The runtime library every ctzd app imports as `ctzd`: named data sources
(`ctzd.query`, `ctzd.rest`), the in-process cache (`@ctzd.cached`), the parquet
table store (`ctzd.write_table`, `ctzd.read_table`), notifications
(`ctzd.notify`), number text for messages (`ctzd.display`), and test fakes
(`ctzd.testing`). See `docs/plan.md` in the platform repository for the design.

## Number text: `ctzd.display`

A job that writes a number into an email body or a Teams message formats it with
`ctzd.display`, which shows each float exactly as the web UI's grid and number
components do. It is basis-core's `basis.core.display`, a port of parallax's
formatter held to every case of parallax's own test file: the float is rounded to
15 significant digits, half away from zero, then once to the shown decimals.

```python
import ctzd

ctzd.display.number(1234.5)  # "1,234.50"
ctzd.display.number(2.675, decimals=2)  # "2.68", where Python's round gives 2.67
ctzd.display.percent(0.0525)  # "5.25%"
ctzd.display.bps(0.0525, decimals=0)  # "525 bp", with a no-break space
ctzd.display.money(-1234.5, "USD")  # "-$1,234.50"
ctzd.display.price32(99.515625, step="half")  # "99-16+"
ctzd.display.text(1_250_000.0, ctzd.display.Format(abbreviate="M"))  # "1.3M"
```

Rates are fractions; `None` and NaN show as empty text; a value that rounds to
zero shows no minus sign.
