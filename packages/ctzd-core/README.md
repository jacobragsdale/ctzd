# ctzd-core

The runtime library every ctzd app imports as `ctzd`: named data sources
(`ctzd.query`, `ctzd.rest`), the in-process cache (`@ctzd.cached`), the parquet
table store (`ctzd.write_table`, `ctzd.read_table`), notifications
(`ctzd.notify`), number text for messages (`ctzd.display`), and test fakes
(`ctzd.testing`). See `docs/plan.md` in the platform repository for the design.

## Number text: `ctzd.display`

A job that writes a number into an email body or a Teams message formats it with
`ctzd.display`, which shows each float exactly as the web UI's grid and number
components do. It ports parallax's formatter, Excel's display rule: the float is
rounded to 15 significant digits, half away from zero, then once to the shown
decimals. The functions return text and never change a value, so keep the float
for any arithmetic and format it only where it leaves for a person.

```python
import ctzd

ctzd.display.number(1234.5)  # "1,234.50"
ctzd.display.number(2.675, decimals=2)  # "2.68", where Python's round gives 2.67
ctzd.display.number(1_250_000.0, abbreviate="M")  # "1.3M"
ctzd.display.percent(0.0525)  # "5.25%"
ctzd.display.bps(0.0525, decimals=0)  # "525 bp", with a no-break space
ctzd.display.money(-1234.5, "USD", negative="parentheses")  # "($1,234.50)"
ctzd.display.price32(99.515625)  # "99-16+"
```

- Rates are fractions: 0.0525 is 5.25% and 525 bp.
- A float shows 2 decimals, 1 when abbreviated, unless `decimals` (0 to 30)
  says otherwise; an int, such as `len(frame)`, shows none. Numbers read from
  sources and the store are floats, so a count shows whole with `decimals=0`.
- A value that rounds to zero shows no minus sign: -0.0001 is `0.00`.
- `None` and NaN show as empty text, as the grid shows a missing value.
- `money` shows the currency's minor units: 2 for USD, 0 for JPY.

The module's docstring maps each function and keyword to basis-ui's components
(`Num`, `Pct`, `Bps`, `Money`, `Price32`) and lists where the port differs. Its
tests run every float and integer case of the fixtures parallax's Rust formatter
writes, copied into `tests/fixtures/format.json` with the basis-ui commit they
came from.
