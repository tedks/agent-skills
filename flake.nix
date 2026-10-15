{
  description = "Portable agent skills and the harness-control runtime";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";

  outputs = { nixpkgs, ... }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
      lib = pkgs.lib;
      python = pkgs.python312;
      pythonPackages = pkgs.python312Packages;
      mcp = pythonPackages.mcp;
      testPython = python.withPackages (_: [ mcp ]);
      harnessControl = pythonPackages.buildPythonApplication {
        pname = "agent-harness-control";
        version = "0.1.0";
        pyproject = true;
        src = ./harness-control;

        build-system = [ pythonPackages.setuptools ];
        dependencies = [ mcp ];
        doCheck = false;

        makeWrapperArgs = [
          "--set" "AGENT_SKILLS_ROOT" "${./skills}"
          "--set" "PYTHONDONTWRITEBYTECODE" "1"
          "--unset" "PYTHONHOME"
          "--unset" "PYTHONPATH"
          "--prefix" "PATH" ":" (lib.makeBinPath [ python pkgs.bash pkgs.coreutils ])
        ];

        meta = {
          description = "Pinned self-status and lifecycle MCP server for terminal agents";
          license = lib.licenses.agpl3Only;
          mainProgram = "harness-control";
          platforms = [ system ];
        };
      };
    in
    assert lib.versionAtLeast mcp.version "1.2.0";
    assert lib.versionOlder mcp.version "2";
    {
      packages.${system} = {
        harness-control = harnessControl;
        default = harnessControl;
      };

      apps.${system} = {
        harness-control = {
          type = "app";
          program = lib.getExe harnessControl;
          meta.description = "Run the packaged harness-control MCP server";
        };
        default = {
          type = "app";
          program = lib.getExe harnessControl;
          meta.description = "Run the packaged harness-control MCP server";
        };
      };

      checks.${system} = {
        harness-control-unit = pkgs.runCommand "harness-control-unit" {
          nativeBuildInputs = [ testPython ];
        } ''
          export AGENT_SKILLS_ROOT=${./skills}
          export HARNESS_TEST_SLEEP=${lib.getExe' pkgs.coreutils "sleep"}
          export PYTHONDONTWRITEBYTECODE=1
          export PYTHONPATH=${./harness-control}
          ${testPython}/bin/python3 ${./harness-control}/test_session_id.py
          ${testPython}/bin/python3 ${./harness-control}/test_harness_tools.py
          ${testPython}/bin/python3 ${./harness-control}/test_harness_lifecycle.py
          ${testPython}/bin/python3 ${./harness-control}/test_harness_tools.py --mutation-check
          ${testPython}/bin/python3 ${./harness-control}/test_harness_lifecycle.py --mutation-check
          touch $out
        '';

        harness-control-package = pkgs.runCommand "harness-control-package" {
          nativeBuildInputs = [ testPython ];
        } ''
          ${testPython}/bin/python3 ${./harness-control}/test_package.py \
            ${lib.getExe harnessControl}
          touch $out
        '';
      };
    };
}
