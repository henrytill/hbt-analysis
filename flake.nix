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
      names = [
        "hbt-hs"
        "hbt-go"
        "hbt-ocaml"
        "hbt-rs"
      ];

      makeHbtBench =
        pkgs:
        pkgs.python3Packages.buildPythonApplication {
          pname = "hbt-bench";
          version = "0.1.0";
          pyproject = true;
          build-system = [ pkgs.python3Packages.flit-core ];
          nativeCheckInputs = with pkgs.python3Packages; [ mypy ];
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
          checkPhase = "mypy hbt_bench";
        };

      # The end-to-end benchmark: hbt-bench with all four implementations built
      # from their own flakes and pointed at by HBT_BENCH_*, so a run does not
      # depend on the result-hbt-* symlinks being current. The bare hbt-bench
      # package still falls back to those symlinks for ad-hoc use.
      makeBench =
        pkgs: system:
        pkgs.runCommand "hbt-bench-all"
          {
            nativeBuildInputs = [ pkgs.makeWrapper ];
          }
          ''
            makeWrapper ${makeHbtBench pkgs}/bin/hbt-bench $out/bin/hbt-bench \
              ${pkgs.lib.concatMapStringsSep " " (name: ''
                --set HBT_BENCH_${pkgs.lib.toUpper (builtins.replaceStrings [ "-" ] [ "_" ] name)} \
                  ${inputs.${name}.packages.${system}.default}/bin/hbt \
              '') names}
          '';
      # The published page. Built rather than committed, in the shape atp uses:
      # the HTML is a derivation output under share/doc, and CI only uploads and
      # deploys it. Nothing is benchmarked in CI -- shared, throttled runners
      # are not a benchmarking environment -- so this renders the results.json
      # that a local run committed.
      results = ./benchmarks/results.json;

      makeSite =
        pkgs:
        let
          # Keeps the page (and CI) green before the first local run has been
          # committed, rather than failing evaluation on a missing file.
          placeholder = pkgs.writeText "no-results.md" ''
            # hbt benchmark

            No results have been committed yet. Run `nix run .#bench` on a quiet
            machine and commit `benchmarks/results.json`.
          '';
          report =
            if builtins.pathExists results then
              "hbt-bench --report-only ${results} > report.md"
            else
              "cp ${placeholder} report.md";
          defaults = ./benchmarks/defaults.yml;
        in
        pkgs.runCommand "hbt-analysis-site"
          {
            nativeBuildInputs = [
              (makeHbtBench pkgs)
              pkgs.pandoc
            ];
          }
          ''
            html=$out/share/doc/hbt-analysis/html
            mkdir -p "$html"
            ${report}
            pandoc report.md --defaults ${defaults} --metadata "pagetitle=hbt benchmark" -o "$html/index.html"
          '';
    in
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
      in
      {
        packages.hbt-bench = makeHbtBench pkgs;
        packages.bench = makeBench pkgs system;
        packages.site = makeSite pkgs;
        packages.default = self.packages.${system}.hbt-bench;

        apps.bench = {
          type = "app";
          program = "${self.packages.${system}.bench}/bin/hbt-bench";
        };
        apps.default = self.apps.${system}.bench;

        devShells.default = pkgs.mkShell {
          inputsFrom = [ self.packages.${system}.hbt-bench ];
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
              pylint
            ]);
        };
      }
    );
}
