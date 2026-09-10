"""Rendering a results file as a human-readable Markdown report."""

from __future__ import annotations

from typing import Any

from hbt_bench.core import FORMAT_VERSION, BenchmarkError

Cells = dict[tuple[str, str], dict[str, Any]]


def _table(header: list[str], rows: list[list[str]], numeric: int | None = None) -> list[str]:
    """One aligned pipe table, followed by a blank line.

    Columns from `numeric` onward hold measurements and are right-aligned, in
    the text and via the GFM `--:` marker so the published HTML aligns them
    too. Digits that do not line up are much harder to compare down a column,
    which is the whole job of these tables.

    None means no numeric columns, which is what a table of prose wants. It is
    spelled that way rather than as a default of `len(header)`, which a default
    expression cannot see.
    """
    if numeric is None:
        numeric = len(header)
    widths = [max([len(header[i])] + [len(r[i]) for r in rows]) for i in range(len(header))]

    def render_row(cells: list[str]) -> str:
        pad = [c.rjust(w) if i >= numeric else c.ljust(w) for i, (c, w) in enumerate(zip(cells, widths))]
        return "| " + " | ".join(pad) + " |"

    # The separator is a row like any other, so the column geometry is stated
    # once: pad and join it the same way, and it cannot drift out of step.
    dashes = ["-" * (w - 1) + ":" if i >= numeric else "-" * w for i, w in enumerate(widths)]
    return [render_row(header), render_row(dashes)] + [render_row(r) for r in rows] + [""]


def _row(cells: Cells, impls: list[str], name: str) -> list[dict[str, Any] | None]:
    """One input's cells, looked up once each.

    An implementation listed under "Unavailable" produced no results at all,
    so a cell can legitimately be absent.
    """
    return [cells.get((impl, name)) for impl in impls]


def _entity_rows(cells: Cells, impls: list[str], inputs: list[str]) -> list[list[str]]:
    rows: list[list[str]] = []
    for name in inputs:
        cell_values = _row(cells, impls, name)
        rows.append([name] + ["--" if c is None or c["entities"] is None else str(c["entities"]) for c in cell_values])
    return rows


def _timing_rows(cells: Cells, impls: list[str], inputs: list[str]) -> list[list[str]]:
    rows: list[list[str]] = []
    for name in inputs:
        cell_values = _row(cells, impls, name)
        timings = [c["timing"] for c in cell_values if c and c["timing"]]
        best = min((t["mean"] for t in timings), default=None)
        row = [name]
        for cell in cell_values:
            if not cell or not cell["timing"]:
                row.append("--")
                continue
            timing = cell["timing"]
            ratio = timing["mean"] / best if best else 1.0
            row.append(f"{timing['mean'] * 1000:.1f} ± {timing['stddev'] * 1000:.1f} ({ratio:.2f}x)")
        rows.append(row)
    return rows


def render(data: dict[str, Any]) -> str:
    """Render a results document as Markdown."""
    version = data.get("version")
    if version != FORMAT_VERSION:
        raise BenchmarkError(f"results format {version!r}, expected {FORMAT_VERSION!r}")
    impls = [i["name"] for i in data["implementations"]]
    inputs = [i["name"] for i in data["inputs"]]
    cells: Cells = {(r["implementation"], r["input"]): r for r in data["results"]}
    host = data["host"]

    lines: list[str] = ["# hbt benchmark", ""]
    lines += [
        f"Generated {data['generated']} on {host['node']} ({host['machine']}, {host['system']} {host['release']})"
        + (f", with {data['hyperfine']}." if data.get("hyperfine") else "."),
        "",
    ]

    lines += ["## Entity counts", ""]
    lines += _table(["input"] + impls, _entity_rows(cells, impls, inputs), numeric=1)
    lines += ["A row that disagrees is a parity bug, not a benchmark result.", ""]

    lines += ["## Timings", ""]
    lines += [
        "Mean wall time in milliseconds, plus or minus one standard deviation. `x` is the ratio to the fastest "
        "implementation on that row; `--` means the implementation did not handle the input.",
        "",
    ]
    lines += _table(["input"] + impls, _timing_rows(cells, impls, inputs), numeric=1)

    unavailable = [i for i in data["implementations"] if i["error"]]
    if unavailable:
        lines += ["## Unavailable", ""]
        lines += ["These implementations were not run at all, so their columns are blank throughout.", ""]
        lines += _table(["implementation", "reason"], [[i["name"], i["error"]] for i in unavailable])

    failures = [r for r in data["results"] if r["error"]]
    if failures:
        lines += ["## Not benchmarked", ""]
        lines += [
            "Pairs excluded from the timings. A reason names what actually happened -- a missing input and a "
            "parser that rejected the format are not the same thing, and neither is the harness failing to find "
            "a count in output it did not recognise.",
            "",
        ]
        rows = [[f["implementation"], f["input"], f["error"]] for f in failures]
        lines += _table(["implementation", "input", "reason"], rows)

    lines += ["## Provenance", ""]
    lines += _table(
        ["implementation", "version", "revision", "store path"],
        [
            [i["name"], i["version"] or "--", (i["revision"] or "--")[:7], i["store_path"] or "--"]
            for i in data["implementations"]
        ],
    )
    lines += _table(["input", "path"], [[i["name"], i["path"]] for i in data["inputs"]])

    return "\n".join(lines).rstrip() + "\n"
