# Maintainer commands

Build implementation lives in [build_tools](../build_tools/README.md).

- `build.py`: forwards to the native Windows, Linux or macOS packaging procedure.
- `prepare_build.py`: inspects/prepares the pinned virtualenv; `--inspect` never
  installs packages or downloads files.
- `security_inventory.py`: dependency/native inventory and official advisory gate.
- `examples/`: regenerates synthetic sample PDFs under the repository's `examples/`.
- `qa/`: screenshot capture, CJK diagnostics and bounded protocol/native fuzzing.

The remaining BAT/shell files forward older startup/build commands to
their maintained entry points. They contain no duplicate packaging logic.
