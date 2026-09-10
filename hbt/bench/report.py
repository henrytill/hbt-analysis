"""Rendering a results file as a human-readable Markdown report."""

from __future__ import annotations

from typing import Any

from jinja2 import Environment, PackageLoader, StrictUndefined
from tabulate import tabulate

from hbt.bench.core import FORMAT_VERSION, BenchmarkError

Cells = dict[tuple[str, str], dict[str, Any]]


def _escape(cell: str) -> str:
    r"""A cell is data, not markup.

    An unescaped `|` in an error message or a path starts a new column, and
    GFM then drops the overflow: `parse failed at a|b` published as `parse
    failed at a`. tabulate does not do this for us -- nor does pandas, which
    renders its Markdown through tabulate -- so it is done here.
    """
    return cell.replace("|", r"\|")


def _table(header: list[str], rows: list[list[str]], numeric: int | None = None) -> str:
    """One aligned pipe table.

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
    text = tabulate(
        [[_escape(c) for c in row] for row in rows],
        headers=[_escape(h) for h in header],
        # "pipe", not "github": both are valid GFM, but github's separator is
        # bare dashes, so colalign would show only in the padding and the
        # published HTML would lose the alignment entirely. pipe emits the
        # `--:` markers.
        tablefmt="pipe",
        colalign=tuple("right" if i >= numeric else "left" for i in range(len(header))),
        # Every cell is already the string we mean to publish. Left on, this
        # would re-format anything that parses as a number -- entity counts
        # among them -- to tabulate's taste rather than ours.
        disable_numparse=True,
    )
    return text


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
    """Render a results document as Markdown.

    The document is a template: the headings, the prose and which sections
    appear are report.md.j2, and this assembles the tables it interpolates.
    Keeping the wording out of Python is the point -- it was string
    continuations that black reflowed mid-sentence.
    """
    version = data.get("version")
    if version != FORMAT_VERSION:
        raise BenchmarkError(f"results format {version!r}, expected {FORMAT_VERSION!r}")
    impls = [i["name"] for i in data["implementations"]]
    inputs = [i["name"] for i in data["inputs"]]
    cells: Cells = {(r["implementation"], r["input"]): r for r in data["results"]}

    unavailable = [i for i in data["implementations"] if i["error"]]
    failures = [r for r in data["results"] if r["error"]]

    # Markdown is whitespace-sensitive, so the template controls every blank
    # line itself: block tags are trimmed, and a section's spacing lives with
    # the section rather than being appended by the code that fills it.
    env = Environment(
        loader=PackageLoader("hbt.bench", "."),
        autoescape=False,  # nosec B701 - Markdown, not HTML; cells are escaped by _escape
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        undefined=StrictUndefined,
    )
    text = env.get_template("report.md.j2").render(
        generated=data["generated"],
        host=data["host"],
        hyperfine=data.get("hyperfine"),
        entities=_table(["input"] + impls, _entity_rows(cells, impls, inputs), numeric=1),
        timings=_table(["input"] + impls, _timing_rows(cells, impls, inputs), numeric=1),
        unavailable=(
            _table(["implementation", "reason"], [[i["name"], i["error"]] for i in unavailable]) if unavailable else ""
        ),
        not_benchmarked=(
            _table(
                ["implementation", "input", "reason"],
                [[f["implementation"], f["input"], f["error"]] for f in failures],
            )
            if failures
            else ""
        ),
        provenance=_table(
            ["implementation", "version", "revision", "store path"],
            [
                [i["name"], i["version"] or "--", (i["revision"] or "--")[:7], i["store_path"] or "--"]
                for i in data["implementations"]
            ],
        ),
        corpus=_table(["input", "path"], [[i["name"], i["path"]] for i in data["inputs"]]),
    )
    return text.rstrip() + "\n"
