"""Private, machine-local configuration; never stored inside the plugin checkout."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile


MAX_CONFIG_BYTES = 32 * 1024
ENV_KEYS = {
    "WHATSAPP_MCP_DB_PATH": "db_path",
    "WHATSAPP_MCP_MEDIA_ROOT": "media_root",
    "WHATSAPP_MCP_WHISPER_MODEL": "whisper_model",
    "WHATSAPP_MCP_WHISPER_BIN": "whisper_bin",
    "WHATSAPP_MCP_FFMPEG_BIN": "ffmpeg_bin",
}


class ConfigError(Exception):
    """A configuration error that never embeds the configuration contents."""


def config_directory() -> Path:
    home = Path.home()
    if not home.is_absolute() or home == Path("/") or ".." in home.parts:
        raise ConfigError("A non-root absolute home directory is required for local configuration.")
    return home / "Library/Application Support/whatsapp-mcp"


def config_path() -> Path:
    return config_directory() / "config.json"


def _validate(values: object) -> dict[str, str]:
    if not isinstance(values, dict) or not set(values) <= set(ENV_KEYS.values()):
        raise ConfigError("The local configuration has unsupported settings.")
    validated = {}
    for key, value in values.items():
        if not isinstance(value, str) or not value or len(value) > 4096 or "\x00" in value:
            raise ConfigError("Local configuration settings must be absolute file paths.")
        if not Path(value).is_absolute():
            raise ConfigError("Local configuration settings must be absolute file paths.")
        validated[key] = value
    return validated


def load_config() -> dict[str, str]:
    """Read bounded JSON without following a config-file or app-directory symlink."""
    descriptor = None
    try:
        if not _prepare_private_path(config_directory(), create=False):
            return {}
        descriptor = os.open(config_path(), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_CONFIG_BYTES:
            raise ConfigError("The local configuration is not a supported regular JSON file.")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            raw = stream.read(MAX_CONFIG_BYTES + 1)
        if len(raw) > MAX_CONFIG_BYTES:
            raise ConfigError("The local configuration exceeds its size limit.")
        document = json.loads(raw)
        if not isinstance(document, dict) or type(document.get("version")) is not int or document["version"] != 1:
            raise ConfigError("The local configuration version is unsupported.")
        return _validate({key: value for key, value in document.items() if key != "version"})
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError, ValueError):
        raise ConfigError("Cannot read the local configuration. Check its format and file permissions.") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def setting(env_name: str) -> str | None:
    """An explicit environment value takes precedence over saved machine settings."""
    if env_name not in ENV_KEYS:
        raise ConfigError("An unsupported configuration setting was requested.")
    if env_name in os.environ:
        return os.environ[env_name]
    return load_config().get(ENV_KEYS[env_name])


def _prepare_private_path(directory: Path, *, create: bool) -> bool:
    """Check every ancestor before reading or creating project-owned directories."""
    home = Path.home()
    application = config_directory()
    if not directory.is_absolute() or ".." in directory.parts or not directory.is_relative_to(application):
        raise ConfigError("Local setup directories must stay inside the private application directory.")
    try:
        # HOME can itself contain a redirected ancestor. Do not normalize it away.
        for ancestor in (home, *home.parents):
            metadata = ancestor.lstat()
            if not stat.S_ISDIR(metadata.st_mode):
                raise ConfigError("The home directory path must contain only regular directories, without symbolic links.")
        current = home
        chain = [home]
        for part in directory.relative_to(home).parts:
            current = current / part
            chain.append(current)
        for current in chain:
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                if not create:
                    return False
                current.mkdir(mode=0o700)
                metadata = current.lstat()
            if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
                raise ConfigError("Local setup directories must belong to the current user and must not be symbolic links.")
            if create and current.is_relative_to(application):
                current.chmod(0o700)
        return True
    except OSError:
        raise ConfigError("Cannot prepare the private local configuration directory.") from None


def private_directory(directory: Path) -> Path:
    """Create only owned application directories; never chmod shared ancestors."""
    _prepare_private_path(directory, create=True)
    return directory


def save_config(values: dict[str, str]) -> None:
    """Replace settings atomically with a mode-0600 file; callers supply all values."""
    validated = _validate(values)
    encoded = (json.dumps({"version": 1, **validated}, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    if len(encoded) > MAX_CONFIG_BYTES:
        raise ConfigError("The local configuration exceeds its size limit.")
    directory = private_directory(config_directory())
    temporary = None
    try:
        descriptor, filename = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=directory)
        temporary = Path(filename)
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, config_path())
    except OSError:
        raise ConfigError("Cannot save the local configuration. Check directory permissions and available space.") from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                raise ConfigError("Cannot remove a temporary configuration file. Review the private setup directory permissions.") from None
