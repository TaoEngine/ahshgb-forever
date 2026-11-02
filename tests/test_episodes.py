# -*- coding: utf-8 -*-
"""episodes 模块单元测试：可播筛选 / 周末档陷阱 / ahrtv 优先（不发网络）。"""
import datetime as dt
import os
import tempfile
import unittest

from ahshgb import episodes, probe

_TMP_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".tmp")


def _mkdtemp():
    # 不用 tempfile.mkdtemp：DSH 沙箱下它以 0700 建目录后进程反而无法写入
    import uuid
    os.makedirs(_TMP_ROOT, exist_ok=True)
    path = os.path.join(_TMP_ROOT, "test-episodes-" + uuid.uuid4().hex)
    os.makedirs(path)
    return path


def _make_roster():
    return [
        {"album": "测试节目",
         "buckets": [{"bucket": "c_9001", "hours": ["1700"],
                      "liveTime": ["17:00-19:00(1-5)"]}]},
        {"album": "走进挂牌企业",
         "buckets": [{"bucket": "c_9002", "hours": ["1600"],
                      "liveTime": ["16:00-17:00(5)"]}]},
        {"album": "ahrtv节目",
         "buckets": [{"bucket": "c_9003", "hours": ["1200"],
                      "liveTime": ["12:00-13:00(1-7)"]}]},
    ]


class CollectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = type('T', (), {'name': _mkdtemp()})()
        self.cache = probe.ProbeCache(os.path.join(self.tmp.name, "probe_cache.csv"))
        # 测试节目：20 个周一命中（工作日）+ 一个周末命中（周末档陷阱应被丢弃）
        monday = dt.date(2026, 1, 5)
        for i in range(20):
            self.cache.put("c_9001", (monday + dt.timedelta(days=7 * i)).strftime("%Y%m%d"),
                           17, 200, 20 * 1048576)
        self.cache.put("c_9001", "20260920", 17, 200, 20 * 1048576)
        # 走进挂牌企业：周四 + 周五，只应保留周五
        self.cache.put("c_9002", "20260924", 16, 200, 10 * 1048576)
        self.cache.put("c_9002", "20260925", 16, 200, 10 * 1048576)
        # ahrtv节目：2021-01-01 阿基米德与 ahrtv 都有 -> 保留 ahrtv；
        # 2023 年阿基米德命中 -> 保留
        self.cache.put("c_9003", "20210101", 12, 200, 30 * 1048576)
        self.cache.put("c_9003", "20230504", 12, 200, 30 * 1048576)
        self.cache.put("c_9003", "20230505", 12, 404, 0)   # 404 不进清单

    def tearDown(self):
        pass

    def _collect(self):
        ahrtv = [{"album": "ahrtv节目", "date": "2021-01-01", "status": 200,
                  "url": "https://vod.ahtv.cn/audio/2021/01/01/a.mp3"},
                 {"album": "ahrtv节目", "date": "2021-01-07", "status": 200,
                  "url": "https://vod.ahtv.cn/audio/2021/01/07/b.mp3"}]
        return episodes.collect(self.cache, _make_roster(), {}, ahrtv)

    def test_weekend_trap_dropped(self):
        rows, stats = self._collect()
        dates = {r["date"] for r in rows if r["album"] == "测试节目"}
        self.assertNotIn("20260920", dates)
        self.assertIn("20260105", dates)
        self.assertGreaterEqual(stats["dropped"].get("周末档陷阱", 0), 1)

    def test_declared_weekday_filter(self):
        rows, _stats = self._collect()
        dates = {r["date"] for r in rows if r["album"] == "走进挂牌企业"}
        self.assertEqual(dates, {"20260925"})

    def test_ahrtv_preferred_in_window(self):
        rows, stats = self._collect()
        got = [(r["source"], r["date"]) for r in rows if r["album"] == "ahrtv节目"]
        self.assertIn(("ahrtv", "20210101"), got)
        self.assertIn(("ajmide", "20230504"), got)
        self.assertNotIn(("ajmide", "20210101"), got)
        self.assertEqual(stats["by_source"].get("ahrtv"), 2)

    def test_episode_fields(self):
        rows, _stats = self._collect()
        aj = next(r for r in rows
                  if r["album"] == "测试节目" and r["date"] == "20260105")
        self.assertEqual(aj["hour"], "1700")
        self.assertEqual(aj["url"],
                         "http://ia-bk-i.ajmide.com/c_9001/20260105/9001_20260105_1700.m4a")
        self.assertAlmostEqual(aj["size_mb"], 20.0, places=2)

    def test_404_excluded(self):
        rows, _stats = self._collect()
        self.assertFalse(any(r["date"] == "20230505" for r in rows))


if __name__ == "__main__":
    unittest.main()

