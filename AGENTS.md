# APE PDF Studio — agent instructions

These instructions apply throughout the repository. Follow explicit user
instructions first and preserve changes already present in the workspace.

## Project and structure

APE PDF Studio is a Python/PySide6 desktop application for reading and editing
PDFs. Windows x64 is the verified platform; macOS and Linux require testing on
their respective systems. Document processing is local.

- `src/ape_pdf_studio/app.py`: GUI startup and forwarding to the existing instance.
- `src/ape_pdf_studio/ui/`: windows, tabs, inline controls, rendering and local IPC.
- `src/ape_pdf_studio/core/`: broker, workers, security, PDF operations and history.
- `src/ape_pdf_studio/locales/`: English, Spanish and French catalogs; application
  string keys are in Italian.
- `native/`: AppContainer launcher and Windows File Explorer integration.
- `scripts/`: launchers, runtime preparation, builds, audits, signing and smoke tests.
- `tests/`: PDF engine, Qt interface and security boundary tests.
- `examples/`: the only folder for sample PDFs maintained in the repository.
- `installer/`, `licenses/`, `SOURCE_CODE.md`, `THIRD_PARTY_NOTICES.md`: packaging,
  licensing and reproducible distribution.

Read the affected modules and their tests before making changes. Preserve
existing names and style; avoid reorganizations unrelated to the task.

## Constraints to preserve

### PDF processing and security

- The GUI uses `BrokerEditor`. Do not add PDF parsing, content decoding or native
  MuPDF/PDFium calls to the main process: use the designated workers.
- On Windows, preserve AppContainer, Job Objects, private ACLs, quotas, timeouts
  and artifact checks. If a worker fails, do not fall back to parsing without
  isolation or disable the preventive check.
- Treat files, metadata, annotations and IPC responses as untrusted data.
  Validate schemas, sizes, counts, finite coordinates and paths before use.
  Do not introduce `pickle`, arbitrary commands or imports controlled by messages.
- Open MuPDF documents through `core/pdf_runtime.py::open_static_pdf`, which
  disables JavaScript. Temporary PDFium display and print copies must continue
  to strip disabled actions.
- Embedded JavaScript and local/relative links alone do not prevent reading a
  PDF: they remain disabled and reported. Do not enable opening local files,
  launching programs, opening attachments or automatic form submission.
  Preserve the blocks on other unsupported actions.
- Web/email links go through `allowed_url` and GUI consent: enablement per
  document and confirmation of each destination. Never open them automatically.
- Do not put passwords or secrets in process arguments, logs or the repository.
  Do not upload personal documents to external services.

### Files and edits

- Work on the broker's private copies. Edits must produce validated revisions,
  support undo/redo and preserve atomic saving.
- Do not modify user-provided originals during diagnosis or testing. When
  checking a real document issue, also verify that the original is unchanged.
- Use `user_path` and `same_file` for user-selected paths. Do not apply
  `Path.resolve()` indiscriminately: it can make OneDrive or Personal Vault files
  inaccessible. Preserve the canonicalization and checks required at private
  workspace and IPC boundaries.
- Distinguish opening passwords from printing/editing restrictions alone.
  Preserve the existing read-only policy for encrypted documents.
- Preserve forms, annotations, bookmarks and metadata where the operation
  permits. Edits can invalidate signatures and PDF/A compliance: retain the
  warnings and do not claim validation that is not performed.

### Interface

- Keep form filling and text editing directly on the page. Saving, exporting
  and closing must handle drafts without losing them.
- Pen motion must remain local and fluid, without synchronous RPC calls for
  each mouse event. Saved strokes remain removable Ink annotations.
- Maintain one window per user/session: new document opens are forwarded as
  tabs; an already open document is selected without losing edits.
  The CLI and PDF workers remain separate processes.
- Reuse vector icons and theme components. Verify visual changes with Qt and
  account for resizing and HiDPI displays.
- Use `tr("testo italiano")` for new application strings and update all three
  catalogs. Do not translate PDF contents, filenames or user-entered text.

## Environment and verification

Use `.venv-build/Scripts/python.exe` for Windows development and testing. Exact
release dependency and interpreter versions are in `requirements-release.txt`;
native build tool versions are in `requirements-native-build.txt`.
Do not install dependencies into the global Python environment.

For a new environment, follow the README. When the Windows environment needs
preparing or updating, use this sequence:

```powershell
.\scripts\build_patched_mupdf.ps1 -PythonExe .\.venv-build\Scripts\python.exe
.\.venv-build\Scripts\python.exe -m pip install --find-links .dev-deps/patched-wheels -r requirements-release.txt -e ".[dev]"
.\scripts\build_windows_sandbox.ps1
.\.venv-build\Scripts\python.exe scripts/prepare_sandbox_runtime.py
```

Do not replace the corrected MuPDF build with an upstream wheel without also
checking its native version. Do not rebuild an already suitable environment.

Run the tests relevant to the change first. Example from the repository root:

```powershell
$agentTestTemp = 'build/pytest-agent-' + [guid]::NewGuid().ToString('N')
.\.venv-build\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp $agentTestTemp tests/test_editor.py
```

- Use a separate temporary directory for each run, especially for concurrent
  tests. Do not delete temporary files belonging to other processes.
- GUI tests use Qt `offscreen` and fixtures with temporary settings. Also isolate
  the `SingleInstance` channel so test PDFs are not forwarded to the user's app.
  Mock network responses in tests that should not contact real services.
- Windows tests require a working AppContainer launcher and runtime. Distinguish
  restrictions of the external execution environment from code defects; do not
  work around them by disabling the sandbox.
- For security boundary changes, also run `scripts/test_windows_sandbox.py` and
  the affected protocol tests. The complete regression, fuzzing and audit
  sequence is in `.github/workflows/security.yml`.
- Add tests for new behavior, bugs and concrete risks. Use proportionate checks
  for documentation-only changes or minor visual adjustments.
- If tests find stale editable-install metadata, compare it with `pyproject.toml`
  and regenerate local metadata; do not change the product version to pass tests.

## Dependencies, builds and delivery

- Do not bump the application version for an ordinary change. When a task
  requires a version change, align `pyproject.toml`, `__version__` and the other
  release references already maintained by the scripts.
- For security updates, align project constraints, release pins, startup checks
  and inventory gates. Check official advisories and rerun the audit: do not
  ignore a CVE merely to make the pipeline pass.
- Use `scripts/build_installer.ps1` for Windows packaging and retain the smoke
  tests. Development builds may be unsigned; `-Release` requires a certificate
  and the prescribed checks. Do not store keys or passwords in the repository.
- Keep dependency licenses, notices and sources consistent with the distributed
  components. Do not replace native components by manually editing binaries.
- Put generated artifacts in `build/`, `dist/` or `.dev-deps/`, following the
  existing scripts. Do not include virtual environments, caches or personal files
  in changes.
- Complete the authorized work and relevant checks autonomously. Do not add
  confirmation requests for routine, reversible edits.
- When reporting completion, describe the result, checks actually performed
  and any limitations. Distinguish source changes, rebuilt installers and GitHub
  pipeline runs actually performed: do not present them as equivalent.
