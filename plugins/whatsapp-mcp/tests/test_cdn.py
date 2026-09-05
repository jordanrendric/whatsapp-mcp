"""Synthetic CDN metadata only; no live URLs, credentials, media or network calls."""

from contextlib import contextmanager
import hashlib
import json
import unittest
from unittest.mock import patch

import test_repository
from whatsapp_mcp.repository import MAX_CDN_URL_CHARS, RepositoryError


SYNTHETIC_URL = "https://mmg.whatsapp.net/synthetic-media.enc?token=synthetic-only"


class CDNMetadataTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_repository.RepositoryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.repo = self.fixture.repo
        self.writer = self.fixture.writer

    def add_url_columns(self, include_date=True):
        self.writer.execute("ALTER TABLE ZWAMEDIAITEM ADD COLUMN ZMEDIAURL TEXT")
        if include_date:
            self.writer.execute("ALTER TABLE ZWAMEDIAITEM ADD COLUMN ZMEDIAURLDATE REAL")
        self.writer.commit()

    def attach_url(self, url=SYNTHETIC_URL, pk=99, message_type=3,
                   path="Media/synthetic.opus", date=10):
        self.fixture.attach(path=path, message_type=message_type, pk=pk)
        self.writer.execute("UPDATE ZWAMEDIAITEM SET ZMEDIAURL=?,ZMEDIAURLDATE=? WHERE Z_PK=?",
                            (url, date, pk))
        self.writer.commit()
        return pk

    def test_valid_audio_url_is_returned_without_local_download(self):
        self.add_url_columns()
        pk = self.attach_url()
        result = self.repo.get_media_url(pk)
        self.assertTrue(result["capability_supported"])
        self.assertTrue(result["available"])
        self.assertEqual(result["cdn_url"], SYNTHETIC_URL)
        self.assertEqual(result["message_id"], pk)
        self.assertEqual(result["media_id"], pk)
        self.assertEqual(result["message_type"], 3)
        self.assertEqual(result["source_url_date"], "2001-01-01T00:00:10.000Z")
        self.assertFalse(result["local_available"])
        self.assertIsNone(result["reason"])
        for field in ("remote_availability", "expiry", "encryption"):
            self.assertEqual(result[field], "unverified")

    def test_valid_image_url_can_have_an_independent_local_file(self):
        self.add_url_columns()
        pk = self.attach_url(url="https://edge.cdn.whatsapp.net:443/synthetic.jpg?a=%2F",
                             message_type=1, path="Media/synthetic.jpg")
        (self.fixture.media / "Media/synthetic.jpg").write_bytes(b"synthetic image fixture")
        result = self.repo.get_media_url(pk)
        self.assertTrue(result["available"])
        self.assertTrue(result["local_available"])
        self.assertEqual(result["message_type"], 1)

    def test_missing_url_column_only_disables_this_capability(self):
        pk = self.fixture.attach()
        result = self.repo.get_media_url(pk)
        self.assertFalse(result["capability_supported"])
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "schema_unsupported")
        self.assertIsNone(result["cdn_url"])
        self.assertTrue(self.repo.status()["available"])
        self.assertEqual(self.repo.get_message(pk)["id"], pk)
        self.assertTrue(self.repo.search_messages("match")["messages"])

    def test_missing_date_column_does_not_disable_url(self):
        self.add_url_columns(include_date=False)
        pk = self.fixture.attach()
        self.writer.execute("UPDATE ZWAMEDIAITEM SET ZMEDIAURL=? WHERE Z_PK=?", (SYNTHETIC_URL, pk))
        self.writer.commit()
        result = self.repo.get_media_url(pk)
        self.assertTrue(result["available"])
        self.assertIsNone(result["source_url_date"])

    def test_no_media_and_missing_url_are_distinct(self):
        self.add_url_columns()
        self.assertEqual(self.repo.get_media_url(50)["reason"], "no_media")
        for index, url in enumerate((None, "")):
            pk = self.attach_url(url=url, pk=100 + index)
            result = self.repo.get_media_url(pk)
            self.assertEqual(result["reason"], "no_cdn_url")
            self.assertFalse(result["available"])
            self.assertIsNone(result["cdn_url"])

    def test_untrusted_urls_are_sanitized_and_never_returned(self):
        self.add_url_columns()
        bad_urls = [
            "http://mmg.whatsapp.net/synthetic", "file:///private/synthetic", "//mmg.whatsapp.net/synthetic",
            "https://whatsapp.net/synthetic", "https://mmg.whatsapp.net.evil.invalid/synthetic",
            "https://evil-whatsapp.net/synthetic", "https://whatsapp.net@evil.invalid/synthetic",
            "https://user@mmg.whatsapp.net/synthetic", "https://user:pass@mmg.whatsapp.net/synthetic",
            "https://@mmg.whatsapp.net/synthetic", "https://mmg.whatsapp.net:22/synthetic",
            "https://mmg.whatsapp.net:0/synthetic", "https://mmg.whatsapp.net:65536/synthetic",
            "https://mmg.whatsapp.net:/synthetic", "https://mmg.whatsapp.net:0443/synthetic",
            "https://127.0.0.1/synthetic", "https://[::1]/synthetic", "https://[invalid/synthetic",
            "https://mmg.whatsapp.net./synthetic", "https://mmg..whatsapp.net/synthetic",
            "https://-mmg.whatsapp.net/synthetic", "https://mmg-.whatsapp.net/synthetic",
            "https://mmg_whatsapp.net/synthetic", "https://mmg%2ewhatsapp.net/synthetic",
            "https://mmg.whatsapp.net\\@evil.invalid/synthetic", "https://mmg.whatsapp.net/synthetic#fragment",
            "https://mmg.whatsapp.net/synthetic#", " https://mmg.whatsapp.net/synthetic",
            "https://mmg.whatsapp.net/synthetic with space", "https://mmg.whatsapp.net/synthetic\n",
            "https://mmg.whatsapp.net/synthetic\t", "https://mmg.whatsapp.net/synthetic\x00",
            "https://mmg.whatsapp.net/synthetic%0D%0aheader", "https://mmg.whatsapp.net/synthetic%7f",
            "https://mmg.whatsapp.net/synthetic%", "https://mmg.whatsapp.net/synthetic%GG",
            'https://mmg.whatsapp.net/synthetic"', "https://mmg.whatsapp.net/<synthetic>",
            "https://mmg.whatsapp.net/synthetic\u202e", "https://mmg.whatsаpp.net/synthetic",
            "https://" + "a" * 64 + ".whatsapp.net/synthetic", b"https://mmg.whatsapp.net/bytes",
        ]
        pk = self.attach_url()
        for value in bad_urls:
            with self.subTest(value=value):
                self.writer.execute("UPDATE ZWAMEDIAITEM SET ZMEDIAURL=? WHERE Z_PK=?", (value, pk))
                self.writer.commit()
                result = self.repo.get_media_url(pk)
                self.assertEqual(result["reason"], "untrusted_cdn_url")
                self.assertFalse(result["available"])
                self.assertIsNone(result["cdn_url"])

    def test_url_size_boundary_and_oversized_field(self):
        self.add_url_columns()
        prefix = "https://mmg.whatsapp.net/"
        boundary = prefix + "a" * (MAX_CDN_URL_CHARS - len(prefix))
        pk = self.attach_url(url=boundary)
        self.assertEqual(self.repo.get_media_url(pk)["cdn_url"], boundary)
        self.writer.execute("UPDATE ZWAMEDIAITEM SET ZMEDIAURL=? WHERE Z_PK=?", (boundary + "a" * 100000, pk))
        self.writer.commit()
        result = self.repo.get_media_url(pk)
        self.assertEqual(result["reason"], "untrusted_cdn_url")
        self.assertIsNone(result["cdn_url"])

    def test_hidden_removed_and_orphaned_messages_never_expose_url(self):
        self.add_url_columns()
        self.fixture.insert("ZWACHATSESSION", Z_PK=4, ZCONTACTJID="removed@s.whatsapp.net",
                            ZPARTNERNAME="Removed synthetic", ZSESSIONTYPE=0, ZHIDDEN=0, ZREMOVED=1)
        for pk, chat_id in ((300, 3), (400, 4), (500, 999)):
            self.attach_url(pk=pk)
            self.writer.execute("UPDATE ZWAMESSAGE SET ZCHATSESSION=? WHERE Z_PK=?", (chat_id, pk))
        self.writer.commit()
        for pk in (300, 400, 500, 9999):
            with self.subTest(message_id=pk), self.assertRaisesRegex(RepositoryError, "not found locally"):
                self.repo.get_media_url(pk)

    def test_message_id_is_validated_without_interpolating_sql(self):
        self.add_url_columns()
        self.attach_url()
        for value in (True, 0, -1, 2**63, "99", "99 OR 1=1", "'; DROP TABLE ZWAMEDIAITEM; --"):
            with self.subTest(message_id=value), self.assertRaises(RepositoryError):
                self.repo.get_media_url(value)
        self.assertTrue(self.repo.get_media_url(99)["available"])

    def test_url_is_only_available_from_dedicated_message_scoped_call(self):
        self.add_url_columns()
        first = self.attach_url(pk=100, url=SYNTHETIC_URL)
        second_url = "https://mmg.whatsapp.net/another-synthetic.enc"
        self.attach_url(pk=101, url=second_url)
        self.assertEqual(self.repo.get_media_url(first)["cdn_url"], SYNTHETIC_URL)
        for result in (self.repo.get_message(first), self.repo.get_messages(1),
                       self.repo.list_chats(), self.repo.search_messages("match")):
            serialized = json.dumps(result)
            self.assertNotIn("cdn_url", serialized)
            self.assertNotIn(SYNTHETIC_URL, serialized)
            self.assertNotIn(second_url, serialized)

    def test_does_not_select_key_material_message_text_or_opaque_metadata(self):
        self.add_url_columns()
        self.writer.execute("ALTER TABLE ZWAMEDIAITEM ADD COLUMN ZMEDIAKEY BLOB")
        self.writer.execute("ALTER TABLE ZWAMEDIAITEM ADD COLUMN ZMETADATA BLOB")
        pk = self.attach_url()
        self.writer.execute("UPDATE ZWAMEDIAITEM SET ZMEDIAKEY=?,ZMETADATA=? WHERE Z_PK=?",
                            (b"SYNTHETIC-KEY-NOT-FOR-OUTPUT", b"SYNTHETIC-METADATA-NOT-FOR-OUTPUT", pk))
        self.writer.commit()
        real_connect = self.repo._connect
        statements = []
        @contextmanager
        def traced_connect():
            with real_connect() as connection:
                connection.set_trace_callback(statements.append)
                yield connection
        with patch.object(self.repo, "_connect", side_effect=traced_connect):
            result = self.repo.get_media_url(pk)
        selected = " ".join(sql.upper() for sql in statements if sql.lstrip().upper().startswith("SELECT"))
        for column in ("ZMEDIAKEY", "ZMETADATA", "ZTEXT"):
            self.assertNotIn(column, selected)
        serialized = json.dumps(result)
        self.assertNotIn("SYNTHETIC-KEY", serialized)
        self.assertNotIn("SYNTHETIC-METADATA", serialized)

    def test_url_and_date_use_one_database_snapshot(self):
        self.add_url_columns()
        pk = self.attach_url()
        real_connect = self.repo._connect
        @contextmanager
        def update_after_snapshot():
            with real_connect() as connection:
                self.writer.execute("UPDATE ZWAMEDIAITEM SET ZMEDIAURL=?,ZMEDIAURLDATE=20 WHERE Z_PK=?",
                                    ("https://mmg.whatsapp.net/changed-synthetic", pk))
                self.writer.commit()
                yield connection
        with patch.object(self.repo, "_connect", side_effect=update_after_snapshot):
            result = self.repo.get_media_url(pk)
        self.assertEqual(result["cdn_url"], SYNTHETIC_URL)
        self.assertEqual(result["source_url_date"], "2001-01-01T00:00:10.000Z")

    def test_returning_stored_metadata_performs_no_network_or_subprocess(self):
        self.add_url_columns()
        pk = self.attach_url()
        with patch("socket.socket", side_effect=AssertionError("No network is permitted")), \
                patch("subprocess.Popen", side_effect=AssertionError("No external fetch is permitted")):
            self.assertEqual(self.repo.get_media_url(pk)["cdn_url"], SYNTHETIC_URL)

    def test_invalid_date_is_not_misrepresented_as_expiry(self):
        self.add_url_columns()
        pk = self.attach_url(date="not-a-timestamp")
        result = self.repo.get_media_url(pk)
        self.assertTrue(result["available"])
        self.assertIsNone(result["source_url_date"])
        self.assertEqual(result["expiry"], "unverified")

    def test_read_does_not_modify_database_or_wal(self):
        self.add_url_columns()
        pk = self.attach_url()
        paths = (self.fixture.db, self.fixture.db.with_name(self.fixture.db.name + "-wal"))
        before = [(hashlib.sha256(path.read_bytes()).digest(), path.stat().st_mtime_ns) for path in paths]
        self.repo.get_media_url(pk)
        after = [(hashlib.sha256(path.read_bytes()).digest(), path.stat().st_mtime_ns) for path in paths]
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
