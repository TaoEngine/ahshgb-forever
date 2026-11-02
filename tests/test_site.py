# -*- coding: utf-8 -*-
"""ahshgb.site 的单元测试：全部合成数据、不联网、不写真实站点目录。

临时目录放在仓库 ``.tmp/`` 下（已被 .gitignore 忽略）：CI 与本地沙箱都允许写入，
且测试结束会自行清理。
"""

from __future__ import annotations

import hashlib
import shutil
import unittest
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

from ahshgb.config import COVER_URL, DEFAULT_TITLE
from ahshgb.site import (
    ITUNES_NS,
    MIME_BY_EXT,
    album_hash,
    build_site,
    feed_filename,
    mime_for_url,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
TMP_ROOT = REPO_ROOT / ".tmp"

TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 11, 2, 20, 30, 0, tzinfo=TZ)
BASE = "https://taoengine.github.io/ahshgb-forever"

ALBUM_A = "我们·约吧"
# 故意带上中文引号与英文双引号：它们绝不能出现在 feed 文件名里
ALBUM_B = '「心花一路FUN」"周末版"'

URL_A_2100 = "https://cdn.example.com/a/20261102_2100.m4a"
URL_A_0900 = "https://cdn.example.com/a/20261102_0900.m4a"
URL_A_1101 = "https://cdn.example.com/a/20261101_1200.mp3"
URL_B_M3U8 = "https://play.example.com/live/20261031.m3u8"
URL_B_1830 = "https://cdn.example.com/b/20261031_1830.m4a"
URL_B_M4B = "https://cdn.example.com/b/20261030_1830.m4b"


def itunes(tag):
    """itunes 命名空间下的完整标签名。"""
    return "{%s}%s" % (ITUNES_NS, tag)


def sample_episodes():
    """两个节目、7 条输入 / 6 条唯一 episode（其中 1 条完全重复）。"""
    return [
        # A：同一天两场，小时已知 → 两条标题都要追加 HH:MM
        {
            "album": ALBUM_A,
            "date": "20261102",
            "hour": "0900",
            "url": URL_A_0900,
            "source": "ajmide",
            "size_mb": 12.5,
        },
        {
            "album": ALBUM_A,
            "date": "20261102",
            "hour": "2100",
            "url": URL_A_2100,
            "source": "ajmide",
            "size_mb": 12.5,
        },
        # A：单独一天 → 不追加时刻；无 size_mb → length 0
        {
            "album": ALBUM_A,
            "date": "20261101",
            "hour": "1200",
            "url": URL_A_1101,
            "source": "ahrtv",
        },
        # B：同一天一条 hour 未知、一条 hour 已知 → 只有后者追加 HH:MM
        {
            "album": ALBUM_B,
            "date": "20261031",
            "hour": "",
            "url": URL_B_M3U8,
            "source": "ajmide",
        },
        {
            "album": ALBUM_B,
            "date": "20261031",
            "hour": "1830",
            "url": URL_B_1830,
            "source": "ajmide",
            "size_mb": 25.0,
        },
        # B：自定义标题（单独一天 → 不加后缀）
        {
            "album": ALBUM_B,
            "date": "20261030",
            "hour": "1830",
            "url": URL_B_M4B,
            "source": "ahrtv",
            "size_mb": 3.0,
            "title": "特别节目·城市之光",
        },
        # 完全重复（同 album + url）→ 应被去重
        {
            "album": ALBUM_B,
            "date": "20261031",
            "hour": "",
            "url": URL_B_M3U8,
            "source": "ajmide",
        },
    ]


class _SiteTestCase(unittest.TestCase):
    """提供临时输出目录的公共基类。"""

    def make_out_dir(self):
        TMP_ROOT.mkdir(parents=True, exist_ok=True)
        root = TMP_ROOT / ("site-test-" + uuid.uuid4().hex)
        root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        return root / "site"


