# hbt-analysis

Comparing four independent implementations of **hbt** ("Heterogeneous Bookmark Transformation"). Each implementation is a git submodule with its own upstream repo, its own flake, and its own toolchain; see [AGENTS.md](AGENTS.md) for the layout and for how to work inside them.

This README used to be an org-babel notebook: one `#+begin_src sh` block per implementation per input, with hyperfine's terminal output pasted underneath by Emacs. That was brittle, Emacs-specific, and recorded no provenance — a timing sat in the file with no way to tell which build produced it. It has been replaced by `hbt-analysis`.

## The command line

`hbt-analysis` is one executable with a command per job, because every job here runs over the same implementations: `bench` times them, `conformance` holds them to the corpus, and `fuzz` holds them to each other. All three take the same three options for choosing what they run over — `--impl NAME` to limit the set, `--binary NAME=PATH` to use a particular build, `--revision NAME=REV` to record which build it is — after the command name, like everything else they take:

```sh
hbt-analysis bench --impl hbt-rs --warmup 50
hbt-analysis conformance --impl hbt-go -q
hbt-analysis fuzz --impl hbt-rs --impl hbt-go --seed 1
```

`nix run .#bench`, `nix run .#conformance` and `nix run .#fuzz` are those commands with every implementation built from the flake and passed in.

## Benchmarking

```sh
nix run .#bench
```

That builds all four implementations from their own flakes, runs the matrix, writes `benchmarks/results.json`, and prints the Markdown report. Arguments are passed through:

```sh
nix run .#bench -- --warmup 50
nix run .#bench -- --impl hbt-rs --impl hbt-go
nix run .#bench -- --report-only benchmarks/results.json   # re-render, no re-run
```

Inside the dev shell, run it from the working tree so edits take effect. There it falls back to the `result-hbt-*` symlinks rather than building anything through the flake, so the numbers describe whatever those symlinks currently point at:

```sh
nix develop
python -m hbt.analysis bench --build # --build refreshes the symlinks first
```

### Run it locally, publish from CI

CI is not a usable benchmarking environment — shared, throttled, noisy runners — so nothing is ever *timed* in a workflow. Regenerate the numbers by hand on a quiet machine and commit `benchmarks/results.json`.

`--info-only` is the part of the harness that a workflow *can* run: it does the `--info` stage, records the entity counts, and skips hyperfine entirely, so it measures nothing and does not care how noisy the runner is. What it buys is the one cross-implementation check nothing else enforces — a row where the four disagree is a parity bug — against a corpus that exists in any checkout:

```sh
nix run .#bench -- --info-only --corpus benchmarks/fixtures.toml -o info.json
```

It requires `-o`. A document with no timings must never land on `benchmarks/results.json`, which is what the page publishes, so the flag refuses the default rather than relying on the caller to redirect it.

Rendering is a different matter, and that part is automated. `nix build .#site` turns the committed results into a standalone HTML page under `result/share/doc/hbt-analysis/html/`, and `.github/workflows/pages.yml` builds that and deploys it to GitHub Pages on every push to `master`. The HTML is a build output, not a committed file; `benchmarks/defaults.yml` holds the pandoc settings.

`results.json` has to be *committed*, not merely present: Nix builds from the git tree, so an untracked one is invisible and the page will not change.

### The corpus

`benchmarks/corpus.toml` names the inputs. The defaults are the author's private bookmark exports; they are in no repo and do not exist in a fresh checkout, so a first run elsewhere needs that file pointed at something real. The `hbt-data` fixtures vendored in each submodule work for a smoke test, though they are far too small to time meaningfully.

Every implementation is tried against every input. A pair that fails is recorded as unsupported and excluded from the timings rather than aborting the run, so the corpus does not need to know which parser handles what.

### What the report contains

- **Entity counts** from `--info`, one row per input. A row where the four disagree is a parity bug, not a benchmark result.
- **Timings**: mean wall time with standard deviation and the ratio to the fastest implementation on that row. Each input is one hyperfine invocation naming all four commands, so they are measured under the same conditions, and run with `-N` so shell startup is not part of the measurement. The ratios are computed by the renderer from the exported means; hyperfine's own summary goes to stderr and is discarded.
- **Unavailable** implementations, which produced no results at all, and **Not benchmarked** pairs, with the reason each was left out.
- **Provenance**: the Nix store path behind every number, the binary's own `--version` string, and — under `.#bench`, where it is known exactly — the revision it was built from.

