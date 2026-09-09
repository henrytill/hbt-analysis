# hbt-analysis

Comparing four independent implementations of **hbt** ("Heterogeneous Bookmark Transformation"). Each implementation is a git submodule with its own upstream repo, its own flake, and its own toolchain; see [AGENTS.md](AGENTS.md) for the layout and for how to work inside them.

This README used to be an org-babel notebook: one `#+begin_src sh` block per implementation per input, with hyperfine's terminal output pasted underneath by Emacs. That was brittle, Emacs-specific, and recorded no provenance — a timing sat in the file with no way to tell which build produced it. It has been replaced by `hbt-bench`.

## Benchmarking

```sh
nix run .#bench
```

That builds all four implementations from their own flakes, runs the matrix, and writes `benchmarks/results.json` plus `benchmarks/report.md`. Arguments are passed through:

```sh
nix run .#bench -- --warmup 50
nix run .#bench -- --impl hbt-rs --impl hbt-go
nix run .#bench -- --report-only benchmarks/results.json   # re-render to stdout
```

Inside the dev shell, run it from the working tree so edits take effect. There it falls back to the `result-hbt-*` symlinks rather than building anything through the flake, so the numbers describe whatever those symlinks currently point at:

```sh
nix develop
python -m hbt_bench --build          # --build refreshes the symlinks first
```

### Run it locally, publish from CI

CI is not a usable benchmarking environment — shared, throttled, noisy runners — so nothing is ever *timed* in a workflow. Regenerate the numbers by hand on a quiet machine and commit `benchmarks/results.json`.

Rendering is a different matter, and that part is automated. `nix build .#site` turns the committed results into a standalone HTML page under `result/share/doc/hbt-analysis/html/`, and `.github/workflows/pages.yml` builds that and deploys it to GitHub Pages on every push to `master`. The HTML is a build output, not a committed file; `benchmarks/defaults.yml` holds the pandoc settings.

Until a `results.json` has been committed the page renders a short placeholder, so the workflow is green from the start rather than failing on a missing file.

### The corpus

`benchmarks/corpus.toml` names the inputs. The defaults are the author's private bookmark exports; they are in no repo and do not exist in a fresh checkout, so a first run elsewhere needs that file pointed at something real. The `hbt-data` fixtures vendored in each submodule work for a smoke test, though they are far too small to time meaningfully.

Every implementation is tried against every input. A pair that fails is recorded as unsupported and excluded from the timings rather than aborting the run, so the corpus does not need to know which parser handles what.

### What the report contains

- **Entity counts** from `--info`, one row per input. A row where the four disagree is a parity bug, not a benchmark result.
- **Timings**: mean wall time with standard deviation and the ratio to the fastest implementation on that row. hyperfine computes the ranking — each input is one hyperfine invocation naming all four commands, run with `-N` so shell startup is not part of the measurement.
- **Unsupported** pairs, with the reason.
- **Provenance**: the submodule revision and Nix store path behind every number.

`results.json` is the durable artifact and `--report-only` re-renders from it, so the report can be regenerated without re-running anything.

## The root flake

The four implementations are flake inputs, which is what lets `.#bench` build them end to end. They are `git+file:` inputs rather than `path:`: three of the four subflakes derive their version from `self.shortRev or self.dirtyShortRev`, and a path input carries no git metadata at all, so they fail to evaluate with `attribute 'dirtyShortRev' missing`.

The cost is a third layer of pinning. `flake.lock` records a rev for each implementation, on top of this repo's gitlinks and each implementation's own `hbt-data` pin. After `scripts/update-submodules.sh` moves a pointer, re-lock to match:

```sh
nix flake update hbt-hs hbt-go hbt-ocaml hbt-rs
```

`inputs.self.submodules = true` is set, so `self` is the tree *with* the submodules rather than with four empty directories — without it the relative `git+file:` inputs resolve only because the flake happens to be evaluated in place, not from a copied source. It does not remove the need for `git+file:`, since it still gives a `path:` input no git metadata. The usual cost of setting it — every submodule tree landing in `src = self` — does not apply here: the `hbt-bench` derivation takes a `lib.fileset`-filtered source of just `hbt_bench/`, `pyproject.toml`, and this README, so it stays 44K and does not rebuild when a pointer moves.

## Layout

| Path | What |
|---|---|
| `hbt_bench/` | the benchmark harness (Python, flit) |
| `benchmarks/` | corpus definition, results, and rendered report |
| `scripts/` | submodule pointer maintenance (bash) |
| `flake.nix` | the harness, the dev shell, `.#bench`, and `.#site` |
| `hbt-{hs,go,ocaml,rs}/` | the implementations, as submodules |
