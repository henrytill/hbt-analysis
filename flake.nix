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
    flake-compat = {
      url = "github:edolstra/flake-compat";
      flake = false;
    };

    # The four implementations, as inputs, so `nix run .#bench` builds them and
    # runs the whole matrix in one command.
    #
    # git+file: rather than path:, which would be the lighter choice -- three of
    # the four subflakes derive their version from `self.shortRev or
    # self.dirtyShortRev`, and a path input carries no git metadata at all, so
    # they fail to evaluate with "attribute 'dirtyShortRev' missing".
    #
    # The cost is a third layer of pinning: the lock records a rev for each, on
    # top of this repo's gitlinks and each implementation's own hbt-data pin.
    # After scripts/update-submodules.sh moves a pointer, re-lock to match --
    # `nix flake update hbt-hs hbt-go hbt-ocaml hbt-rs`.
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
      # the report's column order comes from hbt_bench.core.
      names = builtins.filter (nixpkgs.lib.hasPrefix "hbt-") (builtins.attrNames inputs);
    in
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = nixpkgs.legacyPackages.${system};

        hbtBench = pkgs.python3Packages.buildPythonApplication {
          pname = "hbt-bench";
          version = "0.1.0";
          pyproject = true;
          build-system = [ pkgs.python3Packages.flit-core ];
          # The type check is a flake check, not a build step. Leaving it here
          # made mypy -- a ~70 MiB closure on top of python3 -- a build input of
          # every consumer, including the pages build, which only needs to run
          # pandoc over a JSON file.
          doCheck = false;
          # Just the package, not the whole tree: keeps the four submodule trees
          # that self.submodules pulls in out of the derivation, and stops
          # AGENTS.md edits from triggering a rebuild.
          src = pkgs.lib.fileset.toSource {
            root = ./.;
            fileset = pkgs.lib.fileset.unions [
              ./hbt_bench
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
                  --add-flags "--binary ${name}=${inputs.${name}.packages.${system}.default}/bin/hbt" \
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
        # of everything that consumes hbt-bench. The copy is because mypy needs
        # the directory named for the package, and a store path is not.
        checks.mypy =
          pkgs.runCommand "hbt-bench-mypy" { nativeBuildInputs = [ pkgs.python3Packages.mypy ]; }
            ''
              cp -r ${./hbt_bench} hbt_bench
              mypy --strict hbt_bench
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
            ]);
        };
      }
    );
}
