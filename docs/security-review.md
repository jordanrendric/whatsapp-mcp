# Security review — private preview

Review date: **2026-09-04**. Scope: the code shipped in this repository, its MCP interface, local SQLite access, media-file handling, subprocess lifecycle, dependency lock, and synthetic tests. This is a source review and test report, not an independent external audit or certification.

## Findings addressed

| Priority | Finding | Resolution and evidence |
| --- | --- | --- |
| P2 | A directory could be replaced by a symlink after media-path validation and before audio copying | `Repository.open_audio()` walks directories using anchored descriptors and `O_NOFOLLOW`. The MCP worker consumes the opened descriptor rather than reopening the pathname. Tests cover directory substitution, symlinks, FIFO rejection and descriptor cleanup |
| P2 | Hidden/removed chats were omitted from listings but reachable through search and direct reads | A shared visibility predicate now applies to message readers, search, anchors and audio lookup; orphan messages are excluded. Archived chats remain available |
| P2 | A subprocess child ignoring SIGTERM could outlive a wrapper that exited first | Cleanup now targets the owned process group before reaping its leader. Tests cover cancellation, timeout, normal wrapper exit, resistant children and unrelated sessions |
| P3 | Captured stderr could grow on disk even though only a prefix was returned | Diagnostics are drained from a pipe with a 64 KiB retained prefix. Tests exercise a 32 MiB flood, an endless writer, timeout and bounded memory; no diagnostic log file is created |

The review did not identify an unresolved P0/P1 issue within this scope. That does not establish that the project has no vulnerabilities.

## Checks performed

- **139 synthetic tests passed on both Python 3.11.15 and 3.14.6 on macOS**, covering read-only DB/WAL invariants, schema failure, SQL parameters, visibility, pagination, authorship, anchored media, cancellation, process cleanup, portable discovery, onboarding, verified-download failure cases, CDN lookup and real MCP stdio exchange.
- SQLite writes fail even if the extra query-only/authorizer guards are removed in the synthetic fixture, verifying the underlying read-only URI connection.
- **29 pinned Python dependencies** were checked with `pip-audit` against available advisories: **zero known vulnerabilities reported**, with no dependency skipped at the time of the check. Advisory results are time-dependent.
- Original earlier functional validation exercised the native Apple Silicon installation's read/search path and transcribed a synthetic Portuguese phrase with the installed Whisper model. The security review itself used no real messages or audio.
- The automatic setup was exercised on this Mac: text and audio reported ready, existing binaries/model were reused, and the private configuration was saved with mode 0600. This did not exercise a real fresh Homebrew install or a full model download; those branches use synthetic tests. A headers-only request verified that the pinned default model URL was available and declared the expected size.
- GitHub Actions is configured for synthetic tests on macOS with Python 3.11 and 3.14. Consult the Actions runs for current CI status; CI does not certify a second live WhatsApp installation.

Dependency auditing does not cover the user's external ffmpeg/whisper.cpp builds, model weights, macOS, uv, Codex or future dependency releases. Update those components through trusted sources.

## Important boundaries

1. **Read-only application state, not zero filesystem writes.** The plugin does not modify database contents, send messages or change read/play receipts. SQLite can use or create WAL shared-memory/lock coordination files. Runtime setup creates dependency caches, and audio processing creates temporary files.
2. **No local-malware sandbox.** The operating system, selected account, configuration, trusted executables/model files, MCP client and dependency installation are trusted. Binaries are not run inside an operating-system network or filesystem sandbox.
3. **Context disclosure is intentional.** Requested messages and transcripts are returned to the agent. A malicious message remains untrusted text; the skill's instructions help the agent handle it but do not constitute a universal prompt-injection filter.
4. **Temporary files are not securely erased.** Normal completion, handled errors and cancellation remove working files. Power loss or abrupt termination can leave data in the OS temporary directory. Ordinary deletion is not a forensic-erasure guarantee.
5. **Local synchronization and schema vary.** History may be partial. Schema validation checks required structures; it cannot prove the semantics of every future WhatsApp release. Unsupported layouts must fail visibly instead of attempting writes or migrations.
6. **Visibility is not application authentication.** Hidden/removed flags are excluded; the plugin does not implement or bypass WhatsApp's UI authentication for locked chats. Only grant filesystem access and enable the plugin in an appropriately trusted account/client.
7. **Diagnostics can identify the machine.** Status includes local paths. Remove those paths, participant identifiers and content before sharing reports. Subprocess diagnostics are not returned by the tools.

## Onboarding and CDN extension

The renamed `whatsapp-mcp` adds explicit setup tools and one message-scoped CDN URL lookup. Setup writes only its per-user configuration/runtime/model directories and optional Homebrew-managed packages; it remains read-only toward WhatsApp. It accepts no arbitrary download URL or package name. The uv bootstrap and default model use fixed revisions and SHA-256 checks, with size/time limits and private temporary files. Existing binaries and models remain trusted user-supplied components.

Review also checks ignored curl configuration during bootstrap, symlink ancestors in setup paths, interrupted or corrupted downloads, conflicting account choices, and cancellation. Python 3.11 on macOS uses the native Darwin `waitid` ABI when CPython lacks its binding. The ABI was checked against the SDK and both implementations were exercised with synthetic process trees.

CDN lookup reads optional URL fields only when explicitly called, validates HTTPS WhatsApp hosts, excludes hidden/removed/orphan messages, and never reads media keys. Tests cover missing columns, absent local media, invalid URLs and bounded fields. Returning a stored URL is not a remote-download or decryption guarantee. URLs are sensitive tool output and must not be posted in reports.

## Before public release

Review the project name and technical repository/plugin identifiers against the current [Meta branding guidelines](branding.md); retain the independent-project disclosure. Confirm that current CI passes, review dependency advisories again, and test a fresh Mac installation. Do not change repository visibility until the owner approves the public release.

Report vulnerabilities following [SECURITY.md](../SECURITY.md). Do not submit databases, real audio, transcripts, tokens or private paths as evidence.
