# hbt-analysis

Comparing four independent implementations of **hbt** ("Heterogeneous Bookmark Transformation"). Each implementation is a git submodule with its own upstream repo, its own flake, and its own toolchain; see [AGENTS.md](AGENTS.md) for the layout and for how to work inside them.

This README used to be an org-babel notebook: one `#+begin_src sh` block per implementation per input, with hyperfine's terminal output pasted underneath by Emacs. That was brittle, Emacs-specific, and recorded no provenance — a timing sat in the file with no way to tell which build produced it. It has been replaced by `hbt-bench`.

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
python -m hbt_bench --build          # --build refreshes the symlinks first
```

### Run it locally, publish from CI

CI is not a usable benchmarking environment — shared, throttled, noisy runners — so nothing is ever *timed* in a workflow. Regenerate the numbers by hand on a quiet machine and commit `benchmarks/results.json`.

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

The document is a Jinja template, `hbt_bench/report.md.j2` — the headings, the prose and which sections appear live there rather than in Python. The tables are rendered with `tabulate` and interpolated into it. Cells are escaped first: a `|` in an error message or a path would otherwise start a new column and GFM would silently drop the overflow, which `tabulate` does not handle for you — nor does `pandas`, which renders its Markdown through it.

### One source of truth

`benchmarks/results.json` is the only benchmark file that is committed. Everything else is a translation of it: the Markdown report is rendered on demand (`--report-only`, to stdout unless `-r` names a file), and the HTML page is a Nix build output. Neither is ever written into the tree as a tracked file, so there is no derived copy to fall out of date with the numbers it came from.

## The root flake

The four implementations are flake inputs, which is what lets `.#bench` build them end to end. They are `git+file:` inputs rather than `path:`: three of the four subflakes derive their version from `self.shortRev or self.dirtyShortRev`, and a path input carries no git metadata at all, so they fail to evaluate with `attribute 'dirtyShortRev' missing`.

The cost is a third layer of pinning. `flake.lock` records a rev for each implementation, on top of this repo's gitlinks and each implementation's own `hbt-data` pin. After `scripts/update-submodules.sh` moves a pointer, re-lock to match:

```sh
nix flake update hbt-hs hbt-go hbt-ocaml hbt-rs
```

`inputs.self.submodules = true` is set, so `self` is the tree *with* the submodules rather than with four empty directories. The usual cost of setting it — every submodule tree landing in `src = self` — does not apply here: the `hbt-bench` derivation takes a `lib.fileset`-filtered source of just `hbt_bench/`, `pyproject.toml`, and this README, so it stays 44K and does not rebuild when a pointer moves.

The `git+file:` inputs emit a deprecation warning ([NixOS/nix#12281](https://github.com/NixOS/nix/issues/12281)). That issue prescribes replacing them with `inputs.self.submodules = true` plus a bare path literal (`hbt-rs.url = ./hbt-rs`), which is half of what is already here — but the other half does not work: `hbt-go` then fails to evaluate with `attribute 'dirtyShortRev' missing`, because three of the four subflakes read their version out of `self`'s git metadata and a path input has none. Making the prescribed form usable means teaching the four upstreams to tolerate a revision-less `self`. Until then the warning is ignorable, which is also what the Nix maintainers say on that issue.

The companion warning about not reading HEAD is benign: detaching `hbt-go` three commits back and re-locking records the checked-out revision, not `master`, so the inputs do follow the gitlinks.

## Layout

| Path | What |
|---|---|
| `hbt_bench/` | the benchmark harness (Python, flit) |
| `benchmarks/` | corpus definitions, `results.json`, and the pandoc defaults for the page |
| `scripts/` | submodule pointer maintenance (bash) |
| `flake.nix` | the harness, the dev shell, `.#bench`, and `.#site` |
| `hbt-{hs,go,ocaml,rs}/` | the implementations, as submodules |
