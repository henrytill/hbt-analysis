"""Rendering a results file as a human-readable Markdown report."""

from __future__ import annotations

from typing import Any

Cells = dict[tuple[str, str], dict[str, Any]]


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    """One aligned pipe table, followed by a blank line."""
    widths = [max([len(header[i])] + [len(r[i]) for r in rows]) for i in range(len(header))]

    def render_row(cells: list[str]) -> str:
        return "| " + " | ".join(c.ljust(w) for c, w in zip(cells, widths)) + " |"

    sep = "|" + "|".join(" " + "-" * w + " " for w in widths) + "|"
    return [render_row(header), sep] + [render_row(r) for r in rows] + [""]


def _entity_rows(cells: Cells, impls: list[str], inputs: list[str]) -> list[list[str]]:
    rows: list[list[str]] = []
    for name in inputs:
        row = [name]
        for impl in impls:
            result = cells.get((impl, name))
            row.append("--" if result is None or result["entities"] is None else str(result["entities"]))
        rows.append(row)
    return rows


def _timing_rows(cells: Cells, impls: list[str], inputs: list[str]) -> list[list[str]]:
    rows: list[list[str]] = []
    for name in inputs:
        present = [cells[(i, name)]["timing"] for i in impls if cells.get((i, name)) and cells[(i, name)]["timing"]]
        best = min((t["mean"] for t in present), default=None)
        row = [name]
        for impl in impls:
            result = cells.get((impl, name))
            if not result or not result["timing"]:
                row.append("--")
                continue
            timing = result["timing"]
            ratio = timing["mean"] / best if best else 1.0
            row.append(f"{timing['mean'] * 1000:.1f} ± {timing['stddev'] * 1000:.1f} ({ratio:.2f}x)")
        rows.append(row)
    return rows


def render(data: dict[str, Any]) -> str:
    """Render a results document as Markdown."""
    impls = [i["name"] for i in data["implementations"]]
    inputs = [i["name"] for i in data["inputs"]]
    cells: Cells = {(r["implementation"], r["input"]): r for r in data["results"]}
    host = data["host"]

    lines: list[str] = ["# hbt benchmark", ""]
    lines += [
        f"Generated {data['generated']} on {host['node']} ({host['machine']}, {host['system']} {host['release']}).",
        "",
    ]

    lines += ["## Entity counts", ""]
    lines += _table(["input"] + impls, _entity_rows(cells, impls, inputs))
    lines += ["A row that disagrees is a parity bug, not a benchmark result.", ""]

    lines += ["## Timings", ""]
    lines += [
        "Mean wall time in milliseconds, plus or minus one standard deviation. `x` is the ratio to the fastest "
        "implementation on that row; `--` means the implementation did not handle the input.",
        "",
    ]
    lines += _table(["input"] + impls, _timing_rows(cells, impls, inputs))

    failures = [r for r in data["results"] if r["error"]]
    if failures:
        lines += ["## Unsupported", ""]
        rows = [[f["implementation"], f["input"], f["error"]] for f in failures]
        lines += _table(["implementation", "input", "reason"], rows)

    lines += ["## Provenance", ""]
    lines += _table(
        ["implementation", "version", "revision", "store path"],
        [
            [i["name"], i["version"] or "--", (i["revision"] or "--")[:7], i["store_path"]]
            for i in data["implementations"]
        ],
    )
    lines += _table(["input", "path"], [[i["name"], i["path"]] for i in data["inputs"]])

    return "\n".join(lines).rstrip() + "\n"
