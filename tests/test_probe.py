# -*- coding: utf-8 -*-
"""probe 模块单元测试（不发网络请求）。"""
import datetime as dt
import os
import tempfile
import unittest

from ahshgb import probe

_TMP_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".tmp")


def _mkdtemp():
    # 不用 tempfile.mkdtemp：DSH 沙箱下它以 0700 建目录后进程反而无法写入
    import uuid
    os.makedirs(_TMP_ROOT, exist_ok=True)
    path = os.path.join(_TMP_ROOT, "test-probe-" + uuid.uuid4().hex)
    os.makedirs(path)
    return path


class UrlTest(unittest.TestCase):
    def test_url_of_and_parse_roundtrip(self):
        url = probe.url_of("c_8894", "2026-09-21", 17)
        self.assertEqual(
            url, "http://ia-bk-i.ajmide.com/c_8894/20260921/8894_20260921_1700.m4a")
        self.assertEqual(probe.parse_url(url), ("c_8894", "20260921", 17))

    def test_parse_url_rejects_other(self):
        self.assertIsNone(probe.parse_url("https://example.com/a.mp3"))
        self.assertIsNone(probe.parse_url(""))


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = type('T', (), {'name': _mkdtemp()})()
        self.path = os.path.join(self.tmp.name, "probe_cache.csv")

    def tearDown(self):
        pass

    def test_put_get_and_roundtrip_keeps_order(self):
        cache = probe.ProbeCache(self.path)
        cache.put("c_1001", "20260101", 7, 200, 10 * 1048576)
        cache.put("c_1002", "20260102", 8, 404, 0)
        cache.put("c_1001", "20260101", 7, 403, 0)      # 原地更新
        cache.put("c_1003", "20260103", 9, 200, 5 * 1048576)   # 追加
        cache.save()

        again = probe.ProbeCache(self.path)
        self.assertEqual(len(again), 3)
        self.assertEqual([row[0] for row in again.rows],
                         ["c_1001", "c_1002", "c_1003"])
        self.assertEqual(again.get("c_1001", "2026-01-01", 7)[0], 403)

    def test_do_not_overwrite_size_with_zero(self):
        cache = probe.ProbeCache(self.path)
        cache.put("c_1001", "20260101", 7, 200, 10 * 1048576)
        cache.put("c_1001", "20260101", 7, 200, 0)
        self.assertAlmostEqual(cache.get("c_1001", "20260101", 7)[1], 10.0, places=2)

    def test_duplicate_rows_last_wins(self):
        with open(self.path, "w", encoding="utf-8", newline="") as fh:
            fh.write("bucket,date,hour,status,size_mb,checked_at,source\n")
            fh.write("c_1,20260101,3,404,0,,probe\n")
            fh.write("c_1,20260101,3,200,1.5,,probe\n")
        cache = probe.ProbeCache(self.path)
        self.assertEqual(len(cache), 1)
        self.assertEqual(cache.get("c_1", "20260101", 3)[0], 200)

    def test_invalid_date_rows_skipped(self):
        with open(self.path, "w", encoding="utf-8", newline="") as fh:
            fh.write("bucket,date,hour,status,size_mb,checked_at,source\n")
            fh.write("c_1,20261332,3,200,1.0,,probe\n")      # 2026-13-32 非法
            fh.write("c_1,20260101,3,200,1.0,,probe\n")
        cache = probe.ProbeCache(self.path)
        self.assertEqual(len(cache), 1)
        self.assertEqual(cache.invalid, 1)

    def test_hits_iterator(self):
        cache = probe.ProbeCache(self.path)
        cache.put("c_1", "20260101", 3, 200, 1048576)
        cache.put("c_1", "20260102", 3, 404, 0)
        cache.put("c_1", "20260103", 3, 403, 0)
        cache.put("c_1", "20260104", 3, 206, 2097152)
        self.assertEqual([h[1] for h in cache.hits()], ["20260101", "20260104"])


class NeedsProbeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = type('T', (), {'name': _mkdtemp()})()
        self.cache = probe.ProbeCache(os.path.join(self.tmp.name, "c.csv"))
        self.today = dt.date(2026, 11, 2)

    def tearDown(self):
        pass

    def test_missing_always(self):
        self.assertTrue(probe.needs_probe(
            self.cache, "c_1", "2026-10-01", 17, today=self.today))

    def test_hit_never(self):
        self.cache.put("c_1", "2020-01-01", 17, 200, 1048576)
        self.assertFalse(probe.needs_probe(
            self.cache, "c_1", "2020-01-01", 17, today=self.today))

    def test_recent_miss_rechecked(self):
        self.cache.put("c_1", "2026-11-01", 17, 404, 0,
                       when="2026-11-01 09:00:00")
        self.assertTrue(probe.needs_probe(
            self.cache, "c_1", "2026-11-01", 17, today=self.today))

    def test_old_fresh_miss_skipped(self):
        self.cache.put("c_1", "2026-08-01", 17, 404, 0,
                       when="2026-11-01 09:00:00")
        self.assertFalse(probe.needs_probe(
            self.cache, "c_1", "2026-08-01", 17, today=self.today))

    def test_expired_miss_rechecked(self):
        self.cache.put("c_1", "2026-08-01", 17, 404, 0,
                       when="2026-09-01 09:00:00")
        self.assertTrue(probe.needs_probe(
            self.cache, "c_1", "2026-08-01", 17, today=self.today))


if __name__ == "__main__":
    unittest.main()

