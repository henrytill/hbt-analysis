# AGENTS.md

This file provides guidance to coding agents working in this repository. `CLAUDE.md` is a symlink to it, so Claude Code reads the same content.

## What this repository is

`hbt-analysis` is an umbrella repo for comparing four independent implementations of **hbt** ("Heterogeneous Bookmark Transformation"), a bookmark parser/transformer. Each implementation is a git submodule with its own upstream repo, its own flake, and its own toolchain:

| Submodule | Language | Upstream |
|---|---|---|
| `hbt-hs` | Haskell (cabal multi-package) | github.com/henrytill/hbt-hs |
| `hbt-go` | Go (GNUmakefile) | github.com/henrytill/hbt-go |
| `hbt-ocaml` | OCaml (dune) | github.com/henrytill/hbt-ocaml |
| `hbt-rs` | Rust (cargo workspace) | github.com/henrytill/hbt-rs |

The root itself contains no source code. `README.org` is a literate org-babel benchmark notebook: each section builds one implementation with Nix into `result-hbt-*` and records `--info` entity counts and `hyperfine` timings against the author's local corpora (`~/src/notes/all-2024.md`, `~/src/bookmarks/*`). Re-running those blocks (or `nix build ./hbt-X# -o result-hbt-X` plus the hyperfine command) is how comparative results get refreshed. The `result-hbt-*` symlinks are gitignored Nix store paths.

Work in this repo is usually *within* one submodule. Commits at the root are almost always submodule pointer bumps (`git add hbt-rs && git commit`), and changes to a submodule must be committed and pushed in that submodule's own repo first.

## Building and testing

**Nothing here works outside Nix.** There is no system-wide `cargo`, `go`, `dune`, `ghc`, or `cabal` — `command -v cargo go dune cabal ghc` at the root returns nothing. Every compiler, linter, and language server comes from that project's flake. Never run a bare build/test/lint command in a submodule and never conclude a toolchain is "missing" or "broken"; it just isn't in scope yet.

### Building

From the root (all four verified working):

```sh
nix build ./hbt-hs#    -o result-hbt-hs      # default = `all`: bin/{hbt,pinboard-client}
nix build ./hbt-go#    -o result-hbt-go      # bin/{hbt,pinboard}
nix build ./hbt-ocaml# -o result-hbt-ocaml   # bin/hbt
nix build ./hbt-rs#    -o result-hbt-rs      # bin/hbt
```

Other package attrs: `hbt-hs` → `all-static`, `hbt-cli`, `hbt-cli-static`, `hbt-pinboard-client{,-static}`, `hbt-attic`; `hbt-ocaml` → `hbt-cli`, `hbt-cli-static`, `hbt-attic`; `hbt-rs` → `hbt`, `hbt-static`; `hbt-go` → `hbt` only.

Static builds go through these attrs, **not** through a dev shell. `dune build --profile static` inside the `hbt-ocaml` shell fails to link (`cannot find -lm/-lpthread/-lc`) because the static profile needs the musl package set; use `nix build ./hbt-ocaml#hbt-cli-static` instead.

Only `hbt-ocaml` and `hbt-rs` define flake `checks`, and both pass:

```sh
nix flake check ./hbt-rs#      # cargo-clippy, cargo-fmt, cargo-deny, hbt
nix flake check ./hbt-ocaml#   # hbt-cli, hbt-attic
```

`hbt-hs` and `hbt-go` have no `checks` attribute — `nix flake check` there is a no-op, and their CI runs `nix build` / `make` instead.

### Dev shells

Each implementation has a `devShells.default`. **`nix develop` does not change directory**, so run it from inside the submodule — `nix develop ./hbt-rs# --command cargo test` from the root fails with "could not find `Cargo.toml`". Use:

```sh
cd hbt-rs && nix develop --command cargo test
```

Commands verified inside each shell:

