{
  description = "Benchmarks and analysis for the hbt implementations";

  inputs = {
    # Without this, `self` is the parent tree with the submodules as empty
    # directories, and the relative git+file: inputs below only resolve because
    # the flake happens to be evaluated in place. It makes them work from a
    # copied source too. The `src` below is filtered, so pulling the submodule
    # trees into `self` does not enlarge the hbt-bench derivation or make it
    # rebuild whenever a pointer moves.
    self.submodules = true;

    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    flake-utils.url = "github:numtide/flake-utils";

    # The four implementations, as inputs, so `nix run .#bench` builds them and
    # runs the whole matrix in one command.
    #
    # These emit a deprecation warning: relative git+file: paths resolve against
    # the process working directory rather than the flake, and NixOS/nix#12281
    # says they will stop working. That issue prescribes a replacement --
    # `inputs.self.submodules = true` (set below) plus a bare path literal,
    # `hbt-rs.url = ./hbt-rs`. It was tried here and does not work: three of the
    # four subflakes derive their version from `self.shortRev or
    # self.dirtyShortRev`, and a path input carries no git metadata, so hbt-go
    # fails to evaluate with "attribute 'dirtyShortRev' missing". Making that
    # form usable means teaching the four upstreams to tolerate a revision-less
    # self; until then this is the option that works. The nix maintainers say
    # the warning may be ignored.
    #
    # `github:henrytill/hbt-*` would also silence it -- those inputs carry rev
    # and shortRev, so the version derivation above is satisfied, and each
    # subflake's own `self.submodules = true` still pulls in its hbt-data. That
    # is deliberately not done: it would make the submodule checkouts dead
    # weight, and working inside them is what this repo is for. Building an
    # implementation from github means the working tree is no longer what gets
    # benchmarked.
    #
    # The warning about not reading HEAD is separate and benign. Verified by
    # detaching hbt-go three commits back and re-locking: nix recorded the
    # checked-out revision, not master. It follows the gitlink correctly.
    #
    # The real cost is a third layer of pinning: the lock records a rev for
    # each, on top of this repo's gitlinks and each implementation's own
    # hbt-data pin. After scripts/update-submodules.sh moves a pointer, re-lock
    # to match -- `nix flake update hbt-hs hbt-go hbt-ocaml hbt-rs`.
    hbt-hs.url = "git+file:./hbt-hs";
    hbt-go.url = "git+file:./hbt-go";
    hbt-ocaml.url = "git+file:./hbt-ocaml";
    hbt-rs.url = "git+file:./hbt-rs";
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
      ...
    }@inputs:
    let
      # Derived from the inputs rather than restated: the implementations have
      # to be listed as inputs anyway, and a second literal list ten lines
      # below is one more thing to keep in sync. Order is irrelevant here --
      # the report's column order comes from hbt.bench.core.
      names = builtins.filter (nixpkgs.lib.hasPrefix "hbt-") (builtins.attrNames inputs);
    in
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = nixpkgs.legacyPackages.${system};

        # The CLI alone, where a flake distinguishes it from its default. Only
        # the `hbt` binary is ever benchmarked, but hbt-hs's default is `all`,
        # which joins hbt-cli with pinboard-client: a 4.3 GB closure in place of
        # 83 MB, built and fetched on every cold `nix run .#bench`. Keyed off
        # the attribute rather than the implementation name, so it stays true
        # for whoever exposes one next -- hbt-ocaml already does, where the two
        # are the same derivation, and hbt-go and hbt-rs have no such split.
        cliPackage =
          name:
          let
            ps = inputs.${name}.packages.${system};
          in
          ps.hbt-cli or ps.default;

        hbtBench = pkgs.python3Packages.buildPythonApplication {
          pname = "hbt-bench";
          # From __init__.py, which pyproject's dynamic version already makes
          # the one source the wheel is built from. Restating it here would let
          # the store path -- a provenance label, in a tool whose subject is
          # provenance -- go on claiming 0.1.0 after a bump.
          version = builtins.head (
            builtins.match ".*__version__ = \"([^\"]+)\".*" (builtins.readFile ./hbt/bench/__init__.py)
          );
          pyproject = true;
          build-system = [ pkgs.python3Packages.flit-core ];
          dependencies = with pkgs.python3Packages; [
            click
            jinja2
            tabulate
          ];
          # The type check is a flake check, not a build step. Leaving it here
          # made mypy -- a ~70 MiB closure on top of python3 -- a build input of
          # every consumer, including the pages build, which only needs to run
          # pandoc over a JSON file.
          doCheck = false;
          # Just the package, not the whole tree: keeps the four submodule trees
          # that self.submodules pulls in out of the derivation, and stops
          # AGENTS.md edits from triggering a rebuild. `./hbt/bench` rather than
          # `./hbt`, so that a second member of the namespace does not become a
          # source input of this one.
          src = pkgs.lib.fileset.toSource {
            root = ./.;
            fileset = pkgs.lib.fileset.unions [
              ./hbt/bench
              ./pyproject.toml
              ./README.md
            ];
          };
          # hyperfine is a runtime dependency that is not a Python package, so
          # it goes on PATH rather than into dependencies.
          makeWrapperArgs = [ "--prefix PATH : ${pkgs.lib.makeBinPath [ pkgs.hyperfine ]}" ];
        };

        # The end-to-end benchmark: hbt-bench with all four implementations
        # built from their own flakes, passed as --binary/--revision so the
        # run does not depend on the result-hbt-* symlinks being current and
        # records the revision it actually built. The bare hbt-bench package
        # still falls back to those symlinks for ad-hoc use.
        bench =
          pkgs.runCommand "hbt-bench-all"
            {
              nativeBuildInputs = [ pkgs.makeWrapper ];
            }
            ''
              makeWrapper ${hbtBench}/bin/hbt-bench $out/bin/hbt-bench \
                ${pkgs.lib.concatMapStringsSep " " (name: ''
                  --add-flags "--binary ${name}=${cliPackage name}/bin/hbt" \
                  --add-flags "--revision ${name}=${inputs.${name}.rev}" \
                '') names}
            '';

        # The published page. Built rather than committed, in the shape atp
        # uses: the HTML is a derivation output under share/doc, and CI only
        # uploads and deploys it. Nothing is benchmarked in CI -- shared,
        # throttled runners are not a benchmarking environment -- so this
        # renders the results.json that a local run committed.
        site =
          pkgs.runCommand "hbt-analysis-site"
            {
              nativeBuildInputs = [
                hbtBench
                pkgs.pandoc
              ];
            }
            ''
              html=$out/share/doc/hbt-analysis/html
              mkdir -p "$html"
              hbt-bench --report-only ${./benchmarks/results.json} > report.md
              pandoc report.md --defaults ${./benchmarks/defaults.yml} \
                --metadata "pagetitle=hbt benchmark" -o "$html/index.html"
            '';
      in
      {
        packages.hbt-bench = hbtBench;
        packages.bench = bench;
        packages.site = site;
        packages.default = hbtBench;

        apps.bench = {
          type = "app";
          program = "${bench}/bin/hbt-bench";
        };
        apps.default = self.apps.${system}.bench;

        # A flake check rather than a build step, so mypy is not a build input
        # of everything that consumes hbt-bench.
        #
        # pyproject.toml is the one place this project's tool configuration
        # lives, so the check reads it with --config-file rather than restating
        # any of it here -- `strict` and `explicit_package_bases` both come from
        # the file, and `mypy hbt/bench` in the dev shell is then the same check
        # this runs. --config-file and not a copy next to the source, because
        # mypy is happy to read config from anywhere.
        #
        # The source itself does have to be copied: explicit_package_bases makes
        # the working directory the package root, so the tree has to sit at
        # `hbt/bench` for the module to be `hbt.bench`, and a store path's
        # basename is a hash. Copying ./hbt/bench rather than ./hbt for the same
        # reason `src` above does: this check carries one package's dependency
        # set, and a second member of the namespace would bring its own.
        checks.mypy =
          pkgs.runCommand "hbt-bench-mypy"
            {
              nativeBuildInputs = [
                pkgs.python3Packages.mypy
                # tabulate ships no py.typed, so its stubs have to be added
                # alongside it; mypy prefers a stub package over an untyped one
                # when both are present. A stub is not a runtime dependency, so
                # this is the one thing the package cannot supply. jinja2 and
                # tabulate themselves come from hbtBench, the same inheritance
                # `inputsFrom` gives the dev shell below -- a dependency added
                # to `dependencies` above reaches this check on its own.
                pkgs.python3Packages.types-tabulate
              ]
              ++ hbtBench.propagatedBuildInputs;
            }
            ''
              mkdir hbt
              cp -r ${./hbt/bench} hbt/bench
              cp -r ${./tests} tests
              mypy --config-file ${./pyproject.toml} hbt/bench tests
              touch $out
            '';

        devShells.default = pkgs.mkShell {
          inputsFrom = [ hbtBench ];
          packages =
            (with pkgs; [
              hyperfine
              nixfmt
              pyright
              shellcheck
              shfmt
            ])
            ++ (with pkgs.python3Packages; [
              black
              flake8
              isort
              mypy
              pylint
              types-tabulate
            ]);
        };
      }
    );
}
