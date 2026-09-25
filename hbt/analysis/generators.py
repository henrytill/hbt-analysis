"""Inputs for the fuzzer to feed every implementation, as Hypothesis strategies.

A strategy rather than a function of a random source, so that Hypothesis
shrinks a disagreement by simplifying the choices that built the document --
dropping a block, taking an earlier alternative -- rather than by deleting its
lines, which leaves whatever else was on a line that matters.  Every choice is
listed simplest first for that reason: Hypothesis shrinks a `sampled_from` or
`one_of` toward its first option, so a shrunk case reads "a link to
https://a.com named Foo" unless the link or the name is what matters.

The pieces are chosen for the edges the implementations have been seen to
treat differently, rather than drawn from the whole of each format: the point
is the next disagreement, and uniformly random text finds only that every
parser refuses it.  They are deliberately not limited to what the corpus
already pins.  A disagreement found here is settled in hbt-data and pinned
there as a fixture, after which it stops being news; until then, finding it
again is the fuzzer doing its job.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from hypothesis import strategies as st

# Dates as the formats write them: well-formed first, then the variants
# chrono's `%B %-d, %Y` accepts or refuses in ways a reimplementation of it has
# to match.
DATES = (
    "November 15, 2023",
    "December 6, 2023",
    "Dec 7, 2023",
    "december  8,2023",
    "November 5, 23",
    "February 29, 2024",
    "February 30, 2023",
    "Sept 5, 2023",
)

URLS = (
    "https://a.com",
    "https://b.com/x",
    "https://a.com/",
    "http://c.org/p?q=1",
    "HTTPS://D.COM/Y",
    "https://x.com/a[b]?c|d",
    "https://例え.jp/パス",
    "mailto:z@y.com",
    "javascript:void",
    "/relative",
)

# Link and heading text: plain, then each inline construct a parser may treat as
# ending, splitting or carrying the text.
TEXT = (
    "Foo",
    "Bar baz",
    "Hello, world!",
    "`code`",
    "x `y` z",
    "a *em* b",
    "**strong**",
    "a\\*b",
    "a &amp; b",
    "![img](https://i.com)",
    "q<span>r</span>s",
    "A\nB",
)

text = st.sampled_from(TEXT)
url = st.sampled_from(URLS)


def _inline(name: str, target: str) -> str:
    return f"[{name}]({target})"


def _titled(name: str, target: str) -> str:
    return f'[{name}]({target} "title")'


def _between(before: str, name: str, after: str) -> str:
    return f"{before} {name} {after}"


def _heading(level: int, name: str) -> list[str]:
    return [f"{'#' * level} {name}"]


link = st.one_of(
    st.builds(_inline, text, url),
    url.map(lambda u: f"<{u}>"),
    url.map(lambda u: f"[]({u})"),
    st.just("<me@ex.com>"),
    text.map(lambda t: f"[{t}][r]"),
    st.builds(_titled, text, url),
)

item = st.tuples(
    st.integers(min_value=0, max_value=4),
    st.sampled_from(("-", "*", "1.")),
    st.one_of(link, text, st.builds(_between, link, text, link)),
)


def _list(items: Sequence[tuple[int, str, str]]) -> list[str]:
    return [f"{'  ' * depth}{marker} {body}" for depth, marker, body in items]


block = st.one_of(
    st.lists(item, min_size=1, max_size=6).map(_list),
    st.builds(_heading, st.integers(min_value=2, max_value=5), text),
    link.map(lambda s: [s]),
    link.map(lambda s: [f"> {s}"]),
)


def _section(date: str | None, blocks: list[list[str]]) -> list[list[str]]:
    return ([] if date is None else [[f"# {date}"]]) + blocks


section = st.builds(
    _section,
    st.one_of(st.sampled_from(DATES), st.none()),
    st.lists(block, max_size=4),
)


def _document(sections: list[list[list[str]]], definition: str | None) -> str:
    blocks = [b for s in sections for b in s] + ([] if definition is None else [[f"[r]: {definition}"]])
    return "".join(line + "\n" for b in blocks for line in [*b, ""])


markdown = st.builds(_document, st.lists(section, max_size=3), st.one_of(st.none(), url))
"""A document of dated sections, each holding headings, links and nested lists."""


@dataclass(frozen=True)
class Generator:
    """One input format: how to make a document, and the extension that names the format."""

    suffix: str
    strategy: st.SearchStrategy[str]


GENERATORS: dict[str, Generator] = {
    "markdown": Generator(".md", markdown),
}
