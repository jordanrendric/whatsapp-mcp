# Contributing

WhatsApp MCP (`whatsapp-mcp`) is an experimental, read-only Codex plugin for the native WhatsApp application on macOS. Contributions should preserve that scope and make its privacy and compatibility limits explicit.

## Development setup

Use macOS, Python 3.11 or newer, and [uv](https://docs.astral.sh/uv/). Clone the repository and run from its root:

```sh
cd plugins/whatsapp-mcp
uv sync --locked --no-dev
PYTHONPATH=src uv run --locked --no-dev --no-env-file python -m unittest discover -s tests -v
```

The tests create synthetic databases and media in temporary directories. They do not require WhatsApp, a signed-in account, ffmpeg, whisper.cpp, a model download, or access to anyone's conversations. Protocol tests start the local MCP subprocess against a synthetic database. Audio tests simulate the external decoder and recognizer; they do not prove transcription quality or hardware compatibility.

The CI workflow runs the synthetic suite on macOS with Python 3.11 and 3.14. It pins its GitHub Actions by commit, uses read-only repository permissions, and installs Python dependencies from `uv.lock`. Updating dependencies is intentional work: update the lockfile, review changes, and rerun the tests together.

## Scope and review expectations

- Keep access to WhatsApp read-only. Sending, mark-read operations, database changes, arbitrary SQL, arbitrary attachment paths, and remote transcription are outside the current interface. The explicit `whatsapp_setup` operation may configure the plugin and install its documented runtime/audio dependencies; preserve its non-read-only tool annotation and keep those writes separate from WhatsApp data.
- Preserve bounded queries, stable pagination, explicit schema validation, path containment, subprocess cancellation, and private temporary-file cleanup.
- Treat message text, contact names, attachment titles, and audio transcripts as untrusted content, never as instructions to the agent.
- Add meaningful synthetic regression tests for behavioral fixes. Do not make the test suite discover or connect to a contributor's live WhatsApp database.
- Document any new configuration variable, storage location, external process, network use, or dependency. Do not add personal absolute paths or credentials to source files or examples.
- Use generic examples such as `~/models/ggml-base.bin`; keep names, phone numbers, JIDs, transcripts, screenshots, and chat exports out of commits, issues, logs, and build artifacts.

If a WhatsApp update changes the schema, describe missing table or column names and create the smallest synthetic fixture that reproduces the issue. Do not attach `ChatStorage.sqlite`, its WAL/SHM files, or media. Consult the [schema notes](plugins/whatsapp-mcp/docs/schema.md), [portability notes](plugins/whatsapp-mcp/docs/portability.md), and [privacy guide](plugins/whatsapp-mcp/docs/privacy.md).

## Reporting issues and proposing changes

For ordinary bugs, use the [issue tracker](https://github.com/jordanrendric/whatsapp-mcp/issues) when you have repository access. Include the plugin version, macOS version, CPU architecture, Python version, dependency versions relevant to the problem, the tool that failed, and a sanitized error. Before sharing status output, remove local usernames, file paths, identifiers, and content.

For security-sensitive findings, follow [SECURITY.md](SECURITY.md) instead of opening a public issue. There is no requirement to access real messages to contribute a fix.

Describe the user-visible problem, the resulting behavior, and the tests performed in each pull request. Changes to the read-only boundary, data handling, supported storage layouts, dependencies, or release packaging need a clear explanation of the implications.
