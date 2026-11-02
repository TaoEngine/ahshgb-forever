# -*- coding: utf-8 -*-
"""update 模块单元测试（dry-run，不发网络请求）。"""
import datetime as dt
import json
import os
import unittest

from ahshgb import config, update

_TMP_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".tmp")


def _mkdtemp():
    import uuid
    os.makedirs(_TMP_ROOT, exist_ok=True)
    path = os.path.join(_TMP_ROOT, "test-update-" + uuid.uuid4().hex)
    os.makedirs(path)
    return path


class UpdateDryRunTest(unittest.TestCase):
    def setUp(self):
        self.dir = _mkdtemp()
        with open(os.path.join(self.dir, config.PROBE_CACHE_NAME), "w",
                  encoding="utf-8-sig", newline="") as fh:
            fh.write("bucket,date,hour,status,size_mb,checked_at,source\n")
            fh.write("c_1001,20260105,17,200,10.0,2026-10-01 00:00:00,probe\n")
        roster = [{"album": "测试节目", "buckets": [
            {"bucket": "c_1001", "hours": ["1700"],
             "liveTime": ["17:00-19:00(1-5)"]}]}]
        with open(os.path.join(self.dir, config.ROSTER_NAME), "w",
                  encoding="utf-8") as fh:
            json.dump(roster, fh, ensure_ascii=False)

    def test_dry_run_plans_weekdays_only(self):
        stats = update.run(self.dir, days=14, dry_run=True, use_api=False)
        self.assertEqual(stats["probed"], 0)
        self.assertEqual(stats["hits"], 0)
        # 14 天窗口里恰好 10 个工作日；缓存里已有的命中（2026-01-05）不在窗口内
        self.assertEqual(stats["points"], 10)
        self.assertEqual(stats["start"], (config.today_beijing()
                                          - dt.timedelta(days=14)).isoformat())
        self.assertEqual(stats["end"], (config.today_beijing()
                                        - dt.timedelta(days=1)).isoformat())

    def test_missing_cache_raises(self):
        empty = _mkdtemp()
        with self.assertRaises(FileNotFoundError):
            update.run(empty, days=7, dry_run=True, use_api=False)


if __name__ == "__main__":
    unittest.main()