The document is a Jinja template, `hbt/bench/report.md.j2` — the headings, the prose and which sections appear live there rather than in Python. The tables are rendered with `tabulate` and interpolated into it. Cells are escaped first: a `|` in an error message or a path would otherwise start a new column and GFM would silently drop the overflow, which `tabulate` does not handle for you — nor does `pandas`, which renders its Markdown through it.

### One source of truth

`benchmarks/results.json` is the only benchmark file that is committed. Everything else is a translation of it: the Markdown report is rendered on demand (`--report-only`, to stdout unless `-r` names a file), and the HTML page is a Nix build output. Neither is ever written into the tree as a tracked file, so there is no derived copy to fall out of date with the numbers it came from.

## Conformance

```sh
nix run .#conformance
```

Builds the four implementations the same way `.#bench` does and holds each one to the [hbt-data](https://github.com/henrytill/hbt-data) corpus with `hbt-analysis conformance`, one column per implementation, one row per fixture. The comparison is not reimplemented here: the command imports `hbt.conformance`, the harness each implementation runs on its own, so the matrix and an implementation's own check cannot disagree about what a fixture means. It exits non-zero if any cell is not `PASS` or `XFAIL`.

Each implementation is checked against the corpus **it** pins, found by URL in its own `.gitmodules`, not against one revision chosen here. The pins differ by design, so the header prints each column's corpus revision beside its build, and notes when they disagree. `--corpus DIR` checks all four against one directory instead — the way to ask whether everyone passes a corpus being edited.

Waivers belong to an implementation, so each carries its own `conformance.waivers` and the matrix reads it from there, by the same convention its own conformance run does — a fixture some implementation does not satisfy yet shows as `XFAIL` here without this repository being told who is broken. `--waivers NAME=FILE` replaces one implementation's file, for checking a binary that is not the submodule's:

```sh
nix run .#conformance -- -q                                   # only rows some implementation did not pass
nix run .#conformance -- markdown/basic 'html/*'              # filter, as hbt-conformance does
nix run .#conformance -- --corpus ../hbt-data                 # everyone, against one checkout
nix run .#conformance -- --waivers hbt-go=path/to/waivers     # instead of its own file
```

Under `.#conformance` the binaries are the `flake.lock` revisions, while each corpus is read from the working tree's nested checkout. When the lock falls behind the gitlinks those can pair a binary with a corpus newer than the one it pins — the header shows both, and `nix flake update hbt-hs hbt-go hbt-js hbt-ocaml hbt-rs` realigns them. In the dev shell, `python -m hbt.analysis conformance` falls back to the `result-hbt-*` symlinks as `bench` does.

The harness itself is the `hbt-data` flake input, not a fifth submodule: a fifth entry in `.gitmodules` would read as a fifth implementation to everything that derives the set from it. To see an unreleased harness change in the matrix, point the input at a checkout with `--override-input hbt-data path:../hbt-data`.

## Fuzzing

```sh
nix run .#fuzz — --impl hbt-hs --impl hbt-go --impl hbt-ocaml --impl hbt-rs
```

Conformance can only find a disagreement someone already thought to pin. `hbt-analysis fuzz` generates documents instead, runs every selected implementation over each, and reports where they do not all read a document the same way — the raw material for the next fixture. No implementation is the reference: a report says who disagrees with whom, and settling which side is right is hbt-data's business. Agreement is `hbt.conformance`'s own comparison, and any two failures agree, since the implementations word their errors differently.

The documents come from [Hypothesis](https://hypothesis.readthedocs.io/) strategies in `hbt/fuzz/generators.py`, built from the constructs the implementations have been seen to treat differently, and Hypothesis shrinks each disagreement to the simplest document that still shows it. A disagreement is reported as its independent parts — *these implementations fail with this message*, *this field splits them this way* — so a document that differs in a label and in a name is two reports, each shrunk on its own, rather than a third kind of its own. The search runs in rounds, one part to a round: a round's first new part becomes its target, and only documents showing that part count as failures while it shrinks, so shrinking cannot slide from one disagreement to another. The search ends when a round gets through `-n` documents (200 by default) without anything new. It exits non-zero if anything was found.

```sh
nix run .#fuzz — --seed 1234                  # reproduce a run; the seed is always printed
nix run .#fuzz — --keep found/                # write each shrunk input, ready to become a fixture
nix run .#fuzz — --no-shrink -n 50            # a quick look
```

A seed reproduces a run, unless a document takes longer than `--timeout` on one machine and not another, or shrinking one find reaches Hypothesis's five-minute limit. Only `--format markdown` exists so far; a format is a strategy and a file extension in `GENERATORS`. hbt-js's CLI is still a stub, so it fails every document and shows up as one disagreement of its own; leave it out with `--impl` until it can parse.

## The root flake

The four implementations are flake inputs, which is what lets `.#bench` build them end to end. They are `git+file:` inputs rather than `path:`: three of the four subflakes derive their version from `self.shortRev or self.dirtyShortRev`, and a path input carries no git metadata at all, so they fail to evaluate with `attribute 'dirtyShortRev' missing`.

The cost is a third layer of pinning. `flake.lock` records a rev for each implementation, on top of this repo's gitlinks and each implementation's own `hbt-data` pin. After `scripts/update-submodules.sh` moves a pointer, re-lock to match:

```sh
nix flake update hbt-hs hbt-go hbt-js hbt-ocaml hbt-rs
```

`inputs.self.submodules = true` is set, so `self` is the tree *with* the submodules rather than with four empty directories. The usual cost of setting it — every submodule tree landing in `src = self` — does not apply here: the `hbt-analysis` derivation takes a `lib.fileset`-filtered source of just `hbt/analysis/`, `hbt/bench/`, `hbt/fuzz/`, `pyproject.toml`, and this README, so it stays a few tens of kilobytes rather than the size of the four submodule trees, and does not rebuild when a pointer moves.

The `git+file:` inputs emit a deprecation warning ([NixOS/nix#12281](https://github.com/NixOS/nix/issues/12281)). That issue prescribes replacing them with `inputs.self.submodules = true` plus a bare path literal (`hbt-rs.url = ./hbt-rs`), which is half of what is already here — but the other half does not work: `hbt-go` then fails to evaluate with `attribute 'dirtyShortRev' missing`, because three of the four subflakes read their version out of `self`'s git metadata and a path input has none. Making the prescribed form usable means teaching the four upstreams to tolerate a revision-less `self`. Until then the warning is ignorable, which is also what the Nix maintainers say on that issue.

The companion warning about not reading HEAD is benign: detaching `hbt-go` three commits back and re-locking records the checked-out revision, not `master`, so the inputs do follow the gitlinks.

## Layout

| Path | What |
|---|---|
| `hbt/analysis/` | `hbt-analysis` (Python): `cli.py` gathers the commands, `commands/bench.py`, `commands/conformance.py` and `commands/fuzz.py` are the three commands, `commands/__init__.py` is what they share, `implementations.py` finds the implementations and their binaries |
| `hbt/bench/` | the benchmark library `hbt-analysis bench` drives, a sibling of `hbt.conformance`: `timing.py`, `results.py`, and `report.py` with its template. `hbt` is a PEP 420 namespace portion, so it has no `__init__.py`; all three packages ship in the one `hbt-analysis` distribution, built with hatchling |
| `hbt/fuzz/` | the fuzzing library `hbt-analysis fuzz` drives: `generators.py` holds the Hypothesis strategies, `verdict.py` runs an implementation and takes a disagreement apart, `search.py` is the one-atom-a-round search, `report.py` renders a find |
| `benchmarks/` | corpus definitions, `results.json`, and the pandoc defaults for the page |
| `scripts/` | submodule pointer maintenance (bash) |
| `flake.nix` | `hbt-analysis`, the dev shell, `.#bench`, `.#conformance`, `.#fuzz`, and `.#site` |
| `hbt-{hs,go,js,ocaml,rs}/` | the implementations, as submodules |