```sh
# hbt-rs (crane devShell: cargo 1.93, rust-analyzer, cargo-deny)
cargo build && cargo test
cargo test -p hbt-test --test parsing markdown::test_basic   # generated cases are named test_<stem>
cargo test -p hbt-test --test parsing -- --list              # list them
cargo deny check                                             # see caveat below

# hbt-go (go 1.25, gopls, go-tools/staticcheck, universal-ctags)
make all           # bin/hbt, bin/pinboard
make test          # go generate ./test, then go test -v ./...
make lint          # go vet + staticcheck + deadcode (clean)
go test ./test -run TestMarkdownBasicYAML     # generated golden case: Test<Category><Stem><Format>

# hbt-ocaml (dune 3.21, ocaml-lsp, ocp-index, node/npm)
dune build
dune runtest
dune exec cli/main.exe -- --info core/data/markdown/basic.input.md
npm run fmt        # node_modules linked by the shell's linkNodeModulesHook; do not `npm install`

# hbt-hs (cabal 3.16, GHC 9.10.3, ghcid, fourmolu, hlint, weeder via shellFor)
cabal build all
cabal test all
cabal test hbt-test    # core golden tests
```

Caveat on `cargo deny check`: it currently **fails** in the dev shell on a live RUSTSEC advisory for `quick-xml` 0.39.1 (pulled in via `hbt-pinboard`), while `nix flake check ./hbt-rs#`'s `cargo-deny` check passes because the Nix sandbox has no network and so does not fetch the advisory DB. `bans`, `licenses`, and `sources` are clean in both. Don't treat that failure as something the working tree introduced.

Test naming differs from source-file naming because cases are generated from the shared data repo — see below.

## Cross-implementation architecture

All four implementations mirror the same design, so a change in one usually has three counterparts. Names differ but the shape does not:

- **Entity** (`entity.rs` / `Hbt/Entity.hs` / `entity.ml` / `internal/types/entity.go`) — a bookmark: URL, timestamps, names, labels (tags), `extended`, `isFeed`, etc. URLs and labels are newtype-wrapped, not bare strings.
- **Collection** (`collection.*`) — a *graph*, not a list: `nodes: Vec<Entity>` plus an adjacency `edges` list, with a URL→index map for deduplication. Parsers insert entities and add parent/child edges (e.g. Markdown heading nesting, HTML folder nesting). The serialized form carries a semver `version` field checked against `^0.1.0`.
- **Parsers** — Markdown, HTML (Netscape bookmark files), and Pinboard XML/JSON. Format is selected by file extension or an explicit `--from`/`-f` flag.
- **Formatters** — YAML and HTML (the HTML output is rendered from a Netscape-bookmarks template).
- **`attic/`** — parked experiments present in all four (Belnap four-valued logic and Belnap vectors). Not part of the CLI; treat as separate from the bookmark pipeline.
- **`pinboard/`** — Pinboard post/note types, plus an API client in `hbt-go` and `hbt-hs` (`pinboard-client`).

The CLI surface is intentionally near-identical: `--info`, `--list-tags`, `--mappings FILE`, `-o OUTPUT`, and format selection. Rust uses clap (`-f/--from`, `-t/--to`, plus `--schema`, which emits `collection.schema.json`); Go uses stdlib `flag`, which accepts both `-info` and `--info`; Haskell uses `GetOpt`.

Parity is close but not exact — verify against the binary rather than assuming:

- `--info` output text differs. Rust/OCaml/Haskell print `<path>: N entities`; Go prints `Collection contains N entities`. Don't diff these across implementations expecting identical stdout.
- `hbt-hs` exits with `Warning: --mappings option not yet implemented` rather than applying mappings.

## The shared test-data contract (`hbt-data`)

All four submodules vendor the **same** golden data repo, `github.com/henrytill/hbt-data`, at different paths:

- `hbt-rs/test-data/`, `hbt-go/test/testdata/`, `hbt-ocaml/core/data/`, `hbt-hs/core/test/data/`

It is a data-only repo (~70 files, no code). Layout is `<category>/<name>.input.<ext>` paired with `<name>.expected.yaml` and, for HTML, `<name>.expected.html`, under `html/`, `markdown/`, `pinboard/xml/`, `pinboard/json/`. Case names describe the parsing edge they pin down (`inverted_parent`, `link_text_with_backticks`, `descending_dates`, `empty_link`, `no_url`, ...). Test cases are *derived* from these filenames rather than hand-written: `hbt-rs` expands them with the `hbt_test_macros::test_parser!` proc macro, `hbt-go` generates them via `go generate ./test` (`test/testgen.go`), `hbt-ocaml` globs them in `core/dune`'s `data_test`.

This repo is the cross-language behavioral spec, so treat it accordingly:

