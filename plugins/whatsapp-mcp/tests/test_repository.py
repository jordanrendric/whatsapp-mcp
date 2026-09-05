import hashlib
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from whatsapp_mcp.repository import Repository, RepositoryError, REQUIRED


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "ChatStorage.sqlite"
        self.media = self.root / "Message"
        (self.media / "Media").mkdir(parents=True)
        self.writer = sqlite3.connect(self.db)
        self.addCleanup(self.writer.close)
        self.writer.execute("PRAGMA journal_mode=WAL")
        text_columns = {"ZCONTACTJID", "ZPARTNERNAME", "ZTEXT", "ZFROMJID", "ZTOJID", "ZPUSHNAME", "ZMEMBERJID", "ZCONTACTNAME", "ZMEDIALOCALPATH", "ZTITLE"}
        for table, columns in REQUIRED.items():
            definitions = [name + (" INTEGER PRIMARY KEY" if name == "Z_PK" else " TEXT" if name in text_columns else " REAL" if name in {"ZMESSAGEDATE", "ZLASTMESSAGEDATE"} else " INTEGER") for name in sorted(columns)]
            self.writer.execute(f'CREATE TABLE {table} ({",".join(definitions)})')
        self.insert("ZWACHATSESSION", Z_PK=1, ZCONTACTJID="sample@g.us", ZPARTNERNAME="Sample group", ZSESSIONTYPE=1, ZLASTMESSAGEDATE=100, ZHIDDEN=0, ZREMOVED=0)
        self.insert("ZWACHATSESSION", Z_PK=2, ZCONTACTJID="sample@s.whatsapp.net", ZPARTNERNAME="Example contact", ZSESSIONTYPE=0, ZLASTMESSAGEDATE=110, ZHIDDEN=0, ZREMOVED=0)
        self.insert("ZWACHATSESSION", Z_PK=3, ZCONTACTJID="hidden@s.whatsapp.net", ZPARTNERNAME="Hidden", ZSESSIONTYPE=0, ZLASTMESSAGEDATE=120, ZHIDDEN=1, ZREMOVED=0)
        self.insert("ZWAGROUPMEMBER", Z_PK=4, ZMEMBERJID="sender@lid", ZCONTACTNAME="Example sender")
        # IDs intentionally disagree with conversation order; sort 0 is duplicated.
        for pk, order, ts, text in [(50, -2, 1, "match first"), (3, -1, 2, "match second"), (9, 0, 3, "literal 10%_ match"), (12, 0, 3, "match fourth"), (1, 1, 4, "match newest")]:
            self.insert("ZWAMESSAGE", Z_PK=pk, ZCHATSESSION=1, ZSORT=order, ZMESSAGEDATE=ts, ZISFROMME=0, ZMESSAGETYPE=0, ZTEXT=text, ZFROMJID="sample@g.us", ZPUSHNAME="Remote name", ZGROUPMEMBER=4)
        self.writer.commit()
        self.repo = Repository(self.db, self.media)

    def insert(self, table, **values):
        self.writer.execute(f'INSERT INTO {table} ({",".join(values)}) VALUES ({",".join("?" for _ in values)})', list(values.values()))

    def attach(self, path="Media/voice.opus", message_type=3, pk=99):
        self.insert("ZWAMEDIAITEM", Z_PK=pk, ZMEDIALOCALPATH=path, ZFILESIZE=6, ZMOVIEDURATION=10)
        self.insert("ZWAMESSAGE", Z_PK=pk, ZCHATSESSION=1, ZSORT=4, ZMESSAGEDATE=5, ZISFROMME=0, ZMESSAGETYPE=message_type, ZFROMJID="sample@g.us", ZMEDIAITEM=pk, ZGROUPMEMBER=4)
        self.writer.commit()
        return pk

    def test_database_and_live_wal_are_unchanged(self):
        tracked = [self.db, Path(str(self.db) + "-wal")]
        before = {p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns, p.stat().st_size) for p in tracked}
        self.assertTrue(self.repo.status()["read_only"])
        self.assertEqual(len(self.repo.get_messages(1)["messages"]), 5)
        self.repo.search_messages("match")
        after = {p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns, p.stat().st_size) for p in tracked}
        self.assertEqual(before, after)

    def test_pagination_uses_sort_and_id_without_dropping_ties(self):
        ids, anchor = [], None
        while True:
            result = self.repo.get_messages(1, limit=2, before_id=anchor)
            ids = [m["id"] for m in result["messages"]] + ids
            if not result["has_more"]:
                break
            anchor = result["next_before_id"]
        self.assertEqual(ids, [50, 3, 9, 12, 1])
        result = self.repo.get_messages(1, limit=2, after_id=50)
        self.assertEqual([m["id"] for m in result["messages"]], [3, 9])
        self.assertTrue(result["has_more"])

    def test_search_timestamp_pagination_handles_ties(self):
        ids, anchor = [], None
        while True:
            result = self.repo.search_messages("match", limit=2, before_id=anchor)
            ids.extend(m["id"] for m in result["messages"])
            if not result["has_more"]:
                break
            anchor = result["next_before_id"]
        self.assertEqual(ids, [1, 12, 9, 3, 50])

    def test_group_author_is_member_not_group_jid(self):
        message = self.repo.get_message(50)
        self.assertEqual(message["sender"]["jid"], "sender@lid")
        self.assertEqual(message["sender"]["name"], "Example sender")
        self.writer.execute("UPDATE ZWAMESSAGE SET ZGROUPMEMBER=NULL WHERE Z_PK=50")
        self.writer.commit()
        self.assertIsNone(self.repo.get_message(50)["sender"]["jid"])

    def test_coredata_date_and_exclusive_until(self):
        self.assertEqual(self.repo.get_message(50)["sent_at"], "2001-01-01T00:00:01.000Z")
        result = self.repo.search_messages("match", since="2001-01-01T00:00:02Z", until="2001-01-01T00:00:03Z")
        self.assertEqual([m["id"] for m in result["messages"]], [3])
        with self.assertRaises(RepositoryError):
            self.repo.search_messages("match", since="2001-01-01T00:00:02")
        with self.assertRaises(RepositoryError):
            self.repo.search_messages("match", since="2001-01-01")

    def test_literal_search_and_sql_injection(self):
        self.assertEqual([m["id"] for m in self.repo.search_messages("10%_")["messages"]], [9])
        self.assertEqual(self.repo.search_messages("' OR 1=1 --")["messages"], [])
        self.assertEqual(len(self.repo.get_messages(1)["messages"]), 5)

    def test_chats_filters_and_hidden_rows(self):
        self.assertEqual([c["id"] for c in self.repo.list_chats(kind="group")["chats"]], [1])
        self.assertEqual([c["id"] for c in self.repo.list_chats(kind="contact")["chats"]], [2])
        self.assertEqual([c["id"] for c in self.repo.list_chats(query="Example")["chats"]], [2])
        page = self.repo.list_chats(limit=1)
        self.assertTrue(page["has_more"])
        self.assertEqual(page["next_offset"], 1)

    def test_hidden_removed_and_orphaned_messages_are_excluded_from_all_readers(self):
        self.insert("ZWACHATSESSION", Z_PK=4, ZCONTACTJID="removed@s.whatsapp.net",
                    ZPARTNERNAME="Removed", ZSESSIONTYPE=0, ZHIDDEN=0, ZREMOVED=1)
        for pk, chat_id in ((300, 3), (400, 4), (500, 999)):
            self.insert("ZWAMEDIAITEM", Z_PK=pk, ZMEDIALOCALPATH="Media/voice.opus")
            self.insert("ZWAMESSAGE", Z_PK=pk, ZCHATSESSION=chat_id, ZSORT=10,
                        ZMESSAGEDATE=100, ZISFROMME=0, ZMESSAGETYPE=3,
                        ZTEXT="private synthetic needle", ZMEDIAITEM=pk)
        (self.media / "Media/voice.opus").write_bytes(b"synthetic audio")
        self.writer.commit()
        self.assertEqual(self.repo.search_messages("private synthetic needle")["messages"], [])
        for pk, chat_id in ((300, 3), (400, 4), (500, 999)):
            with self.subTest(chat_id=chat_id):
                self.assertEqual(self.repo.search_messages("needle", chat_id=chat_id)["messages"], [])
                for method, args in ((self.repo.get_message, (pk,)),
                                     (self.repo.get_messages, (chat_id,)),
                                     (self.repo.get_audio_path, (pk,))):
                    with self.assertRaises(RepositoryError):
                        method(*args)
                with self.assertRaises(RepositoryError):
                    with self.repo.open_audio(pk):
                        self.fail("An excluded message must not open media")
                with self.assertRaises(RepositoryError):
                    self.repo.search_messages("needle", before_id=pk)
                with self.assertRaises(RepositoryError):
                    self.repo.get_messages(1, before_id=pk)

    def test_audio_resolves_under_message_root(self):
        (self.media / "Media/voice.opus").write_bytes(b"OggSxx")
        pk = self.attach()
        self.assertEqual(self.repo.get_audio_path(pk), (self.media / "Media/voice.opus").resolve())
        self.assertTrue(self.repo.get_message(pk)["media"]["local_available"])
        (self.media / "Media/voice.wav").write_bytes(b"RIFFxx")
        pk = self.attach("Media/voice.wav", message_type=8, pk=100)
        self.assertTrue(self.repo.get_message(pk)["media"]["is_audio"])
        self.assertEqual(self.repo.get_audio_path(pk).suffix, ".wav")

    def test_missing_and_unsafe_media_paths(self):
        outside = self.root / "outside.opus"
        outside.write_bytes(b"private")
        (self.media / "Media/link.opus").symlink_to(outside)
        for index, path in enumerate(["../outside.opus", str(outside), "https://example.invalid/audio.opus", "Media/link.opus", "Media/missing.opus"]):
            pk = self.attach(path, pk=100 + index)
            with self.assertRaises(RepositoryError):
                self.repo.get_audio_path(pk)
            self.assertFalse(self.repo.get_message(pk)["media"]["local_available"])

    def test_open_audio_rejects_leaf_and_directory_symlinks(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "voice.opus").write_bytes(b"outside synthetic data")
        (self.media / "Media/voice.opus").symlink_to(outside / "voice.opus")
        pk = self.attach()
        with self.assertRaises(RepositoryError):
            with self.repo.open_audio(pk):
                self.fail("Leaf symlinks must be rejected")
        (self.media / "Media/voice.opus").unlink()
        (self.media / "Media").rmdir()
        (self.media / "Media").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(RepositoryError):
            with self.repo.open_audio(pk):
                self.fail("Directory symlinks must be rejected")

    def test_open_audio_pins_file_before_path_is_replaced(self):
        source = self.media / "Media/voice.opus"
        source.write_bytes(b"authorized synthetic audio")
        pk = self.attach()
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "voice.opus").write_bytes(b"outside synthetic data")
        with self.repo.open_audio(pk) as descriptor:
            (self.media / "Media").rename(self.media / "original-media")
            (self.media / "Media").symlink_to(outside, target_is_directory=True)
            self.assertEqual(os.read(descriptor, 100), b"authorized synthetic audio")
        with self.assertRaises(OSError):
            os.fstat(descriptor)

    def test_open_audio_pins_parent_during_directory_replacement(self):
        source = self.media / "Media/voice.opus"
        source.write_bytes(b"authorized synthetic audio")
        pk = self.attach()
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "voice.opus").write_bytes(b"outside synthetic data")
        real_open = os.open
        def replace_after_open(path, flags, **kwargs):
            descriptor = real_open(path, flags, **kwargs)
            if path == "Media":
                (self.media / "Media").rename(self.media / "original-media")
                (self.media / "Media").symlink_to(outside, target_is_directory=True)
            return descriptor
        with patch("whatsapp_mcp.repository.os.open", side_effect=replace_after_open):
            with self.repo.open_audio(pk) as descriptor:
                self.assertEqual(os.read(descriptor, 100), b"authorized synthetic audio")

    def test_open_audio_rejects_fifo_and_closes_every_descriptor(self):
        os.mkfifo(self.media / "Media/voice.opus")
        pk = self.attach()
        opened = []
        real_open = os.open
        def record_open(*args, **kwargs):
            descriptor = real_open(*args, **kwargs)
            opened.append(descriptor)
            return descriptor
        with patch("whatsapp_mcp.repository.os.open", side_effect=record_open):
            with self.assertRaises(RepositoryError):
                with self.repo.open_audio(pk):
                    self.fail("FIFOs must be rejected without blocking")
        for descriptor in set(opened):
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    def test_authorizer_blocks_mutations_attach_and_arbitrary_reads(self):
        for sql in ["DELETE FROM ZWAMESSAGE", "ATTACH ':memory:' AS external", "PRAGMA query_only=OFF", "SELECT randomblob(5)", "CREATE TABLE injected(x)"]:
            with self.repo._connect() as c:
                with self.assertRaises(sqlite3.DatabaseError):
                    c.execute(sql)

    def test_sqlite_uri_is_read_only_even_without_extra_guards(self):
        with self.repo._connect() as c:
            c.set_authorizer(None)
            c.execute("PRAGMA query_only=OFF")
            with self.assertRaisesRegex(sqlite3.OperationalError, "readonly"):
                c.execute("UPDATE ZWAMESSAGE SET ZTEXT='changed' WHERE Z_PK=50")
        self.assertEqual(self.repo.get_message(50)["text"], "match first")

    def test_schema_drift_fails_closed(self):
        self.writer.execute("ALTER TABLE ZWAMESSAGE RENAME COLUMN ZSORT TO ZSORT_NEW")
        self.writer.commit()
        with self.assertRaisesRegex(RepositoryError, "ZSORT"):
            self.repo.status()

    def test_invalid_ids_and_anchors(self):
        for value in [True, 0, -1, "1", 2**63]:
            with self.assertRaises(RepositoryError):
                self.repo.get_message(value)
        with self.assertRaises(RepositoryError):
            self.repo.get_messages(2, before_id=50)
        with self.assertRaises(RepositoryError):
            self.repo.get_messages(1, before_id=50, after_id=3)
        with self.assertRaises(RepositoryError):
            self.repo.list_chats(limit=101)

    def test_null_ordering_fails_explicitly(self):
        self.writer.execute("UPDATE ZWAMESSAGE SET ZSORT=NULL WHERE Z_PK=50")
        self.writer.commit()
        with self.assertRaisesRegex(RepositoryError, "ordering"):
            self.repo.get_messages(1)
        with self.assertRaisesRegex(RepositoryError, "ordering"):
            self.repo.get_messages(1, before_id=50)

    def test_environment_overrides(self):
        with patch.dict(os.environ, {"WHATSAPP_MCP_DB_PATH": str(self.db), "WHATSAPP_MCP_MEDIA_ROOT": str(self.media)}):
            self.assertEqual(Repository().get_message(50)["id"], 50)

    def test_discovery_rejects_multiple_accounts(self):
        personal = self.root / "Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite"
        business = self.root / "Library/Group Containers/group.net.whatsapp.WhatsAppSMB.shared/ChatStorage.sqlite"
        for p in [personal, business]:
            p.parent.mkdir(parents=True)
            p.touch()
        with patch("whatsapp_mcp.repository.sys.platform", "darwin"), patch("whatsapp_mcp.repository.Path.home", return_value=self.root):
            with self.assertRaisesRegex(RepositoryError, "Multiple"):
                Repository._discover()


if __name__ == "__main__":
    unittest.main()
