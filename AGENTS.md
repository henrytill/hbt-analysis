# AGENTS.md

This file provides guidance to coding agents working in this repository. `CLAUDE.md` is a symlink to it — edit only this file.

**Two rules that prevent most wasted moves here:**

1. **Nothing works outside Nix.** There is no system-wide `cargo`, `go`, `dune`, `ghc`, or `cabal`. Enter a dev shell first: `cd hbt-rs && nix develop`. The root has one too, for the benchmark harness and the bash scripts.
2. **Every submodule pointer goes stale**, in two layers. A failing golden test is more often an old pin than a parser bug — see [The shared test-data contract](#the-shared-test-data-contract-hbt-data). `scripts/update-submodules.sh -n` reports the drift in a few seconds.

## What this repository is

`hbt-analysis` is an umbrella repo for comparing four independent implementations of **hbt** ("Heterogeneous Bookmark Transformation"), a bookmark parser/transformer. Each implementation is a git submodule with its own upstream repo, its own flake, and its own toolchain:

| Submodule | Language | Upstream |
|---|---|---|
| `hbt-hs` | Haskell (cabal multi-package) | github.com/henrytill/hbt-hs |
| `hbt-go` | Go (GNUmakefile) | github.com/henrytill/hbt-go |
| `hbt-ocaml` | OCaml (dune) | github.com/henrytill/hbt-ocaml |
| `hbt-rs` | Rust (cargo workspace) | github.com/henrytill/hbt-rs |

The root holds one piece of source: `hbt/bench/`, the benchmark harness (Python, flit-packaged, `nix run .#bench`). It builds all four implementations from their own flakes, records `--info` entity counts, times each input across all four with `hyperfine`, and writes `benchmarks/results.json` — the one committed benchmark file. The Markdown report and the HTML page are both translations of it and are never committed: the report renders to stdout (or to `-r FILE`), and the page is a Nix build output. `README.md` documents it. `nix build .#site` renders the committed `benchmarks/results.json` into a standalone HTML page under `share/doc/hbt-analysis/html/`, which `.github/workflows/pages.yml` deploys to GitHub Pages — the same shape `henrytill/atp` uses. **Nothing is ever timed in CI**; the workflow only renders results produced by a local run. It replaced an org-babel notebook whose numbers carried no provenance.

**You almost certainly cannot re-run the real benchmark.** The corpus in `benchmarks/corpus.toml` points at the author's private bookmark exports (`~/src/notes/all-2024.md`, `~/src/bookmarks/*`); they are in no repo and do not exist in a fresh checkout, so refreshing the numbers is not work an agent can do. Point the corpus at the `hbt-data` fixtures for a smoke test — they exercise every code path but are far too small to time meaningfully.

The four implementations are `git+file:` flake inputs of the root flake, which is what makes `.#bench` work end to end. That is a **third layer of pinning** on top of the gitlinks and each implementation's own `hbt-data` pin: after `scripts/update-submodules.sh` moves a pointer, `nix flake update hbt-hs hbt-go hbt-ocaml hbt-rs` is needed for `.#bench` to build the new revisions. `path:` inputs would avoid the extra pin but cannot work — three of the four subflakes read `self.shortRev or self.dirtyShortRev`, and path inputs carry no git metadata, so `hbt-go` dies with `attribute 'dirtyShortRev' missing`. That also rules out the replacement [NixOS/nix#12281](https://github.com/NixOS/nix/issues/12281) prescribes for the deprecation warning these inputs emit (bare path literal + `inputs.self.submodules`), so **don't "fix" the warning** — it was tried, it fails, and the comment in `flake.nix` records why. Making it work is an upstream change in all four implementations. `inputs.self.submodules = true` is set; the `hbt-bench` derivation takes a `lib.fileset`-filtered `src`, so the submodule trees it pulls into `self` never reach the build.

**The `result-hbt-*` symlinks are working binaries**, not just build output. Use them for any question about what an implementation *does* — it costs nothing and needs no Nix invocation:

```sh
./result-hbt-go/bin/hbt --info hbt-rs/test-data/markdown/basic.input.md
```

Rebuild only when you have moved a pointer. They are gitignored Nix store paths.

Work in this repo is usually *within* one submodule. Commits at the root are almost always submodule pointer bumps (`git add hbt-rs && git commit`), and changes to a submodule must be committed and pushed in that submodule's own repo first.

To advance the four top-level pointers to their upstream default branches, run `scripts/update-submodules.sh` — `-n`/`--dry-run` reports what would move without touching anything. It stages the bumps and leaves them for you to review and commit; it reports and skips a submodule it cannot check out (a worktree left dirty by a local build), and exits non-zero if any were skipped. It also re-checkouts the nested `hbt-data` copies to whatever the new revisions pin, which is otherwise the step whose omission shows up as mass golden-test failures. A monthly workflow runs the same script and opens a pull request; that half lives in `scripts/open-submodule-pr.sh`, which refuses to run outside CI.

## Building and testing

There is no system-wide toolchain and none is vendored: every compiler, linter, and language server comes from that project's flake. Never run a bare build/test/lint command in a submodule, and never conclude a toolchain is "missing" or "broken" — it just isn't in scope yet.

### Building

From the root:

```sh
nix build ./hbt-hs#    -o result-hbt-hs      # default = `all`: bin/{hbt,pinboard-client}
nix build ./hbt-go#    -o result-hbt-go      # bin/{hbt,pinboard}
nix build ./hbt-ocaml# -o result-hbt-ocaml   # bin/hbt
nix build ./hbt-rs#    -o result-hbt-rs      # bin/hbt
```

Each flake exposes a default plus per-binary and `-static` variants; `hbt-go` exposes only `hbt`. Enumerate rather than guessing — `nix eval ./hbt-rs#packages.x86_64-linux --apply builtins.attrNames` answers in ~2s without realising anything, and `nix build … --dry-run --no-link` tells you whether a build is instant or cold before you commit to it.

Static builds go through the `-static` attrs, **not** through a dev shell: `dune build --profile static` inside the `hbt-ocaml` shell fails to link (`cannot find -lm/-lpthread/-lc`) because the static profile needs the musl package set. Use `nix build ./hbt-ocaml#hbt-cli-static`.

The root defines `checks.mypy`. Among the submodules only `hbt-ocaml` and `hbt-rs` define flake `checks`; `nix flake check` is a no-op in the other two, whose CI runs `nix build` / `make` instead. `nix flake check ./hbt-rs# --no-build` reports what would run without running it.

### Dev shells

Each implementation has a `devShells.default`. **`nix develop` does not change directory**, so run it from inside the submodule — `nix develop ./hbt-rs# --command cargo test` from the root fails with "could not find `Cargo.toml`".

Entering the shell costs 4–7 seconds, and `hbt-ocaml`'s re-runs its `linkNodeModulesHook` every time. For more than one command, enter it once:

```sh
cd hbt-rs && nix develop      # then work normally
```

Reserve `nix develop --command …` for a single one-shot or for scripting.

Inside each shell:

```sh
# hbt-rs — rust-analyzer, cargo-deny
cargo build && cargo test
cargo test -p hbt-test --test parsing markdown::test_basic   # generated cases are named test_<stem>
cargo test -p hbt-test --test parsing -- --list              # list them
cargo deny check                                             # see caveat below

# hbt-go — gopls, gotools, staticcheck, universal-ctags
make all           # bin/hbt, bin/pinboard
make test
make lint
go test ./test -run TestMarkdownBasicYAML     # generated golden case: Test<Category><Stem><Format>

# hbt-ocaml — ocaml-lsp, ocp-index, clang-tools, node/npm
dune build
dune runtest
npm run fmt        # node_modules linked by the shell's hook; do not `npm install`

# hbt-hs — GHC 9.10.3 (the one pinned toolchain: `ghcName` in flake.nix), ghcid, fourmolu, hlint, weeder
cabal build all
cabal test all
cabal test hbt-test    # core golden tests
```

`make` targets are defined in `hbt-go/GNUmakefile`; every other tool version floats with `flake.lock`.

**Caveat on `cargo deny check`:** the dev shell has network and fetches the RUSTSEC advisory DB; the Nix sandbox does not. So an `advisories` failure that appears under `nix develop` but not under `nix flake check` is a live advisory against a pinned dependency, not something your working tree introduced. (Currently `quick-xml`, via `hbt-pinboard`.) `bans`, `licenses`, and `sources` run identically in both.

## Cross-implementation architecture

A map of the shared design, not a specification — it is maintained in four upstreams and none of them enforce it. `collection.schema.json` and the fixtures are the authorities.

All four implementations mirror the same shape, so a change in one usually has three counterparts:

- **Entity** (`entity.rs` / `Hbt/Entity.hs` / `entity.ml` / `internal/types/entity.go`) — a bookmark: URL, timestamps, names, labels (tags), `extended`, `isFeed`, etc. URLs and labels are newtype-wrapped, not bare strings.
- **Collection** (`collection.*`) — a *graph*, not a list: `nodes: Vec<Entity>` plus an adjacency `edges` list, with a URL→index map for deduplication. Parsers insert entities and add parent/child edges (e.g. Markdown heading nesting, HTML folder nesting). The serialized form carries a semver `version` field checked against `^0.1.0`.
- **Parsers** — Markdown, HTML (Netscape bookmark files), and Pinboard XML/JSON. Format is selected by file extension or an explicit `--from`/`-f` flag.
- **Formatters** — YAML and HTML (rendered from a Netscape-bookmarks template).
- **`attic/`** — parked experiments present in all four (Belnap four-valued logic and Belnap vectors). Not part of the CLI.
- **`pinboard/`** — Pinboard post/note types, plus an API client in `hbt-go` and `hbt-hs` (`pinboard-client`).

The CLI surface is intentionally near-identical: `--info`, `--list-tags`, `--mappings FILE`, `-o OUTPUT`, and format selection. Rust uses clap (`-f/--from`, `-t/--to`, plus `--schema`, which emits `collection.schema.json`); Go uses stdlib `flag`, which accepts both `-info` and `--info`; Haskell uses `GetOpt`.

Parity is close but not exact, and no shared test enforces it — check the `result-hbt-*` binaries rather than assuming. Two divergences seen recently: `--info` output text differs (Go prints `Collection contains N entities`, the rest print `<path>: N entities`), and `hbt-hs` may still exit with `Warning: --mappings option not yet implemented`.

## The shared test-data contract (`hbt-data`)

All four submodules vendor the **same** golden data repo, `github.com/henrytill/hbt-data`, at different paths:

- `hbt-rs/test-data/`, `hbt-go/test/testdata/`, `hbt-ocaml/core/data/`, `hbt-hs/core/test/data/`

It is a small, data-only repo. Layout is `<category>/<name>.input.<ext>` paired with `<name>.expected.yaml` and, for HTML, `<name>.expected.html`, under `html/`, `markdown/`, `pinboard/xml/`, `pinboard/json/`. Case names describe the parsing edge they pin down (`inverted_parent`, `link_text_with_backticks`, `empty_link`, ...).

Test cases are *derived* from these filenames rather than hand-written, by a different mechanism in each implementation: `hbt-rs` expands them with the `hbt_test_macros::test_parser!` proc macro, `hbt-go` generates them via `go generate ./test` (`test/testgen.go`), `hbt-ocaml` globs them in `core/dune`'s `data_test`, and `hbt-hs` walks the directory at runtime (`core/test/TestData.hs`). So a test name never matches a source file, and adding a case touches no test source.

This repo is the cross-language behavioral spec, so treat it accordingly:

- **Adding or changing a test case means committing to `hbt-data`**, then bumping the submodule pointer in each implementation. Landing a parser behavior change is a multi-repo operation: `hbt-data` first, then each implementation.
- **The pins drift, in two layers.** Each implementation pins `hbt-data` independently, *and* this repo pins each implementation independently. Assume both are stale; a "failure" in one implementation is very often an old pin. Both local layers come from one command:
  ```sh
  git submodule status --recursive
  ```
  Read that output carefully: the parenthetical is `git describe` against the submodule's own refs, not the branch it is on. All four are checked out detached at a pinned revision, so `(heads/master)` there means "this revision is also master's tip", and `(heads/master-4-gdd6a235)` means "four commits past master" — neither is a statement about a checked-out branch. `git -C <sub> rev-parse --abbrev-ref HEAD` is the question you actually want to ask.

  For the upstream side, compare against each project's default branch (`master` for all five):
  ```sh
  gh api repos/henrytill/hbt-data/commits/master --jq '.sha[0:7]'
  for s in hbt-rs:test-data hbt-go:test/testdata hbt-ocaml:core/data hbt-hs:core/test/data; do
    d=${s%%:*}
    printf '%-10s head=%s data=%s\n' "$d" \
      "$(gh api repos/henrytill/$d/commits/master --jq '.sha[0:7]')" \
      "$(gh api repos/henrytill/$d/contents/${s##*:}?ref=master --jq '.sha[0:7]')"
  done
  ```
  Reproduce against upstream before filing anything — the code checked out here is not what the trackers describe.
- **`collection.schema.json`** at the data repo root is the JSON Schema for the serialized `Collection`, generated by the Rust CLI's `hbt --schema` and never hand-edited. It is the authority on field names and types; serialization is `camelCase`, `createdAt`/`updatedAt` are Unix timestamps, `length` is the node count, and `value` is a list of `{id, entity, edges}`. When the schema changes, regenerate it from `hbt-rs` and commit it to `hbt-data`.
- Golden output is timezone-sensitive; `hbt-ocaml` pins `TZ=UTC` in its dune test action.
- Clones need `--recurse-submodules`; CI checks out every implementation with `submodules: true`. A missing data submodule shows up as mass test failures, not as a clear error.

## Cross-repo issue tracking

There is no central tracker and no prose specification. Each implementation has its own issue tracker, `hbt-data` has a fourth, and no repo carries labels or milestones — so a concern that spans implementations is filed 2–5 times, once per repo, in each repo's own vocabulary, with ad-hoc and often one-directional cross-links.

- **Search all five trackers before filing or fixing:** `gh issue list -R henrytill/<repo> --state all` across `hbt-{hs,go,ocaml,rs}` and `hbt-data`. A question that looks new in one repo is usually already argued out — sometimes already decided and closed — in another.
- **No single tracker reflects reality.** A behavior can be closed in two repos and open in the others, or fixed in a repo that never had an issue filed. The trackers are not an index of what is settled.
- **Behavioral questions belong in `hbt-data`**, since a decision is only real once a fixture pins it — but `hbt-data` can currently express only successful parses, so accept-vs-reject decisions have no home at all (tracked as `henrytill/hbt-data#11`).

## Formatting and lint config

Match the submodule's own config, and never import conventions across languages. These formatters and linters are pinned by each flake and exist only inside that project's dev shell.

Each submodule carries its own: `.ocamlformat` plus `.prettierrc.cjs` for the JS stubs in `hbt-ocaml/attic`; `fourmolu.yaml`, `.hlint.yaml`, and `weeder.toml` in `hbt-hs`; `rust-toolchain.toml` and `deny.toml` in `hbt-rs`; `make fmt` / `make lint` in `hbt-go`.

### The root

Python in `hbt/bench/` follows the same conventions as the author's other Python projects (`henrytill/pagewielder`, `henrytill/ananke-py`): flit-core build backend with `dynamic = ["version", "description"]`, black at **line-length 120**, isort with the black profile, flake8 (`.flake8`, `extend-ignore = E203`), mypy `strict`, pylint at 120 with a small disable list, and pyright `strict`. All of them are in the root dev shell, and all of them pass clean — keep it that way:

```sh
nix develop
black hbt && isort hbt && flake8 hbt && mypy hbt && pylint hbt && pyright hbt
```

The set of implementations is read from `.gitmodules` at runtime, the same way `scripts/update-submodules.sh` and `scripts/open-submodule-pr.sh` do it — adding or removing a submodule needs no edit in `hbt/bench/`, and `flake.nix` derives the same set from its inputs. Report columns are sorted, so `.gitmodules` ordering does not leak into the output.

`hbt/bench/__init__.py` is checked in rather than generated: the sibling projects derive `__version__` from a `VERSION` file and the git ref via a `run.py`, and this package is a local tool that is never distributed, so it does not carry that machinery.

`hbt` is a [PEP 420](https://peps.python.org/pep-0420/) namespace portion: it deliberately has **no `__init__.py`**, and the importable package is `hbt.bench`. Nothing else lives under `hbt.` yet — the namespace is there so a future Python member (bindings to `hbt-rs`, say) can join it without renaming this one. The distribution and the script are still `hbt-bench`; flit cannot infer a dotted module from a dist name, so `[tool.flit.module] name` states it. The same non-inference bites the checkers, which otherwise read `hbt/bench` as a top-level `bench` and then cannot resolve `hbt.bench` — mypy needs `explicit_package_bases`, pylint needs `source-roots = ["."]`, and both are set in `pyproject.toml`. `checks.mypy` in `flake.nix` builds its own directory to get a tree at `hbt/bench`, and points mypy at `pyproject.toml` with `--config-file` so it runs the same check the dev shell does rather than a second opinion assembled from flags.

Bash in `scripts/` uses **hard tabs, tab-width 8**. `shellcheck` and `shfmt` are in the root dev shell; the flag set that matches the existing style is:

```sh
shellcheck scripts/*.sh && shfmt -d -i 0 -sr -bn -ci scripts/*.sh
```

`flake.nix` is `nixfmt`-formatted (`nixfmt --check flake.nix`), also from the root dev shell.

One trap in `flake.nix`: it reads `./benchmarks/results.json` from the *copied* flake source, which contains only what git tracks. An untracked `results.json` is invisible to `nix build .#site`. `git add` it before wondering why the page did not change.
