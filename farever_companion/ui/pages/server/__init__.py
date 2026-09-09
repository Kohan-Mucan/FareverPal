"""The Server diagnostics page, split by panel so each file stays small.

- page.py      `ServerPageMixin`: tab chrome + cleanup, composing the three
               panel mixins below
- ping.py      the region ping tester panel
- scanner.py   the live connection scanner panel
- trace.py     the network trace / diagnostics panel
- workers.py   the background QThread workers the panels poll
- net.py       region/host data, the TCP-table scanner, DNS/GeoIP lookups

Nothing is re-exported here: `control_panel.py` imports `ServerPageMixin` from
`.pages.server.page` directly, which is the same one-line-import rule the other
page packages follow. `net.py` is this page's own network plumbing and has
nothing to do with the retired account web client - it is pure function/data
with no widget state, and the name is worth keeping because that is what the
page does.
"""
