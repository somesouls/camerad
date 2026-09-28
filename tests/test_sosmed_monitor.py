import os
import tempfile
import unittest

import sosmed.db as sdb
import sosmed.monitor as smon


class SosmedMonitorPostTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sdb.init_db(sdb.connect(os.path.join(self.tmp.name, "sosmed.db")))
        items = [
            {"platform":"ig","id":"i1","conversation_id":"post-video","author_handle":"a",
             "created_at":"2026-01-12T10:00:00Z","text":"tanya",
             "post_created_at":"2026-01-10T08:00:00Z","post_type":"Video"},
            {"platform":"ig","id":"i2","conversation_id":"post-carousel","author_handle":"b",
             "created_at":"2026-01-20T10:00:00Z","text":"tanya dua",
             "post_created_at":"2026-01-18T08:00:00Z","post_type":"Carousel"},
            {"platform":"x","id":"x1","conversation_id":"unknown-a","author_handle":"c",
             "created_at":"2026-02-01T10:00:00Z","text":"x satu"},
            {"platform":"x","id":"x2","conversation_id":"unknown-b","author_handle":"d",
             "created_at":"2026-02-03T10:00:00Z","text":"x dua"},
        ]
        sdb.ingest_items(self.conn, items, source="test")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_auto_labels_and_post_date(self):
        result = smon.monitor_posts(self.conn)
        posts = {p["conversation_id"]: p for p in result["posts"]}
        self.assertEqual(posts["post-video"]["post_label"], "Januari Video")
        self.assertEqual(posts["post-carousel"]["post_label"], "Januari Carousel")
        self.assertEqual(posts["post-video"]["post_date"][:10], "2026-01-10")
        self.assertEqual(posts["unknown-a"]["post_label"], "Februari A")
        self.assertEqual(posts["unknown-b"]["post_label"], "Februari B")
        self.assertEqual(posts["unknown-a"]["post_date_source"], "komentar_pertama")

    def test_manual_label_is_not_overwritten(self):
        smon.set_post_label(self.conn, "ig", "post-video", "Kampanye EFIN")
        smon.ensure_auto_post_labels(self.conn)
        self.assertEqual(smon.get_post_label(self.conn, "ig", "post-video"), "Kampanye EFIN")

    def test_export_returns_every_database_column(self):
        out = smon.monitor_post_export(self.conn, "ig", "post-video")
        expected = [r[1] for r in self.conn.execute('PRAGMA table_info("sosmed_items")')]
        self.assertTrue(out["ok"])
        self.assertEqual(out["columns"], expected)
        self.assertEqual(set(out["items"][0]), set(expected))
        self.assertIn("raw_json", out["columns"])
        self.assertIn("spv_note", out["columns"])


if __name__ == "__main__":
    unittest.main()
