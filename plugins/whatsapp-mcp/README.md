# WhatsApp MCP

An independent, read-only WhatsApp reader for macOS. Plugin identifier: `whatsapp-mcp`.

The MCP tools find chats, read and search visible messages, and transcribe downloaded audio by message ID using local Whisper. They never send messages or mark them read. Returned text enters the agent conversation.

Run `sh scripts/run.sh --check` from this plugin folder to check database access and installed audio components. Native WhatsApp and file access are required. The launcher prepares uv and Python 3.11+ when needed. Text works without ffmpeg/Whisper. First startup may download runtime components. Use `sh scripts/run.sh --setup --audio` for automatic configuration and optional local audio. See [onboarding](docs/onboarding.md).

- [Full README and installation](https://github.com/jordanrendric/whatsapp-mcp#readme)
- [Portability and configuration](docs/portability.md)
- [Privacy and temporary files](docs/privacy.md)
- [Database schema](docs/schema.md)
- [MIT license](LICENSE)

This project is not affiliated with WhatsApp or Meta. The original icon is not the WhatsApp logo.
