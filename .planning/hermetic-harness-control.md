# Package harness-control as a pinned Nix application

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds. Maintain this document in accordance with `.planning/PLANS.md`.

## Purpose / Big Picture

After this change, every active Linux workstation can run the `harness-control` MCP server from a dedicated Nix profile without installing `uv`, resolving Python dependencies at agent startup, or importing executable code from a mutable checkout. The operator can run `~/.local/bin/harness-control`; Claude and Codex use that same stable path; Nix supplies a pinned Python and MCP dependency through its content-addressed store and can roll the profile back independently of Home Manager.

The active deployment scope is framework0, tower0, and splinter1. Each is x86_64 Linux with Nix. Drynwyn and splinter0 are outside this runtime rollout.

## Progress

- [x] (2026-10-15 01:00Z) Confirmed the active hosts have Nix and selected a dedicated profile instead of Home Manager, the general user profile, PEX, or `nix run` at every startup.
- [x] (2026-10-15 01:00Z) Created isolated `feat/hermetic-harness-control` worktrees in agent-skills and dotfiles and started the cross-repository tracker issue.
- [ ] Add the canonical harness-control runtime, offline tests, and locked Nix package to agent-skills.
- [ ] Prove the package starts without `uv`, ambient Python modules, a mutable checkout, or network access.
- [ ] Land the agent-skills PR, then pin that merged commit in dotfiles.
- [ ] Add the dedicated-profile installer and change Claude/Codex registration to the stable packaged command.
- [ ] Remove the superseded dotfiles runtime copy, update docs/tests, and prove install, upgrade, refusal, and rollback behavior.
- [ ] Reach council-review fixpoint, merge both PRs normally, and deploy the exact merged revisions to framework0, tower0, and splinter1.

## Surprises & Discoveries

- Observation: framework0 and tower0 expose system Python 3.12, while splinter1 exposes Python 3.14.
  Evidence: the 2026-10-15 fleet probe printed `/usr/bin/python3` versions 3.12.3, 3.12.3, and 3.14.4 respectively. An ambient interpreter cannot provide one hermetic dependency closure across the fleet.

- Observation: the server currently spans two mutable repositories.
  Evidence: dotfiles contains `mcp/claude-introspect/server.py` and its lifecycle modules, while `_skills_native.py` loads identity readers and `tmux-message.sh` from the installed agent-skills checkout. A package must pin both halves together.

## Decision Log

- Decision: Use a normal Nix package, installed once, rather than PEX/scie or `nix bundle`.
  Rationale: Ted prefers Nix and does not want another embedded CPython copy. Nix keeps the interpreter as a shared store closure and deduplicates exact paths across packages.
  Date/Author: 2026-10-15, Ted and Codex.

- Decision: Install into `~/.local/state/nix/profiles/harness-control` and expose `~/.local/bin/harness-control`.
  Rationale: A dedicated profile has atomic generations and rollback without coupling the service to tower0's stale Home Manager checkout or unrelated user packages. The stable link gives MCP clients one registration path.
  Date/Author: 2026-10-15, Ted and Codex.

- Decision: agent-skills owns the runtime and Nix package; dotfiles owns checkout resolution, profile installation, and client registration.
  Rationale: agent-skills already owns the canonical identity readers, shared configuration loader, and messaging transport used by the service. Dotfiles already owns machine installation and Claude/Codex configuration.
  Date/Author: 2026-10-15, Codex.

- Decision: Treat the host tmux client/server as an explicit platform capability rather than bundling a second tmux.
  Rationale: tmux clients must speak the running server's protocol. The package pins its Python code and command wrappers, while the configured tmux command deliberately addresses the host server.
  Date/Author: 2026-10-15, Codex.

## Outcomes & Retrospective

Implementation is in progress. No runtime or fleet installation has changed yet.

## Context and Orientation

The agent-skills repository currently contains shared operational code under `skills/`. Three canonical native readers live in `skills/_shared/native/`. The guarded tmux transport lives in `skills/spawn-agent/scripts/`. Dotfiles currently contains the MCP server under `mcp/claude-introspect/`; its `_skills_native.py` adapter imports the canonical readers and locates the transport through an `AGENT_SKILLS_ROOT` environment variable.

An MCP server is a long-running child process that exchanges JSON-RPC messages with Claude or Codex over standard input and output. The current registration starts `uv run --script <dotfiles>/mcp/claude-introspect/server.py`. On a host without `uv`, installers silently skip registration. On a cold host, `uv` can also need to construct a dependency environment before the server starts.

A Nix profile is a stable symlink to one immutable generation of packages in `/nix/store`. Installing into a dedicated profile changes that symlink atomically and preserves older generations for rollback. The package built here will contain a wrapper, a pinned Python environment with `mcp` below major version 2, the harness-control source, and immutable references to the exact agent-skills source tree used at build time.

## Plan of Work

First, copy the current harness-control runtime and its self-contained tests into `harness-control/` in agent-skills. Retain the existing adapter during migration, but ensure the packaged wrapper sets `AGENT_SKILLS_ROOT` to the immutable `skills/` tree captured by the same flake source. This keeps the current reviewed reader and transport boundaries while removing mutable-checkout lookup from production.

