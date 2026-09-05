# Security policy

WhatsApp MCP (`whatsapp-mcp`) is experimental software. Security work currently targets the latest development revision; there is no promised support window for older versions or response-time SLA.

## Report a vulnerability privately

Use [GitHub's private vulnerability reporting form](https://github.com/jordanrendric/whatsapp-mcp/security/advisories/new) **when private reporting is available for the repository**. The repository is initially private, and that form may be unavailable until the repository is public and a maintainer enables private vulnerability reporting. If the form is unavailable, ask a maintainer with whom you already have contact for a private reporting channel; do not publish exploit details in a public issue. No security email address is designated by this project.

Include the affected revision, operating system and dependency versions, expected and actual behavior, and a minimal reproduction using synthetic data. Never upload a WhatsApp database, journal, attachment, message transcript, access token, or unsanitized status output. Coordinate disclosure and fixes with the maintainer before sharing a report publicly.

## Security boundaries

- The MCP transport is local stdio. The plugin exposes no HTTP listener, sending tool, mark-read operation, arbitrary SQL endpoint, or tool parameter for arbitrary files or URLs.
- Database connections use SQLite `mode=ro`, `query_only`, `trusted_schema=OFF`, and a restrictive authorizer. Queries use parameters, bounded pages, and a progress deadline. Schema mismatches fail explicitly. These controls prevent the plugin's database operations from modifying messages; they are not an operating-system sandbox.
- Audio is selected through a message ID and must resolve to downloaded media under the configured media root. The source is checked and copied into a private temporary directory; ffmpeg and whisper.cpp run without a shell. Conversion restricts input protocols and formats. Limits, timeout handling, and cancellation reduce resource use.
- Transcription uses locally installed whisper.cpp and model files. No remote transcription API, telemetry client, or automatic message synchronization is implemented. Explicit audio setup can install missing Homebrew binaries and download a pinned, SHA-256-verified base model. Bootstrap can install a pinned, verified uv binary; uv installs locked Python dependencies. These setup operations use the network. Tool results enter the agent’s context.
- The skill and server instructions classify messages and transcripts as untrusted data. This reduces prompt-injection risk but does not provide a technical guarantee about the actions a separate agent or its other tools may take.

- The CDN tool returns only a stored HTTPS URL on the WhatsApp CDN allowlist from a visible message. It never follows URLs, returns encryption keys, downloads files, or renews links. The URL can grant access to encrypted media and must be treated as sensitive.
- `whatsapp_setup` is explicitly annotated as a local configuration/install operation, not read-only. It does not modify WhatsApp. Configuration stays in a private per-user JSON file; environment variables override it. No arbitrary download URL or package name is accepted from a tool caller.

## Trust assumptions and remaining risks

The user trusts the MCP client, plugin files, Python dependency environment, local configuration, binaries found on `PATH` or explicitly configured, and model files. An executable or environment selected by the user runs with the initiating application's permissions. Executable discovery checks availability, not authenticity or signatures. Keep Python, ffmpeg, whisper.cpp, and their dependencies current and obtain them from sources you trust.

Incoming attachments are untrusted. Format restrictions are defense in depth, not protection against every decoder vulnerability. The subprocesses are not isolated in a network or filesystem sandbox. A malicious local process with the same user privileges, compromised binary, or malicious dependency is outside the protections provided by the read-only query interface.

The plugin can read conversations accessible to its process; it does not provide a per-contact allowlist or a separate authentication layer. macOS permissions are controlled by the user. The plugin does not bypass permission denials. Read-only access does not eliminate disclosure risk: returned messages, names, identifiers, timestamps, attachment metadata, and transcripts may be sensitive.

Temporary audio is removed during normal completion, handled failures, and cancellation. Abrupt termination, an operating-system crash, backups, or filesystem behavior can retain traces; cleanup is not secure erasure. The plugin does not control Codex retention, model-provider processing, operating-system logs, or user-created exports. See the [privacy guide](plugins/whatsapp-mcp/docs/privacy.md).

Before public release, review this policy, enable a working private reporting channel, review dependency updates, and verify that the package and repository history contain no personal data or machine-specific configuration. Automated tests and code review are evidence of particular checks, not a certification or a complete security audit.
