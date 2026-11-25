# Contributing

Teratoma is an experimental replication lab; its engine is named AegisDB. Useful
contributions make a behavior easier to reproduce, explain, test, or compare.
Start with a small change and state which experiment or user problem it improves.

## First local check

Use Python 3.11+ and run from the repository root. No Python package installation
is needed for these checks:

```sh
python -B scripts/test_portable.py
python -B -m src.main --seeds 1 --ticks 1000
```

For the visual demo, see [the demo guide](docs/DEMO.md). Native tests need the
compiled artifacts and a POSIX host; follow [the native guide](docs/NATIVE.md).
Lean changes need the pinned toolchain and [proof checks](formal/unbounded/README.md).

## Find a focused contribution

The [roadmap](docs/ROADMAP.md) lists current gaps without promising delivery dates.
Examples of useful small changes include:

- A minimal seed or request sequence that exposes a consensus/recovery bug.
- A regression test for an unchecked framing, storage, or query edge case.
- A clearer explanation of one replay scenario, linked to the code that causes it.
- A documented native build result on a specific OS, architecture, and Python version.
- A reproducible counterexample or a clarification of a formal-model assumption.

For changes to protocol semantics, message formats, or storage formats, open an
issue with the proposed behavior and compatibility impact before a large patch.
Do not broaden a formal claim without matching statements and verification.

## Report a bug

Include the commit, OS/architecture, Python/compiler or Lean versions, exact
command, expected result, and actual result. For simulator failures, include the
seed, tick budgets, and a minimal trace. For native failures, include process
exit statuses and whether the data directory was fresh or restarted.

Remove private data, credentials, and machine-specific secrets from shared logs.
For a sensitive finding, follow [SECURITY.md](SECURITY.md).

## Prepare a pull request

1. Keep the change focused and explain the observed problem.
2. Add or update a regression check for changed behavior. For a bug, show the
   failure on the old version and the pass after the fix when practical.
3. Run the relevant checks and record their commands, environment, and outcomes.
4. Keep simulator, native-runtime, bounded-model, and Lean results distinct.
5. Document any skipped checks or remaining uncertainty. Preserve material
   failures even when a later attempt passes.

Do not commit generated binaries, temporary data, or raw `artifacts/` directories.
The replay assets under `docs/lab/` are intentional documentation assets; regenerate
them with `python -B -m src.demo --output docs/lab` when the demo changes.
Native builds overwrite the tracked historical `native/build_report.json`; inspect
your diff before including that file. Attach new evidence separately and label its
revision and platform.

Use the pull-request template. Current CI results apply to their tested commit
and job scope; they do not establish production readiness or executable refinement.

## License and attribution

This project uses [Apache License 2.0](LICENSE). Submit work you have the right to
contribute, retain relevant notices, and identify external sources or adapted
material. Do not copy code whose license is incompatible with the contribution.