Add `flake.nix` and its generated `flake.lock`. The flake supports `x86_64-linux`, uses a pinned nixpkgs input, constructs a Python 3.12 environment containing `mcp` below major version 2, and exposes `packages.x86_64-linux.harness-control`, the default package, an app, and checks. The wrapper must run isolated from `PYTHONPATH` and user site packages, set `PYTHONDONTWRITEBYTECODE`, point `AGENT_SKILLS_ROOT` at the captured source, and execute the packaged `server.py`. It must leave the ordinary runtime inputs HOME, XDG configuration, session state, `/proc`, and the configured host tmux command available.

Move the runtime-focused tests from dotfiles into agent-skills and adapt only repository-relative paths. Add an artifact smoke test that uses the built executable and the MCP client to initialize the server and list tools under a private HOME. Prove the executable works with `uv` absent, a hostile `PYTHONPATH`, and the source checkout temporarily unavailable from the test's point of view. Run the existing identity, session-binding, Codex status, messaging, lifecycle, and mutation tests that cover the bundled boundary.

After the agent-skills PR is reviewed and merged, update dotfiles `.agent-skills.ref` to the merged commit. Add `scripts/install/install-harness-control`. It resolves the existing clean pinned checkout, verifies Nix exists, installs `path:<checkout>#harness-control` into the dedicated profile, verifies the resulting executable and its MCP handshake, then atomically manages `~/.local/bin/harness-control`. A failed build, install, or smoke test must leave the prior profile generation and stable link usable.

Change `scripts/install/install-claude-introspect` and the Codex registration in `scripts/install/install-codex-config` to register the stable executable with no `uv run --script` arguments. Preserve the current ownership checks for custom registrations. Add private-HOME installer tests for first install, idempotence, upgrade, failure preservation, absent Nix, foreign stable links, stale old registration migration, and exact registration output. Remove the old dotfiles runtime only after all tests consume the packaged source.

## Concrete Steps

In `/home/tedks/Projects/agent-skills/hermetic-harness-control`, add the runtime and flake, add all new paths to Git so flake source filtering includes them, then run:

    nix flake check
    nix build .#harness-control
    ./result/bin/harness-control

The bare executable blocks waiting for MCP input; the automated smoke test will instead initialize it and list tools, then exit cleanly.

Run the Python and shell tests named by the package README and this plan. Run mutation checks where the migrated tests provide them. Commit, push, create a draft PR early, run council review to fixpoint, and merge normally after exact-head local gates pass.

In `/home/tedks/Projects/dotfiles/hermetic-harness-control`, update the pin only after the first PR merges. Implement and test the installer and registration changes, then repeat the draft PR, exact-head gates, council fixpoint, and normal merge workflow.

Finally, on each active host, fast-forward clean dotfiles and agent-skills master worktrees, run the narrow harness-control and Claude/Codex installers, restart no live harness automatically, and verify both clients report the stable executable registration. Existing sessions load the new registration only after their next normal restart.

## Validation and Acceptance

The agent-skills change is accepted when `nix flake check` and the runtime test suites pass, the built executable completes a real MCP initialize/list-tools exchange, and that exchange still passes with `uv` absent and a hostile `PYTHONPATH`. `nix-store --query --references` or `nix path-info -r` must show the pinned Python/MCP closure, and the wrapper must not reference the mutable checkout path.

The dotfiles change is accepted when isolated installer tests prove first install, no-op reinstall, failed-upgrade preservation, custom-registration refusal, legacy `uv run --script` migration, and stable-path registration. The live fleet is accepted when framework0, tower0, and splinter1 have the same agent-skills Git commit, the dedicated profile resolves to the expected package output, `~/.local/bin/harness-control` is executable, and Claude/Codex list the same stable command without `uv` arguments.

The final runtime should start without network access. Startup duration is measured from process creation through MCP initialization on a cold process and a second warm process; unexpected delay must be diagnosed before rollout rather than hidden by increasing client timeouts.

## Idempotence and Recovery

Nix profile installation is repeatable. The installer must build and smoke-test the candidate before publishing the stable link. If installation fails before profile advancement, no live state changes. If the new generation installs but fails its postcondition, run `nix profile rollback --profile ~/.local/state/nix/profiles/harness-control` and restore the prior stable link target. Dotfiles registration changes preserve unknown custom policy and refuse corrupted client configuration as they do today.

Worktree cleanup happens only after both PRs merge and fleet verification succeeds. Never delete the old runtime or an old profile generation merely to make a retry pass.

## Artifacts and Notes

The cross-repository tracker is `harness-codex-installer-followups` in dotfiles. The original public agent-skills repository is `git@github.com:tedks/agent-skills.git`; dotfiles selects its production checkout through `.agent-skills.ref`.

## Interfaces and Dependencies

The agent-skills flake must expose `packages.x86_64-linux.harness-control`, `packages.x86_64-linux.default`, and `apps.x86_64-linux.harness-control`. The package installs `bin/harness-control`. Its only ambient executable dependency is the configured tmux client used to reach the already-running host tmux server; its Python interpreter, MCP library, Bash and core command dependencies come from Nix.

Dotfiles `scripts/install/install-harness-control` must accept no destructive default. It operates on the fixed dedicated profile, resolves agent-skills through the existing validated reference, and prints a distinct installed, unchanged, skipped, or failed result. Claude and Codex registration must have command `~/.local/bin/harness-control` expanded to an absolute path and an empty argument list.

Revision note, 2026-10-15: initial plan written after the implementation ruling and fleet/Nix preflight.
