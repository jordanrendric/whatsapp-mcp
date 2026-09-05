"""Bounded, read-only access to the macOS WhatsApp Core Data store."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import time
from typing import Any, Iterator
from urllib.parse import urlsplit

from .config import setting


APPLE_EPOCH = 978307200
MAX_PAGE = 100
QUERY_SECONDS = 5.0
MAX_CDN_URL_CHARS = 8192
# Every reader uses the same visibility rule; archive status is independent.
VISIBLE_CHAT = "COALESCE(c.ZREMOVED,0)=0 AND COALESCE(c.ZHIDDEN,0)=0"
AUDIO_EXTENSIONS = frozenset({".opus", ".ogg", ".mp3", ".m4a", ".aac", ".wav", ".flac", ".aiff", ".aif", ".caf"})
REQUIRED = {
    "ZWACHATSESSION": {"Z_PK", "ZCONTACTJID", "ZPARTNERNAME", "ZLASTMESSAGEDATE", "ZSESSIONTYPE", "ZARCHIVED", "ZHIDDEN", "ZREMOVED", "ZUNREADCOUNT"},
    "ZWAMESSAGE": {"Z_PK", "ZCHATSESSION", "ZSORT", "ZMESSAGEDATE", "ZISFROMME", "ZMESSAGETYPE", "ZTEXT", "ZFROMJID", "ZTOJID", "ZPUSHNAME", "ZGROUPMEMBER", "ZMEDIAITEM"},
    "ZWAGROUPMEMBER": {"Z_PK", "ZMEMBERJID", "ZCONTACTNAME"},
    "ZWAMEDIAITEM": {"Z_PK", "ZMEDIALOCALPATH", "ZFILESIZE", "ZMOVIEDURATION", "ZTITLE"},
}


class RepositoryError(Exception):
    """A safe user-facing repository error, without message contents."""


def _integer(value: Any, label: str, minimum: int = 1, maximum: int | None = None) -> int:
    maximum = maximum if maximum is not None else 2**63 - 1
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise RepositoryError(f"{label} must be an integer between {minimum} and {maximum}.")
    return value


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value) + APPLE_EPOCH, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except (ValueError, OverflowError, TypeError, OSError):
        return None


def _apple_date(value: str | None, label: str) -> float | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 64:
        raise RepositoryError(f"{label} must be an ISO 8601 timestamp with timezone.")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("Timestamp needs a timezone")
        result = dt.timestamp() - APPLE_EPOCH
        if not math.isfinite(result):
            raise ValueError("Invalid timestamp")
        return result
    except (ValueError, OverflowError, OSError):
        raise RepositoryError(f"{label} must be an ISO 8601 timestamp with timezone.") from None


def _query(value: str | None, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 1000:
        raise RepositoryError("query must contain 1 to 1000 characters.")
    return "%" + value.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _cdn_url(value: Any) -> str | None:
    """Validate stored CDN metadata without normalizing signed URLs or fetching it."""
    if (not isinstance(value, str) or not 1 <= len(value) <= MAX_CDN_URL_CHARS
            or any(not 33 <= ord(character) <= 126 for character in value)
            or any(character in value for character in '\\#"<>`{}|^')
            or re.search(r"%(?![0-9a-f]{2})", value, re.IGNORECASE)
            or re.search(r"%(?:0[0-9a-f]|1[0-9a-f]|7f)", value, re.IGNORECASE)):
        return None
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        if (parsed.scheme != "https" or not host or len(host) > 253
                or parsed.username is not None or parsed.password is not None
                or parsed.port not in {None, 443}
                or parsed.netloc.lower() not in {host, host + ":443"}
                or not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+whatsapp\.net", host)):
            return None
    except ValueError:
        return None
    return value


class Repository:
    """One database per instance; no downloads, writes, copies, or free-form SQL."""

    def __init__(self, db_path: Path | None = None, media_root: Path | None = None):
        supplied = db_path or setting("WHATSAPP_MCP_DB_PATH")
        self.db_path = Path(supplied).expanduser().resolve() if supplied else self._discover()
        if not self.db_path.is_file():
            raise RepositoryError("WhatsApp database is unavailable. Set WHATSAPP_MCP_DB_PATH to ChatStorage.sqlite.")
        configured_media = media_root or setting("WHATSAPP_MCP_MEDIA_ROOT")
        self.media_root = (Path(configured_media).expanduser() if configured_media else self.db_path.parent / "Message").resolve()

    @staticmethod
    def discover_candidates() -> list[Path]:
        if sys.platform != "darwin":
            raise RepositoryError("Automatic WhatsApp discovery is supported on macOS only.")
        home = Path.home()
        candidates = [
            home / "Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite",
            home / "Library/Group Containers/group.net.whatsapp.WhatsAppSMB.shared/ChatStorage.sqlite",
            home / "Library/Containers/net.whatsapp.WhatsApp/Data/Library/Application Support/WhatsApp/ChatStorage.sqlite",
        ]
        return list(dict.fromkeys(p.resolve() for p in candidates if p.is_file()))

    @staticmethod
    def _discover() -> Path:
        found = Repository.discover_candidates()
        if not found:
            raise RepositoryError("No local WhatsApp ChatStorage.sqlite found. Open WhatsApp or set WHATSAPP_MCP_DB_PATH; macOS may require file-access permission.")
        if len(found) > 1:
            raise RepositoryError("Multiple WhatsApp databases found. Set WHATSAPP_MCP_DB_PATH to choose the account explicitly.")
        return found[0]

    @staticmethod
    def _authorize(action: int, arg1: str | None, arg2: str | None, _db: str | None, _source: str | None) -> int:
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ and arg1 in {*REQUIRED, "sqlite_master"}:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in {"coalesce", "like", "substr", "typeof"}:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_PRAGMA and arg1 == "table_info" and arg2 in REQUIRED:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = None
        try:
            # Do not use immutable=1: the running app's WAL contains recent messages.
            connection = sqlite3.connect(self.db_path.as_uri() + "?mode=ro", uri=True, timeout=1.0)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            # Keep schema, pagination anchor, and result rows in one read snapshot.
            connection.execute("BEGIN")
            deadline = time.monotonic() + QUERY_SECONDS
            connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            connection.set_authorizer(self._authorize)
            for table, required in REQUIRED.items():
                kind = connection.execute("SELECT type FROM sqlite_master WHERE name=?", (table,)).fetchone()
                if kind is None or kind["type"] != "table":
                    raise RepositoryError(f"Unsupported WhatsApp schema: required table {table} is unavailable.")
                actual = {r["name"] for r in connection.execute(f'PRAGMA table_info("{table}")')}
                if not required <= actual:
                    missing = ", ".join(sorted(required - actual))
                    raise RepositoryError(f"Unsupported WhatsApp schema: {table} is missing {missing}.")
            yield connection
        except sqlite3.OperationalError as exc:
            if "interrupted" in str(exc):
                raise RepositoryError("The local query exceeded its time limit. Narrow the chat or date range.") from None
            if "locked" in str(exc) or "busy" in str(exc):
                raise RepositoryError("WhatsApp database is busy. Try the read again shortly.") from None
            raise RepositoryError("Cannot read the WhatsApp database. Check the selected file, macOS access permissions, and database format.") from None
        except sqlite3.DatabaseError:
            raise RepositoryError("The local database read failed; its format or schema may be unsupported.") from None
        finally:
            if connection is not None:
                connection.close()

    def status(self) -> dict[str, Any]:
        with self._connect():
            pass
        return {
            "available": True,
            "read_only": True,
            "database_path": str(self.db_path),
            "media_root": str(self.media_root),
            "media_root_exists": self.media_root.is_dir(),
            "schema": "whatsapp-macos-coredata",
            "history_scope": "Only messages and media already present in this Mac's local WhatsApp storage; synchronization may be partial.",
        }

    @staticmethod
    def _chat_kind(session_type: int | None) -> str:
        # Types 1 and 4 both have group-info records and @g.us chat identifiers.
        # Preserve all other raw types rather than guessing their product labels.
        return {0: "contact", 1: "group", 4: "group"}.get(session_type, "other")

    def list_chats(self, query: str | None = None, limit: int = 30, offset: int = 0, kind: str = "all") -> dict[str, Any]:
        _integer(limit, "limit", maximum=MAX_PAGE)
        _integer(offset, "offset", minimum=0, maximum=100000)
        if kind not in {"all", "group", "contact"}:
            raise RepositoryError("kind must be all, group, or contact.")
        pattern = _query(query, optional=True)
        where = [VISIBLE_CHAT]
        args: list[Any] = []
        if pattern is not None:
            where.append("(ZPARTNERNAME LIKE ? ESCAPE '\\' OR ZCONTACTJID LIKE ? ESCAPE '\\')")
            args.extend([pattern, pattern])
        if kind == "contact":
            where.append("ZSESSIONTYPE=0")
        elif kind == "group":
            where.append("ZSESSIONTYPE IN (1,4)")
        sql = "SELECT Z_PK,ZCONTACTJID,ZPARTNERNAME,ZLASTMESSAGEDATE,ZSESSIONTYPE,ZARCHIVED,ZUNREADCOUNT FROM ZWACHATSESSION c WHERE " + " AND ".join(where) + " ORDER BY COALESCE(ZLASTMESSAGEDATE,0) DESC,Z_PK DESC LIMIT ? OFFSET ?"
        with self._connect() as c:
            rows = c.execute(sql, [*args, limit + 1, offset]).fetchall()
        chats = [{"id": r["Z_PK"], "chat_id": r["Z_PK"], "jid": r["ZCONTACTJID"], "name": r["ZPARTNERNAME"], "kind": self._chat_kind(r["ZSESSIONTYPE"]), "session_type": r["ZSESSIONTYPE"], "last_message_at": _iso(r["ZLASTMESSAGEDATE"]), "archived": bool(r["ZARCHIVED"]), "unread_count": r["ZUNREADCOUNT"]} for r in rows[:limit]]
        return {"chats": chats, "has_more": len(rows) > limit, "next_offset": offset + limit if len(rows) > limit else None}

    _MESSAGE_SELECT = """SELECT m.Z_PK,m.ZCHATSESSION,m.ZSORT,m.ZMESSAGEDATE,m.ZISFROMME,
        m.ZMESSAGETYPE,m.ZTEXT,m.ZFROMJID,m.ZTOJID,m.ZPUSHNAME,
        g.ZMEMBERJID,g.ZCONTACTNAME,c.ZPARTNERNAME,c.ZCONTACTJID,c.ZSESSIONTYPE,
        i.Z_PK AS MEDIA_ID,i.ZMEDIALOCALPATH,i.ZFILESIZE,i.ZMOVIEDURATION,i.ZTITLE
        FROM ZWAMESSAGE m JOIN ZWACHATSESSION c ON c.Z_PK=m.ZCHATSESSION
        LEFT JOIN ZWAGROUPMEMBER g ON g.Z_PK=m.ZGROUPMEMBER
        LEFT JOIN ZWAMEDIAITEM i ON i.Z_PK=m.ZMEDIAITEM"""

    @staticmethod
    def _relative_media(raw: str | None) -> Path:
        if not isinstance(raw, str) or not raw or "\x00" in raw or "://" in raw:
            raise RepositoryError("This message has no usable local media file. Download it in WhatsApp first.")
        path = Path(raw)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise RepositoryError("The media path is outside the configured WhatsApp media root.")
        return path

    @contextmanager
    def _open_media(self, raw: str | None) -> Iterator[int]:
        """Open beneath the configured root without following mutable symlinks.

        Each open is relative to its already-open parent descriptor. Holding the
        final descriptor through the copy prevents path replacement from changing
        which file is transcribed. This is not a sandbox against local malware.
        """
        relative = self._relative_media(raw)
        directory_fd = source_fd = None
        try:
            directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            directory_fd = os.open(self.media_root.anchor, directory_flags)
            # media_root is absolute and resolved once from trusted configuration.
            # Walk its ancestors too, so replacing one by a symlink fails closed.
            parts = (*self.media_root.parts[1:], *relative.parts[:-1])
            for component in parts:
                next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
                os.close(directory_fd)
                directory_fd = next_fd
            source_fd = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                dir_fd=directory_fd)
            if not stat.S_ISREG(os.fstat(source_fd).st_mode):
                raise RepositoryError("The local media path is not a regular file.")
        except OSError:
            if source_fd is not None:
                os.close(source_fd)
                source_fd = None
            raise RepositoryError("The local media file is missing, inaccessible, or uses a symbolic link.") from None
        except BaseException:
            if source_fd is not None:
                os.close(source_fd)
                source_fd = None
            raise
        finally:
            if directory_fd is not None:
                os.close(directory_fd)
        try:
            yield source_fd
        finally:
            if source_fd is not None:
                os.close(source_fd)

    def _resolve_media(self, raw: str | None) -> Path:
        """Check availability only; consumers must use open_audio to pin the file."""
        with self._open_media(raw):
            return self.media_root / self._relative_media(raw)

    def _message(self, row: sqlite3.Row) -> dict[str, Any]:
        is_me = bool(row["ZISFROMME"])
        is_group = row["ZSESSIONTYPE"] in (1, 4)
        if is_me:
            sender = {"is_me": True, "jid": None, "name": "You", "source": "ZISFROMME"}
        elif row["ZMEMBERJID"]:
            sender = {"is_me": False, "jid": row["ZMEMBERJID"], "name": row["ZCONTACTNAME"] or row["ZPUSHNAME"], "source": "group_member"}
        else:
            # On group rows ZFROMJID usually identifies the group itself.
            sender = {"is_me": False, "jid": None if is_group else row["ZFROMJID"], "name": row["ZPUSHNAME"] or (None if is_group else row["ZPARTNERNAME"]), "source": "unknown_group_member" if is_group else "message"}
        raw_path = row["ZMEDIALOCALPATH"]
        extension = Path(raw_path).suffix.lower() if isinstance(raw_path, str) and raw_path else None
        available = False
        if raw_path:
            try:
                self._resolve_media(raw_path)
                available = True
            except RepositoryError:
                pass
        message_type = row["ZMESSAGETYPE"]
        kind = {0: "text", 1: "image", 2: "video", 3: "audio", 8: "document", 11: "video", 15: "sticker", 54: "video"}.get(message_type, "other")
        audio = message_type == 3 or (message_type == 8 and extension in AUDIO_EXTENSIONS)
        media = None
        if kind != "text" and row["MEDIA_ID"] is not None:
            media = {"id": row["MEDIA_ID"], "kind": kind, "extension": extension, "title": row["ZTITLE"], "local_available": available, "is_audio": audio, "size_bytes": row["ZFILESIZE"], "duration_seconds": row["ZMOVIEDURATION"] if audio or kind == "video" else None}
        content = row["ZTEXT"] or ""
        return {"id": row["Z_PK"], "chat_id": row["ZCHATSESSION"], "chat_name": row["ZPARTNERNAME"], "sent_at": _iso(row["ZMESSAGEDATE"]), "sort": row["ZSORT"], "sender": sender, "kind": kind, "message_type": message_type, "text": content[:20000], "text_truncated": len(content) > 20000, "media": media}

    @staticmethod
    def _anchor(c: sqlite3.Connection, message_id: int, chat_id: int | None = None) -> sqlite3.Row:
        _integer(message_id, "message_id")
        row = c.execute("SELECT m.Z_PK,m.ZCHATSESSION,m.ZSORT,m.ZMESSAGEDATE FROM ZWAMESSAGE m JOIN ZWACHATSESSION c ON c.Z_PK=m.ZCHATSESSION WHERE m.Z_PK=? AND " + VISIBLE_CHAT, (message_id,)).fetchone()
        if row is None or (chat_id is not None and row["ZCHATSESSION"] != chat_id):
            raise RepositoryError("The pagination anchor does not exist in the selected chat.")
        if row["ZSORT"] is None or row["ZMESSAGEDATE"] is None:
            raise RepositoryError("The pagination anchor has no ordering key; this local schema is unsupported.")
        return row

    def get_messages(self, chat_id: int, limit: int = 50, before_id: int | None = None, after_id: int | None = None) -> dict[str, Any]:
        _integer(chat_id, "chat_id")
        _integer(limit, "limit", maximum=MAX_PAGE)
        if before_id is not None and after_id is not None:
            raise RepositoryError("Use before_id or after_id, not both.")
        with self._connect() as c:
            if c.execute("SELECT c.Z_PK FROM ZWACHATSESSION c WHERE c.Z_PK=? AND " + VISIBLE_CHAT, (chat_id,)).fetchone() is None:
                raise RepositoryError("The requested chat was not found locally.")
            where, args = [VISIBLE_CHAT, "m.ZCHATSESSION=?"], [chat_id]
            direction = "DESC"
            anchor_id = before_id if before_id is not None else after_id
            if anchor_id is not None:
                anchor = self._anchor(c, anchor_id, chat_id)
                op = "<" if before_id is not None else ">"
                where.append(f"(m.ZSORT {op} ? OR (m.ZSORT=? AND m.Z_PK {op} ?))")
                args.extend([anchor["ZSORT"], anchor["ZSORT"], anchor["Z_PK"]])
                direction = "DESC" if before_id is not None else "ASC"
            rows = c.execute(self._MESSAGE_SELECT + " WHERE " + " AND ".join(where) + f" ORDER BY m.ZSORT {direction},m.Z_PK {direction} LIMIT ?", [*args, limit + 1]).fetchall()
        if any(row["ZSORT"] is None for row in rows):
            raise RepositoryError("A message has no conversation ordering key; pagination cannot safely continue.")
        page = rows[:limit]
        if direction == "DESC":
            page.reverse()
        messages = [self._message(row) for row in page]
        return {"chat_id": chat_id, "messages": messages, "order": "conversation_ascending", "has_more": len(rows) > limit, "more_direction": "after" if after_id is not None else "before", "next_before_id": messages[0]["id"] if messages else None, "next_after_id": messages[-1]["id"] if messages else None}

    def search_messages(self, query: str, chat_id: int | None = None, limit: int = 50, before_id: int | None = None, since: str | None = None, until: str | None = None) -> dict[str, Any]:
        pattern = _query(query)
        _integer(limit, "limit", maximum=MAX_PAGE)
        start, end = _apple_date(since, "since"), _apple_date(until, "until")
        if start is not None and end is not None and start >= end:
            raise RepositoryError("since must be earlier than until; until is exclusive.")
        where, args = [VISIBLE_CHAT, "m.ZTEXT LIKE ? ESCAPE '\\'"], [pattern]
        if chat_id is not None:
            _integer(chat_id, "chat_id")
            where.append("m.ZCHATSESSION=?")
            args.append(chat_id)
        if start is not None:
            where.append("m.ZMESSAGEDATE>=?")
            args.append(start)
        if end is not None:
            where.append("m.ZMESSAGEDATE<?")
            args.append(end)
        with self._connect() as c:
            if before_id is not None:
                anchor = self._anchor(c, before_id, chat_id)
                where.append("(m.ZMESSAGEDATE<? OR (m.ZMESSAGEDATE=? AND m.Z_PK<?))")
                args.extend([anchor["ZMESSAGEDATE"], anchor["ZMESSAGEDATE"], anchor["Z_PK"]])
            rows = c.execute(self._MESSAGE_SELECT + " WHERE " + " AND ".join(where) + " ORDER BY m.ZMESSAGEDATE DESC,m.Z_PK DESC LIMIT ?", [*args, limit + 1]).fetchall()
        if any(row["ZMESSAGEDATE"] is None for row in rows):
            raise RepositoryError("A message has no timestamp; search pagination cannot safely continue.")
        messages = [self._message(row) for row in rows[:limit]]
        return {"messages": messages, "order": "timestamp_descending", "has_more": len(rows) > limit, "next_before_id": messages[-1]["id"] if messages and len(rows) > limit else None, "search_scope": "Local message text and captions only; media contents and audio transcripts are not indexed."}

    def get_message(self, message_id: int) -> dict[str, Any]:
        _integer(message_id, "message_id")
        with self._connect() as c:
            row = c.execute(self._MESSAGE_SELECT + " WHERE m.Z_PK=? AND " + VISIBLE_CHAT, (message_id,)).fetchone()
        if row is None:
            raise RepositoryError("The requested message was not found locally.")
        return self._message(row)

    def get_media_url(self, message_id: int) -> dict[str, Any]:
        """Return only one visible message's stored, unverified CDN URL metadata.

        URL columns are optional: a schema without them still supports ordinary
        readers. Key material and opaque metadata are never selected. This method
        performs no remote request and does not decode or download media.
        """
        _integer(message_id, "message_id")
        with self._connect() as connection:
            columns = {row["name"] for row in connection.execute('PRAGMA table_info("ZWAMEDIAITEM")')}
            supported = "ZMEDIAURL" in columns
            # Slice bytes, not SQLite TEXT: TEXT substr stops at NUL and could
            # silently remove a forbidden control character from the stored URL.
            # Valid URLs here are ASCII; one extra byte detects oversized fields.
            url_column = f"substr(CAST(i.ZMEDIAURL AS BLOB),1,{MAX_CDN_URL_CHARS + 1})" if supported else "NULL"
            type_column = "typeof(i.ZMEDIAURL)" if supported else "NULL"
            date_column = ("CASE WHEN typeof(i.ZMEDIAURLDATE) IN ('integer','real') "
                           "THEN i.ZMEDIAURLDATE ELSE NULL END") if "ZMEDIAURLDATE" in columns else "NULL"
            row = connection.execute(
                "SELECT m.Z_PK,m.ZMESSAGETYPE,i.Z_PK AS MEDIA_ID,i.ZMEDIALOCALPATH,"
                + url_column + " AS CDN_URL_VALUE," + type_column + " AS CDN_URL_TYPE,"
                + date_column + " AS CDN_URL_DATE "
                "FROM ZWAMESSAGE m JOIN ZWACHATSESSION c ON c.Z_PK=m.ZCHATSESSION "
                "LEFT JOIN ZWAMEDIAITEM i ON i.Z_PK=m.ZMEDIAITEM "
                "WHERE m.Z_PK=? AND " + VISIBLE_CHAT, (message_id,),
            ).fetchone()
        if row is None:
            raise RepositoryError("The requested message was not found locally.")

        local_available = False
        if row["MEDIA_ID"] is not None and row["ZMEDIALOCALPATH"]:
            try:
                self._resolve_media(row["ZMEDIALOCALPATH"])
                local_available = True
            except RepositoryError:
                pass
        raw_url = row["CDN_URL_VALUE"]
        url_text = None
        if row["CDN_URL_TYPE"] == "text" and isinstance(raw_url, bytes):
            try:
                url_text = raw_url.decode("ascii")
            except UnicodeError:
                pass
        url = _cdn_url(url_text)
        if not supported:
            reason = "schema_unsupported"
        elif row["MEDIA_ID"] is None:
            reason = "no_media"
        elif raw_url is None or (row["CDN_URL_TYPE"] == "text" and raw_url == b""):
            reason = "no_cdn_url"
        elif url is None:
            reason = "untrusted_cdn_url"
        else:
            reason = None
        return {
            "message_id": row["Z_PK"], "message_type": row["ZMESSAGETYPE"],
            "media_id": row["MEDIA_ID"], "capability_supported": supported,
            "available": url is not None, "cdn_url": url,
            "source_url_date": _iso(row["CDN_URL_DATE"]),
            "local_available": local_available, "reason": reason,
            "remote_availability": "unverified", "expiry": "unverified", "encryption": "unverified",
            "notice": "Stored metadata only; no remote request was made. The URL may be expired or point to encrypted bytes; plaintext availability is not guaranteed. Treat the URL as sensitive.",
        }

    def _audio_relative_path(self, message_id: int) -> str:
        _integer(message_id, "message_id")
        with self._connect() as c:
            row = c.execute("SELECT m.ZMESSAGETYPE,i.ZMEDIALOCALPATH FROM ZWAMESSAGE m JOIN ZWACHATSESSION c ON c.Z_PK=m.ZCHATSESSION LEFT JOIN ZWAMEDIAITEM i ON i.Z_PK=m.ZMEDIAITEM WHERE m.Z_PK=? AND " + VISIBLE_CHAT, (message_id,)).fetchone()
        if row is None:
            raise RepositoryError("The requested message was not found locally.")
        raw = row["ZMEDIALOCALPATH"]
        ext = Path(raw).suffix.lower() if isinstance(raw, str) else ""
        if row["ZMESSAGETYPE"] != 3 and not (row["ZMESSAGETYPE"] == 8 and ext in AUDIO_EXTENSIONS):
            raise RepositoryError("The selected message is not a supported audio attachment.")
        if ext not in AUDIO_EXTENSIONS:
            raise RepositoryError("The audio attachment has no supported local audio file.")
        self._relative_media(raw)
        return raw

    def get_audio_path(self, message_id: int) -> Path:
        """Availability helper; MCP transcription uses open_audio instead."""
        return self._resolve_media(self._audio_relative_path(message_id))

    @contextmanager
    def open_audio(self, message_id: int) -> Iterator[int]:
        """Keep one visible message's regular audio file pinned until work finishes."""
        with self._open_media(self._audio_relative_path(message_id)) as descriptor:
            yield descriptor
