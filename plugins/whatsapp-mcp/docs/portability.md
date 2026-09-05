# Portability and compatibility

WhatsApp MCP (`whatsapp-mcp`) is an experimental plugin for **native WhatsApp on macOS**. It does not support WhatsApp Web, browser profiles, Windows stores, Android backups, or iOS backups. The native application's SQLite schema is private implementation detail, not a stable or officially supported integration API.

## Supported and unverified configurations

| Component | Requirement or current evidence |
| --- | --- |
| Operating system | macOS; the production launcher and MCP server reject other operating systems |
| WhatsApp | Native app with locally synchronized history and the tables/columns described in [schema.md](schema.md) |
| CPU | The initial local validation used Apple Silicon. Intel hardware and its transcription performance have not been validated on a real installation |
| Python | 3.11 or newer; synthetic CI covers 3.11 and 3.14 |
| Dependency manager | Existing `uv` or automatically bootstrapped pinned uv under the plugin’s own per-user application directory |
| Text tools | No ffmpeg, Whisper, or model required |
| Audio tools | Executable `ffmpeg` and `whisper-cli`, plus a compatible readable GGML speech model already installed |
| MCP client | A compatible Codex build that loads the plugin manifest and resolves its relative MCP working directory |

CI uses synthetic data on a hosted macOS runner. It is not a second live WhatsApp compatibility test, an Intel certification, or a transcription-quality evaluation. Discovery includes Business and legacy paths, but their current real-world layouts have not been validated. A future WhatsApp update can require a schema adapter change even on an otherwise supported Mac.

## Paths are discovered on the current machine

The launcher resolves the plugin directory from its own location, so it can run from a clone, an installed plugin cache, or a path containing spaces. It does not depend on the author's checkout path or username.

Database discovery uses the current user's home directory and checks:

```text
~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite
~/Library/Group Containers/group.net.whatsapp.WhatsAppSMB.shared/ChatStorage.sqlite
~/Library/Containers/net.whatsapp.WhatsApp/Data/Library/Application Support/WhatsApp/ChatStorage.sqlite
```

If more than one database is present, choose explicitly using `WHATSAPP_MCP_DB_PATH`. Automatic discovery does not guess which account you intended. By default, the media root is `Message` beside the chosen database. Database IDs are local to that file; do not reuse chat or message IDs between machines, accounts, or restored databases.

Onboarding saves detected paths in `~/Library/Application Support/whatsapp-mcp/config.json`. Environment variables take precedence. Configuration is machine-specific and belongs outside the repository:

| Variable | Behavior |
| --- | --- |
| `WHATSAPP_MCP_DB_PATH` | Explicit SQLite path; `~` is expanded |
| `WHATSAPP_MCP_MEDIA_ROOT` | Explicit allowed media directory; otherwise `Message` beside the database |
| `WHATSAPP_MCP_WHISPER_MODEL` | Absolute path to an existing readable nonempty GGML model; `~` is expanded; an invalid override does not silently select another model |
| `WHATSAPP_MCP_WHISPER_BIN` | Absolute executable path to `whisper-cli`; `~` is expanded; an invalid override does not fall back to `PATH` |
| `WHATSAPP_MCP_FFMPEG_BIN` | Same executable rules for `ffmpeg` |
| `WHATSAPP_MCP_VENV` | Optional Python environment location for the launcher; use an absolute path |
| `XDG_CACHE_HOME` | When no environment override is set, changes the base cache directory; otherwise `$HOME/.cache` is used |

Prefer absolute database and media paths for deterministic behavior when the MCP client changes its working directory. Do not commit real values into `.mcp.json`. The launcher does not load a project `.env` file (`--no-env-file`); configure the MCP process's environment instead. The way a graphical macOS app inherits shell environment variables can differ from a terminal, so use `whatsapp_status` in the intended client to verify the result.

## Audio discovery and hardware differences

Executables are searched on `PATH`, then in both common Homebrew locations: `/opt/homebrew/bin` for many Apple Silicon installations and `/usr/local/bin` for many Intel installations. No Homebrew installation is required when valid executable overrides are provided.

Model discovery checks existing files in these locations:

```text
~/.claude-video-vision/models
~/.cache/whisper.cpp
~/.cache/whisper
~/.local/share/whisper.cpp/models
~/Library/Caches/whisper.cpp
~/Library/Application Support/whisper.cpp/models
~/whisper.cpp/models
/opt/homebrew/share/whisper-cpp/models
/opt/homebrew/share/whisper-cpp
/usr/local/share/whisper-cpp/models
/usr/local/share/whisper-cpp
```

The optional `claude-video-vision` directory is only a reuse opportunity: this plugin does not require or install that plugin. Discovery recognizes expected `ggml-*` speech-model names, excludes Silero VAD and test weights from the speech-model selection, and prefers multilingual models and `large-v3-turbo` when present. Existing models can consume substantial memory; choose an explicit smaller compatible model when appropriate for your hardware.

If `ggml-silero-v5.1.2.bin` is already beside the chosen speech model, the recognizer uses it for voice activity detection. It is optional. `whatsapp_setup(enable_audio=True)` can download a pinned base model when no usable model is found; transcription itself never downloads models.

The recognizer normally uses the acceleration available in the installed whisper.cpp build. A recognized Metal initialization failure triggers one CPU-only retry. This fallback does not guarantee sufficient memory, acceptable latency, or support for every Intel Mac or whisper.cpp version. Incompatible binaries or model formats produce an error. Text tools remain usable when audio dependencies are unavailable.

## Permissions, storage, and troubleshooting

macOS must allow the application launching the MCP server to read the selected WhatsApp container. If access is denied, review that application's file-access settings. The plugin does not change permissions, bypass access controls, or copy the database as a workaround.

The runtime environment normally lives under `~/.cache/whatsapp-mcp/venvs/<lock-hash>` and is keyed by `uv.lock`; it is not stored in the distributable plugin or WhatsApp directory. `uv` may download dependencies or Python when the required runtime is missing. Offline operation requires these components to have been installed and cached beforehand.

Use `whatsapp_status` or `sh scripts/run.sh --check` to diagnose path and dependency discovery. That check reads the database schema but returns no messages and does not execute ffmpeg or Whisper. A successful status reports discovery and access, not a real transcription test. Sanitize its paths before sharing the output.

Common compatibility failures include incomplete device synchronization, media that has not been downloaded, multiple detected accounts, a new WhatsApp schema, macOS permission denial, an environment not inherited by Codex, or an incompatible Whisper binary/model pair. Preserve these explicit errors; do not modify the source database to make an unsupported layout appear compatible.
