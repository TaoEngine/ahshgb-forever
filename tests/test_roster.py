# -*- coding: utf-8 -*-
"""roster 模块单元测试（排期解析 / 映射）。"""
import unittest

from ahshgb import roster


class HourTest(unittest.TestCase):
    def test_hour_int(self):
        self.assertEqual(roster.hour_int("1700"), 17)
        self.assertEqual(roster.hour_int("17"), 17)
        self.assertEqual(roster.hour_int(7), 7)
        self.assertIsNone(roster.hour_int(""))
        self.assertIsNone(roster.hour_int("bad"))


class ScheduleTest(unittest.TestCase):
    def test_parse_days(self):
        self.assertEqual(roster.parse_days("1-5"), {1, 2, 3, 4, 5})
        self.assertEqual(roster.parse_days("1-4,6,7"), {1, 2, 3, 4, 6, 7})
        self.assertEqual(roster.parse_days("5"), {5})
        self.assertEqual(roster.parse_days(""), set())

    def test_parse_schedule(self):
        segs = roster.parse_schedule("17:00-19:00(1-5)")
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0]["start"], 17)
        self.assertEqual(segs[0]["days"], {1, 2, 3, 4, 5})

    def test_weekdays_for_hour(self):
        live = ["12:00-15:00(1-7),20:00-21:00(1-7),22:00-23:59(6,7)"]
        self.assertEqual(roster.weekdays_for_hour(live, 12), set(range(1, 8)))
        self.assertEqual(roster.weekdays_for_hour(live, 22), {6, 7})
        self.assertIsNone(roster.weekdays_for_hour(["已停播"], 17))
        self.assertIsNone(roster.weekdays_for_hour([], 17))

    def test_weekdays_for_hour_unknown_hour(self):
        live = ["07:00-09:00(1-5)"]
        self.assertEqual(roster.weekdays_for_hour(live, 7), {1, 2, 3, 4, 5})
        self.assertIsNone(roster.weekdays_for_hour(live, 20))


class WeekdayInferenceTest(unittest.TestCase):
    def test_weekdays_from_dates(self):
        dates = ["20260921", "20260922", "20260923"]      # 周一~周三
        self.assertEqual(roster.weekdays_from_dates(dates), {1, 2, 3})
        self.assertIsNone(roster.weekdays_from_dates([]))

    def test_trap_aware_2019_weekdays_only(self):
        dates = ["20260105", "20260106", "20260107", "20260108", "20260109"]
        # 2026-01-05 是周一，只有工作日命中 -> 判为工作日节目
        self.assertEqual(roster.trap_aware_weekdays(dates), {1, 2, 3, 4, 5})

    def test_declared_wins(self):
        self.assertEqual(
            roster.trap_aware_weekdays(["20260103"], declared={6, 7}), {6, 7})


class AlbumMapTest(unittest.TestCase):
    def test_roster_wins_over_programs(self):
        roster_data = [{"album": "老名字", "buckets": [{"bucket": "c_1"}]}]
        programs = {"c_1": {"album": "新名字"}, "c_2": {"album": "新节目"}}
        self.assertEqual(roster.album_map(roster_data, programs),
                         {"c_1": "老名字", "c_2": "新节目"})


if __name__ == "__main__":
    unittest.main()