class BuildSiteTest(_SiteTestCase):
    """用同一份 fixture 构建一次，多个用例只读检查产物。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        TMP_ROOT.mkdir(parents=True, exist_ok=True)
        cls._root = TMP_ROOT / ("site-test-" + uuid.uuid4().hex)
        cls._root.mkdir(parents=True)
        cls.out = cls._root / "site"
        cls.result = build_site(sample_episodes(), cls.out, BASE, now=NOW)
        cls.feeds = {feed["album"]: feed for feed in cls.result["feeds"]}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._root, ignore_errors=True)
        super().tearDownClass()

    # ------------------------------------------------------------ 小工具
    def feed_root(self, album):
        return ET.parse(self.out / "feeds" / feed_filename(album)).getroot()

    def channel(self, album):
        channel = self.feed_root(album).find("channel")
        self.assertIsNotNone(channel)
        return channel

    def items(self, album):
        return self.channel(album).findall("item")

    def titles(self, album):
        return [item.findtext("title") for item in self.items(album)]

    # ------------------------------------------------------------ 目录与计数
    def test_counts_and_layout(self):
        self.assertEqual(self.result["albums"], 2)
        self.assertEqual(self.result["episodes"], 6)  # 7 条输入，去重后 6 条
        self.assertEqual(
            [feed["album"] for feed in self.result["feeds"]],
            sorted([ALBUM_A, ALBUM_B]),
        )
        self.assertEqual(
            {feed["album"]: feed["episodes"] for feed in self.result["feeds"]},
            {ALBUM_A: 3, ALBUM_B: 3},
        )
        self.assertEqual(
            sum(feed["episodes"] for feed in self.result["feeds"]), 6
        )

        self.assertTrue((self.out / "ahshgb.opml").is_file())
        nojekyll = self.out / ".nojekyll"
        self.assertTrue(nojekyll.is_file())
        self.assertEqual(nojekyll.read_bytes(), b"")
        self.assertEqual(len(list((self.out / "feeds").glob("*.xml"))), 2)
        self.assertEqual(self.result["opml"], str(self.out / "ahshgb.opml"))

        for feed in self.result["feeds"]:
            path = Path(feed["path"])
            self.assertTrue(path.is_file(), path)
            self.assertEqual(feed["bytes"], path.stat().st_size)
            self.assertGreater(feed["bytes"], 0)
            self.assertEqual(path.parent, self.out / "feeds")

    # ------------------------------------------------------------ 文件名
    def test_feed_filenames_are_stable_ascii_and_safe(self):
        for album in (ALBUM_A, ALBUM_B):
            expected = hashlib.sha1(album.encode("utf-8")).hexdigest()[:12] + ".xml"
            self.assertEqual(feed_filename(album), expected)
            self.assertEqual(album_hash(album), expected[: -len(".xml")])
            self.assertRegex(feed_filename(album), r"^[0-9a-f]{12}\.xml$")
            self.assertTrue(feed_filename(album).isascii())
            for bad in '\\/:*?"<>|':
                self.assertNotIn(bad, feed_filename(album))
            self.assertTrue((self.out / "feeds" / expected).is_file())

        # 换一个输出目录重新生成，文件名必须完全一致
        other = build_site(sample_episodes(), self.make_out_dir(), BASE, now=NOW)
        self.assertEqual(
            {feed["album"]: Path(feed["path"]).name for feed in other["feeds"]},
            {feed["album"]: Path(feed["path"]).name for feed in self.result["feeds"]},
        )

    # ------------------------------------------------------------ RSS 头
    def test_rss_channel_metadata(self):
        for album in (ALBUM_A, ALBUM_B):
            root = self.feed_root(album)
            self.assertEqual(root.tag, "rss")
            self.assertEqual(root.get("version"), "2.0")

            channel = self.channel(album)
            self.assertEqual(channel.findtext("title"), album)
            self.assertEqual(
                channel.findtext("link"),
                "%s/feeds/%s" % (BASE, feed_filename(album)),
            )
            self.assertIn(album, channel.findtext("description"))
            self.assertEqual(channel.findtext("language"), "zh-cn")
            self.assertEqual(channel.findtext("lastBuildDate"), format_datetime(NOW))
            self.assertIn("+0800", channel.findtext("lastBuildDate"))
            self.assertEqual(
                channel.findtext(itunes("author")), "安徽生活广播(安徽城市之声)"
            )
            image = channel.find(itunes("image"))
            self.assertIsNotNone(image)
            self.assertEqual(image.get("href"), COVER_URL)
            self.assertEqual(channel.findtext(itunes("explicit")), "false")

    # ------------------------------------------------------------ 排序 / 标题
    def test_items_are_sorted_newest_first(self):
        for album in (ALBUM_A, ALBUM_B):
            times = [
                parsedate_to_datetime(item.findtext("pubDate"))
                for item in self.items(album)
            ]
            self.assertEqual(times, sorted(times, reverse=True))

        self.assertEqual(
            [item.findtext("pubDate") for item in self.items(ALBUM_A)],
            [
                format_datetime(datetime(2026, 11, 2, 21, 0, tzinfo=TZ)),
                format_datetime(datetime(2026, 11, 2, 9, 0, tzinfo=TZ)),
                format_datetime(datetime(2026, 11, 1, 12, 0, tzinfo=TZ)),
            ],
        )

    def test_duplicate_dates_get_hour_suffix(self):
        self.assertEqual(
            self.titles(ALBUM_A),
            [
                "%s-2026-11-02 21:00" % ALBUM_A,
                "%s-2026-11-02 09:00" % ALBUM_A,
                "%s-2026-11-01" % ALBUM_A,  # 当天仅一条 → 不加时刻
            ],
        )
        self.assertEqual(
            self.titles(ALBUM_B),
            [
                '%s-2026-10-31 18:30' % ALBUM_B,
                "%s-2026-10-31" % ALBUM_B,  # hour 未知 → 即使同日多条也不加
                "特别节目·城市之光",  # 自定义 title 优先
            ],
        )

    # ------------------------------------------------------------ pubDate
    def test_pubdate_is_beijing_time(self):
        first = self.items(ALBUM_A)[0]
        when = parsedate_to_datetime(first.findtext("pubDate"))
        self.assertEqual(when, datetime(2026, 11, 2, 21, 0, tzinfo=TZ))
        self.assertEqual(when.utcoffset(), timedelta(hours=8))
        self.assertTrue(first.findtext("pubDate").endswith("+0800"))

        # hour 未知 → 12:00 兜底
        no_hour = [
            item
            for item in self.items(ALBUM_B)
            if item.findtext("title").endswith("-2026-10-31")
        ]
        self.assertEqual(len(no_hour), 1)
        self.assertEqual(
            parsedate_to_datetime(no_hour[0].findtext("pubDate")),
            datetime(2026, 10, 31, 12, 0, tzinfo=TZ),
        )

    # ------------------------------------------------------------ enclosure / guid
    def test_enclosure_mime_length_and_guid(self):
        enclosures = {
            item.find("enclosure").get("url"): item.find("enclosure")
            for item in self.items(ALBUM_A) + self.items(ALBUM_B)
        }
        self.assertEqual(enclosures[URL_A_2100].get("type"), "audio/mp4")
        self.assertEqual(
            enclosures[URL_A_2100].get("length"), str(round(12.5 * 1048576))
        )
        self.assertEqual(enclosures[URL_A_1101].get("type"), "audio/mpeg")
        self.assertEqual(enclosures[URL_A_1101].get("length"), "0")
        self.assertEqual(
            enclosures[URL_B_M3U8].get("type"), "application/vnd.apple.mpegurl"
        )
        self.assertEqual(enclosures[URL_B_M3U8].get("length"), "0")
        self.assertEqual(enclosures[URL_B_1830].get("type"), "audio/mp4")
        self.assertEqual(
            enclosures[URL_B_1830].get("length"), str(round(25.0 * 1048576))
        )
        self.assertEqual(enclosures[URL_B_M4B].get("type"), "audio/mp4")

        for album in (ALBUM_A, ALBUM_B):
            for item in self.items(album):
                guid = item.find("guid")
                self.assertIsNotNone(guid)
                self.assertEqual(guid.get("isPermaLink"), "false")
                self.assertEqual(guid.text, item.find("enclosure").get("url"))

    def test_mime_for_url_table(self):
        self.assertEqual(mime_for_url("https://x/a.m4a"), "audio/mp4")
        self.assertEqual(mime_for_url("https://x/a.m4b"), "audio/mp4")
        self.assertEqual(mime_for_url("https://x/a.MP3"), "audio/mpeg")
        self.assertEqual(mime_for_url("https://x/a.m3u8"), "application/vnd.apple.mpegurl")
        self.assertEqual(mime_for_url("https://x/a.aac"), "audio/aac")
        self.assertEqual(mime_for_url("https://x/a.ogg"), "audio/ogg")
        self.assertEqual(mime_for_url("https://x/a.flac"), "audio/mpeg")
        self.assertEqual(mime_for_url("https://x/a.m4a?token=abc"), "audio/mp4")
        self.assertEqual(mime_for_url(""), "audio/mpeg")
        self.assertEqual(MIME_BY_EXT[".m3u8"], "application/vnd.apple.mpegurl")

    # ------------------------------------------------------------ OPML
    def test_opml_structure_and_absolute_urls(self):
        root = ET.parse(self.result["opml"]).getroot()
        self.assertEqual(root.tag, "opml")
        self.assertEqual(root.get("version"), "2.0")

        head = root.find("head")
        self.assertIsNotNone(head)
        self.assertEqual(head.findtext("title"), DEFAULT_TITLE)
        self.assertEqual(head.findtext("ownerName"), "ahshgb-forever")
        self.assertEqual(head.findtext("dateCreated"), format_datetime(NOW))
        self.assertTrue(head.findtext("dateCreated").endswith("+0800"))

        body = root.find("body")
        self.assertIsNotNone(body)
        folders = body.findall("outline")
        self.assertEqual(
            [folder.get("text") for folder in folders], sorted([ALBUM_A, ALBUM_B])
        )
        for folder in folders:
            album = folder.get("text")
            rss_outlines = folder.findall("outline")
            self.assertEqual(len(rss_outlines), 1)
            node = rss_outlines[0]
            self.assertEqual(node.get("type"), "rss")
            self.assertEqual(node.get("text"), album)

            xml_url = node.get("xmlUrl")
            parts = urlsplit(xml_url)
            self.assertEqual(parts.scheme, "https")  # 绝对地址，不是相对路径
            self.assertEqual(parts.netloc, "taoengine.github.io")
            self.assertTrue(xml_url.startswith(BASE + "/feeds/"))
            expected = "%s/feeds/%s" % (BASE, feed_filename(album))
            self.assertEqual(xml_url, expected)
            self.assertTrue((self.out / "feeds" / Path(xml_url).name).is_file())

    # ------------------------------------------------------------ 去重 / 健壮性
    def test_dedupe_and_invalid_entries_are_skipped(self):
        episodes = [
            {
                "album": ALBUM_A,
                "date": "20261102",
                "hour": "0900",
                "url": URL_A_0900,
                "source": "ajmide",
            },
            # 同 album + 同 url，只换 source → 仍算重复
            {
                "album": ALBUM_A,
                "date": "20261102",
                "hour": "0900",
                "url": URL_A_0900,
                "source": "ahrtv",
            },
            # 坏日期 → 跳过
            {"album": ALBUM_A, "date": "not-a-date", "hour": "0900", "url": URL_A_1101},
            # 缺 album / 缺 url / 非 dict → 跳过
            {"album": "", "date": "20261102", "hour": "", "url": URL_A_2100},
            {"album": ALBUM_A, "date": "20261102", "hour": "", "url": ""},
            "not-a-dict",
        ]
        result = build_site(episodes, self.make_out_dir(), BASE, now=NOW)
        self.assertEqual(result["albums"], 1)
        self.assertEqual(result["episodes"], 1)
        self.assertEqual(result["feeds"][0]["episodes"], 1)

    # ------------------------------------------------------------ 参数注入
    def test_custom_title_cover_and_base_url_normalization(self):
        out = self.make_out_dir()
        result = build_site(
            sample_episodes(),
            out,
            BASE + "///",
            title="安徽生活广播 · 测试站",
            cover="https://img.example.com/cover.png",
            now=NOW,
        )
        opml_root = ET.parse(result["opml"]).getroot()
        self.assertEqual(opml_root.find("head").findtext("title"), "安徽生活广播 · 测试站")
        for feed in result["feeds"]:
            channel = ET.parse(Path(feed["path"])).getroot().find("channel")
            self.assertEqual(
                channel.find(itunes("image")).get("href"),
                "https://img.example.com/cover.png",
            )
            self.assertNotIn("//feeds", channel.findtext("link"))
            self.assertTrue(channel.findtext("link").startswith(BASE + "/feeds/"))

    def test_output_is_deterministic_regardless_of_input_order(self):
        other_out = self.make_out_dir()
        other = build_site(list(reversed(sample_episodes())), other_out, BASE, now=NOW)
        self.assertEqual(
            (other_out / "ahshgb.opml").read_bytes(),
            (self.out / "ahshgb.opml").read_bytes(),
        )
        mine = {f["album"]: Path(f["path"]).read_bytes() for f in self.result["feeds"]}
        theirs = {f["album"]: Path(f["path"]).read_bytes() for f in other["feeds"]}
        self.assertEqual(mine, theirs)

    # ------------------------------------------------------------ 边界情况
    def test_empty_episodes_still_creates_valid_site(self):
        out = self.make_out_dir()
        result = build_site([], out, BASE, now=NOW)
        self.assertEqual(result["albums"], 0)
        self.assertEqual(result["episodes"], 0)
        self.assertEqual(result["feeds"], [])
        self.assertTrue(Path(result["opml"]).is_file())
        self.assertTrue((out / ".nojekyll").is_file())
        root = ET.parse(result["opml"]).getroot()
        self.assertEqual(root.find("body").findall("outline"), [])

    def test_stale_generated_feeds_are_pruned_but_other_files_kept(self):
        out = self.make_out_dir()
        build_site(
            [
                {
                    "album": ALBUM_A,
                    "date": "20261102",
                    "hour": "0900",
                    "url": URL_A_0900,
                    "source": "ajmide",
                }
            ],
            out,
            BASE,
            now=NOW,
        )
        stale = out / "feeds" / feed_filename(ALBUM_A)
        self.assertTrue(stale.is_file())

        keep = out / "feeds" / "notes.xml"  # 不是本模块的命名，不能被误删
        keep.write_text("keep me", encoding="utf-8")

        result = build_site(
            [
                {
                    "album": ALBUM_B,
                    "date": "20261101",
                    "hour": "1200",
                    "url": URL_B_1830,
                    "source": "ajmide",
                }
            ],
            out,
            BASE,
            now=NOW,
        )
        self.assertFalse(stale.exists())
        self.assertTrue(keep.is_file())
        self.assertEqual(result["albums"], 1)
        self.assertTrue(
            (out / "feeds" / feed_filename(ALBUM_B)).is_file()
        )


if __name__ == "__main__":
    unittest.main()