- **Adding or changing a test case means committing to `hbt-data`**, then bumping the submodule pointer in each implementation — not editing a test file. Landing a parser behavior change is a multi-repo operation: `hbt-data` first, then each implementation.
- **The pins drift, in two directions at once.** Each implementation pins `hbt-data` independently, *and* this umbrella repo pins each implementation independently. Both layers are stale. Check both before concluding two implementations disagree:
  ```sh
  # layer 1: what this repo has checked out vs. each implementation's upstream
  for d in hbt-hs hbt-go hbt-ocaml hbt-rs; do
    printf '%-10s local=%s upstream=%s\n' "$d" \
      "$(git -C "$d" rev-parse --short HEAD)" \
      "$(gh api repos/henrytill/$d/commits/master --jq '.sha[0:7]')"
  done

  # layer 2: which hbt-data commit each implementation pins, here and upstream
  echo "hbt-data master: $(gh api repos/henrytill/hbt-data/commits/master --jq '.sha[0:7]')"
  for s in hbt-rs:test-data hbt-go:test/testdata hbt-ocaml:core/data hbt-hs:core/test/data; do
    d=${s%%:*}; p=${s##*:}
    printf '%-10s here=%s upstream=%s\n' "$d" \
      "$(git -C "$d/$p" rev-parse --short HEAD)" \
      "$(gh api repos/henrytill/$d/contents/$p?ref=master --jq '.sha[0:7]')"
  done
  ```
  A "failure" in one implementation is very often just an older pin, not a parser bug. As of 2026-09-08: all four submodules here are months behind their upstreams, so the checked-out code is **not** what the trackers describe; upstream `hbt-rs`/`hbt-go`/`hbt-ocaml` all track `hbt-data` master while upstream `hbt-hs` is five commits behind it. Reproduce against upstream before filing anything.
- **`collection.schema.json`** at the data repo root is the JSON Schema for the serialized `Collection`, generated by the Rust CLI's `hbt --schema`. It is the authority on field names and types; serialization is `camelCase`, `createdAt`/`updatedAt` are Unix timestamps, `length` is the node count, and `value` is a list of `{id, entity, edges}`. When the schema changes, regenerate it from `hbt-rs` and commit it to `hbt-data` — its recent history is largely schema churn plus tightening (`extended` becoming a list and later gaining a uniqueness constraint, `uniqueItems` on edges and update timestamps, integer widths).
- Golden output is timezone-sensitive; `hbt-ocaml` pins `TZ=UTC` in its dune test action.
- Clones need `--recurse-submodules`; CI checks out every implementation with `submodules: true`. A missing data submodule shows up as mass test failures, not as a clear error.

## Cross-repo issue tracking

There is no central tracker and no prose specification. Each implementation has its own issue tracker, `hbt-data` has a fourth, and no repo carries labels or milestones — so a concern that spans implementations is filed 2–5 times, once per repo, in each repo's own vocabulary, with ad-hoc and often one-directional cross-links.

Practical consequences when working an issue here:

- **Search all five trackers before filing or fixing.** `gh issue list -R henrytill/<repo> --state all` across `hbt-{hs,go,ocaml,rs}` and `hbt-data`. A question that looks new in one repo is usually already argued out — sometimes already *decided and closed* — in another.
- **A closed issue in one repo is not a decision anywhere else.** Several behaviors are settled and closed in two implementations while still open in the others.
- **An absent issue does not mean absent behavior.** Fixes have propagated to implementations that never had an issue filed. The trackers are not an index of what is settled.
- **Behavioral questions belong in `hbt-data`**, since a decision is only real once a fixture pins it — but `hbt-data` can currently express only successful parses, so accept-vs-reject decisions have no home at all (see `henrytill/hbt-data#11`).

## Formatting and lint config

Each submodule carries its own: `.ocamlformat` + `.prettierrc.cjs` (for the JS stubs in `hbt-ocaml/attic`, `npm run fmt`), `fourmolu.yaml` + `.hlint.yaml` + `weeder.toml` (hbt-hs), `rust-toolchain.toml` + `deny.toml` (hbt-rs, `clippy::pedantic` is warned on in `core` and `cli`), `make fmt`/`make lint` (hbt-go). Match the submodule's config; do not import conventions across languages. These formatters and linters are pinned by each flake and exist only inside that project's dev shell — `cd` into the submodule and use `nix develop --command ...`, never whatever might be on `PATH`.
