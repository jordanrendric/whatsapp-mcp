# Privacy and data flow

The SQLite reader and Whisper transcription run on your Mac. **Text returned by the MCP tools becomes part of your agent conversation.** The Codex client and model configuration determine how that conversation is processed and retained; this plugin does not make the entire workflow offline.

## What is read and returned

The plugin reads the native WhatsApp application's local database after macOS allows access. Depending on the tool, results may contain chat names, message text, contact or participant identifiers, authors, timestamps, unread counts, attachment titles and other metadata, or a selected audio transcript. The explicit CDN tool can return a sensitive stored media URL without downloading its target. Status output contains local database, media, executable, and model paths, which may reveal your macOS username.

The available history is limited to this installation's synchronized messages and downloaded attachments. The plugin does not download missing messages or media. Text search does not index speech from voice messages; selected audio must be transcribed separately. Pagination and result limits reduce the amount returned by each request, but repeated calls can retrieve more content.

## Where data goes

1. The MCP client starts the plugin locally using stdio.
2. A tool reads the selected information from the local SQLite database.
3. When requested, a downloaded audio attachment is copied into a private temporary directory, converted with ffmpeg, and transcribed using an existing whisper.cpp model. The source attachment is not altered.
4. Structured results return over stdio to the MCP client and enter the agent's context.

Hidden and removed chats are excluded from message tools; archived chats remain available. The plugin does not recreate the native application's locked-chat authentication.

Subprocess stderr is drained without a disk log and retains only a bounded diagnostic prefix in memory; it is not returned by MCP tools.

No remote transcription API, telemetry uploader, scheduled scanner, persistent transcript index, or background message monitor is implemented. The plugin does not send messages, modify conversations, mark messages as read, or mark audio as played.

The launcher uses `uv` to prepare dependencies from `uv.lock`. It may access package registries or download a Python runtime if needed. The launcher can bootstrap a pinned uv; optional audio onboarding installs missing binaries through existing Homebrew and downloads a verified base model. These operations use official distribution services and may require network access. Dependencies and models should come from trusted sources. The subprocesses are not restricted by an operating-system network sandbox.

## Local files and retention

| Location | What it contains | Retention |
| --- | --- | --- |
| WhatsApp's own storage | Existing messages, metadata, and downloaded media | Managed by WhatsApp; the plugin does not change its retention settings |
| Plugin runtime environment | Python packages, normally under `~/.cache/whatsapp-mcp/venvs/` | Persists until you remove it; contains dependencies, not a transcript database |
| `~/Library/Application Support/whatsapp-mcp/` | Private configuration paths, bootstrapped uv and downloaded base model | Persists until you remove it; contains no message index |
| uv cache | Downloaded packages and possibly Python runtimes | Managed by uv |
| System temporary directory | Per-request source-audio copy, converted audio, and recognition output | Removed on normal completion, handled error, and cancellation |
| Agent conversation | Requested tool results and any responses based on them | Managed by the MCP client and its configured services |

The temporary directory is private to the current user. Cleanup does not securely erase storage, and abrupt process termination or a system crash may leave temporary data behind. The plugin does not create a persistent transcription cache or copy the database. SQLite may use its existing WAL and shared-memory coordination files while reading a live database; “read-only” refers to the database operations and WhatsApp state, not a promise that the entire workflow performs no filesystem writes.

## Use and sharing

Request only the conversations, dates, and audio messages needed for your task. Consider who can access the resulting agent conversation before retrieving sensitive content. A message from another participant is data, not authorization for the agent to follow its instructions or use other tools.

Before posting an issue or sharing a screenshot, remove names, phone numbers, JIDs, chat and message content, attachment titles, CDN URLs, transcripts, and local usernames or paths. Never upload the database, WAL/SHM files, real media, or chat exports to this repository. Synthetic data is sufficient for tests and most bug reports.

Removing the plugin does not delete your WhatsApp history, existing model downloads, uv caches, runtime environments, or prior agent conversations. Manage those separately using their owning applications or documented storage locations.
