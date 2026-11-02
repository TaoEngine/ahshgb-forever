# -*- coding: utf-8 -*-
"""把合并好的 episode 列表发布成播客站点：每节目一个 RSS 2.0 + 一个 OPML 索引。

本模块只负责发布链路的最后一步（不联网、不读缓存）：输入是上游
:mod:`ahshgb.episodes` 产出的 episode 字典列表，输出是可以直接交给 GitHub
Pages / 任意静态托管的目录：

.. code-block:: text

    <out_dir>/
    ├── .nojekyll                      # 关掉 Pages 的 Jekyll 处理
    ├── ahshgb.opml                    # AntennaPod 可直接导入的订阅清单
    └── feeds/
        ├── <sha1(album)[:12]>.xml     # 每个节目一个 RSS
        └── ...

设计约束（与每晚定时生成的任务契合）：

* 纯标准库、不做任何网络请求；
* RSS 文件名只由 ``sha1(album)`` 决定：稳定、纯 ASCII、对 Windows / URL 都安全，
  节目名里的中文书名号、引号、斜杠都不会变成路径；
* 所有 XML 均由 :mod:`xml.etree.ElementTree` 生成，中文与特殊字符自动转义；
* 同一输入 + 同一个 ``now`` 产生的字节完全一致（排序规则全部写死）；
* 时间一律北京时间（UTC+8，无夏令时），``pubDate`` 输出 ``+0800``。
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date as _date
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

__all__ = [
    "build_site",
    "album_hash",
    "feed_filename",
    "mime_for_url",
    "MIME_BY_EXT",
    "ITUNES_NS",
]

# ---------------------------------------------------------------- 常量兜底
# config.py 由主流程维护；这里 import 失败（并发改动、模块缺失）时用本文件内的
# 后备常量，保证发布步骤不因上游模块状态而崩溃。
ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
ET.register_namespace("itunes", ITUNES_NS)

_FALLBACK_TZ = timezone(timedelta(hours=8))
_FALLBACK_TITLE = "安徽生活广播(安徽城市之声) 整期回听"
_FALLBACK_BASE_URL = "https://taoengine.github.io/ahshgb-forever"
_FALLBACK_COVER = ""

AUTHOR = "安徽生活广播(安徽城市之声)"
OWNER_NAME = "ahshgb-forever"
SOURCE_LABELS = {"ajmide": "阿基米德", "ahrtv": "安徽广电"}
MIME_BY_EXT = {
    ".m4a": "audio/mp4",
    ".m4b": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".m3u8": "application/vnd.apple.mpegurl",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
}
DEFAULT_MIME = "audio/mpeg"

try:  # pragma: no cover - 只在 config 不可用时走到兜底分支
    from . import config as _config
except Exception:  # noqa: BLE001 - 发布模块必须对 config 状态免疫
    _config = None


def _config_value(name, fallback):
    """读 config 常量，缺失或为空时用本模块的后备值。"""
    value = getattr(_config, name, None) if _config is not None else None
    return fallback if value is None or value == "" else value


TZ = _config_value("TZ", _FALLBACK_TZ)
DEFAULT_TITLE = _config_value("DEFAULT_TITLE", _FALLBACK_TITLE)
DEFAULT_BASE_URL = _config_value("DEFAULT_BASE_URL", _FALLBACK_BASE_URL)
DEFAULT_COVER = _config_value("COVER_URL", _FALLBACK_COVER)

_DIGITS_RE = re.compile(r"[^0-9]")
# 只清理本模块自己生成的 feed 文件名，绝不碰目录里的其它文件。
_FEED_NAME_RE = re.compile(r"^[0-9a-f]{12}\.xml$")
_MB = 1048576


# ---------------------------------------------------------------- 命名 / MIME
def album_hash(album) -> str:
    """节目名 → 12 位十六进制哈希（RSS 文件名用，稳定且跨平台安全）。"""
    return hashlib.sha1(str(album).encode("utf-8")).hexdigest()[:12]


def feed_filename(album) -> str:
    """节目名 → ``feeds/`` 下的文件名，例如 ``3f2a9c1b0d4e.xml``。"""
    return album_hash(album) + ".xml"


def mime_for_url(url) -> str:
    """按音频 URL 的扩展名挑 MIME，未知扩展名回落 ``audio/mpeg``。

    查询串/锚点先剥掉，因此 ``.../a.m4a?token=1`` 仍能识别成 ``audio/mp4``。
    """
    path = str(url or "")
    for sep in ("?", "#"):
        path = path.split(sep, 1)[0]
    return MIME_BY_EXT.get(Path(path).suffix.lower(), DEFAULT_MIME)


# ---------------------------------------------------------------- 规范化
def _digits(value) -> str:
    return _DIGITS_RE.sub("", str(value if value is not None else ""))


def _hour_minute(hour_value):
    """``"2100"`` → ``(21, 0)``；空值/非法值 → ``None``。"""
    digits = _digits(hour_value)
    if len(digits) < 4:
        return None
    hh, mm = int(digits[0:2]), int(digits[2:4])
    if hh > 23 or mm > 59:
        return None
    return hh, mm


def _parse_when(date_value, hour_value, tz):
    """``date="20261102"`` + ``hour="2100"`` → 北京时间 datetime。

    日期坏掉（无法组成合法日期）时返回 ``None``，调用方会跳过这条 episode；
    小时缺失或非法时按 12:00 处理。日期里的 ``-``/``/`` 等分隔符会被忽略。
    """
    digits = _digits(date_value)
    if len(digits) < 8:
        return None
    try:
        when = datetime(
            int(digits[0:4]), int(digits[4:6]), int(digits[6:8]), 12, 0, tzinfo=tz
        )
    except ValueError:
        return None
    hour = _hour_minute(hour_value)
    if hour is not None:
        when = when.replace(hour=hour[0], minute=hour[1])
    return when


def _size_to_bytes(size_mb) -> int:
    """``size_mb`` → enclosure 的 ``length``（字节，取整）；缺失/非法 → 0。"""
    try:
        mb = float(size_mb) if size_mb not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0
    if mb <= 0:
        return 0
    return int(round(mb * _MB))


def _as_beijing(now, tz) -> datetime:
    """把 ``now`` 参数统一成北京时间；``None`` 表示"当前时间"。"""
    if now is None:
        return datetime.now(tz)
    if isinstance(now, datetime):
        return now.replace(tzinfo=tz) if now.tzinfo is None else now.astimezone(tz)
    if isinstance(now, _date):
        return datetime(now.year, now.month, now.day, 12, 0, tzinfo=tz)
    raise TypeError("now 必须是 datetime / date / None，收到 %r" % (type(now),))


def _normalize_episodes(episodes, tz):
    """过滤、去重、补全 episode 记录；返回按确定性顺序排列的 dict 列表。

    去重键是 ``(album, url)``：同一个节目的同一个音频地址只发布一次
    （上游 ajmide/ahrtv 两个来源可能给出重复项）。输入顺序不影响结果：
    先按字段排序再"先到先得"，保证若干扰项顺序变化产物也不变。
    """
    raw = [ep for ep in (episodes or []) if isinstance(ep, dict)]
    raw.sort(
        key=lambda ep: (
            str(ep.get("album") or ""),
            _digits(ep.get("date")),
            str(ep.get("hour") or ""),
            str(ep.get("url") or ""),
            str(ep.get("title") or ""),
        )
    )
    rows, seen = [], set()
    for ep in raw:
        album = str(ep.get("album") or "").strip()
        url = str(ep.get("url") or "").strip()
        if not album or not url:  # 缺必填字段 → 丢弃（上游数据问题）
            continue
        key = (album, url)
        if key in seen:
            continue
        when = _parse_when(ep.get("date"), ep.get("hour"), tz)
        if when is None:  # 日期无法解析 → 无法生成 pubDate，跳过
            continue
        seen.add(key)
        rows.append(
            {
                "album": album,
                "url": url,
                "when": when,
                "hour": _hour_minute(ep.get("hour")),
                "source": str(ep.get("source") or "").strip(),
                "length": _size_to_bytes(ep.get("size_mb")),
                "given_title": str(ep.get("title") or "").strip(),
            }
        )
    return rows


def _make_items(rows):
    """给每条 episode 生成最终 item（标题 / 描述 / 排序）。

    * 标题默认 ``"{album}-{YYYY-MM-DD}"``；
    * 同节目同日期出现多条时追加 ``" HH:MM"``（hour 未知则不加）；
    * 新 → 旧排序，同一时刻按 URL 降序兜底，保证结果确定。
    """
    per_date = Counter((row["album"], row["when"].date()) for row in rows)
    items = []
    for row in rows:
        day = row["when"].date()
        day_text = day.strftime("%Y-%m-%d")
        hour_text = "%02d:%02d" % row["hour"] if row["hour"] else ""
        title = row["given_title"] or "%s-%s" % (row["album"], day_text)
        if per_date[(row["album"], day)] > 1 and hour_text:
            title = "%s %s" % (title, hour_text)

        detail = "《%s》%s" % (row["album"], day_text)
        if hour_text:
            detail += " " + hour_text
        detail += " 整期回听"
        if row["source"]:
            detail += " · 来源：%s" % SOURCE_LABELS.get(row["source"], row["source"])

        items.append(
            {
                "title": title,
                "when": row["when"],
                "url": row["url"],
                "description": detail,
                "length": row["length"],
                "mime": mime_for_url(row["url"]),
            }
        )
    items.sort(key=lambda item: (item["when"], item["url"]), reverse=True)
    return items


# ---------------------------------------------------------------- XML 构建
def _q(tag: str) -> str:
    """itunes 命名空间下的标签名。"""
    return "{%s}%s" % (ITUNES_NS, tag)


def _text(parent, tag, value):
    """建子元素并写入文本，返回子元素。"""
    element = ET.SubElement(parent, tag)
    element.text = value
    return element


def _build_rss(album, items, feed_url, *, cover, now):
    """构建单个节目的 RSS 2.0 树。"""
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    _text(channel, "title", album)
    _text(channel, "link", feed_url)
    _text(channel, "description", "%s《%s》整期回听" % (AUTHOR, album))
    _text(channel, "language", "zh-cn")
    _text(channel, "lastBuildDate", format_datetime(now))
    _text(channel, _q("author"), AUTHOR)
    ET.SubElement(channel, _q("image"), {"href": cover or ""})
    _text(channel, _q("explicit"), "false")

    for item in items:
        node = ET.SubElement(channel, "item")
        _text(node, "title", item["title"])
        _text(node, "pubDate", format_datetime(item["when"]))
        guid = _text(node, "guid", item["url"])
        guid.set("isPermaLink", "false")  # 音频 CDN 直链不是永久页面
        _text(node, "description", item["description"])
        ET.SubElement(
            node,
            "enclosure",
            {
                "url": item["url"],
                "length": str(item["length"]),
                "type": item["mime"],
            },
        )
    return rss


def _build_opml(albums, base_url, site_title, now):
    """构建 OPML 2.0 树：每个节目一个 folder outline，内含一个 rss 订阅。"""
    opml = ET.Element("opml", {"version": "2.0"})
    head = ET.SubElement(opml, "head")
    _text(head, "title", site_title)
    _text(head, "dateCreated", format_datetime(now))
    _text(head, "ownerName", OWNER_NAME)
    body = ET.SubElement(opml, "body")
    for album in albums:
        folder = ET.SubElement(body, "outline", {"text": album, "title": album})
        ET.SubElement(
            folder,
            "outline",
            {
                "text": album,
                "title": album,
                "type": "rss",
                "xmlUrl": "%s/feeds/%s" % (base_url, feed_filename(album)),
            },
        )
    return opml


def _write_xml(root, path: Path) -> None:
    """缩进后以 UTF-8 + XML 声明写盘（ElementTree 负责全部转义）。"""
    ET.indent(root, space="  ")
    path.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))


def _prune_stale_feeds(feeds_dir: Path, keep: set[str]) -> None:
    """删掉上一轮生成、本轮已不存在的 feed（节目改名/下架后的残留）。

    只删文件名形如 ``[0-9a-f]{12}.xml`` 的文件，不碰目录里的其它内容。
    """
    for path in feeds_dir.glob("*.xml"):
        if _FEED_NAME_RE.match(path.name) and path.name not in keep:
            path.unlink()


# ---------------------------------------------------------------- 主入口
def build_site(episodes, out_dir, base_url, *, title=None, cover=None, now=None) -> dict:
    """把 episode 列表写成每节目 RSS + ``ahshgb.opml`` 站点。

    参数
    ----
    episodes:
        episode 字典列表，字段：``album``、``date``（``YYYYMMDD``）、
        ``hour``（``HHMM`` 或 ``""``）、``url``、``source``（``ajmide``/``ahrtv``）、
        可选 ``size_mb``（float）与 ``title``。允许重复，按 ``(album, url)`` 去重。
    out_dir:
        站点输出目录（会被创建）。
    base_url:
        站点根地址，例如 ``https://taoengine.github.io/ahshgb-forever``；feed 的
        ``link`` 与 OPML 的 ``xmlUrl`` 都是它拼出来的绝对地址。
    title / cover:
        站点标题与封面图；缺省取 :mod:`ahshgb.config` 里的常量。
    now:
        生成时间（注入测试用），``datetime``/``date``/``None``；统一换算成北京时间。

    返回
    ----
    汇总信息（见下），可直接被 CLI 打印：

    ``{"albums": int, "episodes": int,
       "feeds": [{"album", "path", "episodes", "bytes"}, ...],
       "opml": "<out_dir>/ahshgb.opml"}``

    其中 ``episodes`` 是去重后实际写入的总条数，``feeds`` 按节目名排序，
    ``path`` 是 feed 文件路径（字符串）。
    """
    tz = TZ
    site_title = str(title) if title else DEFAULT_TITLE
    image = str(cover) if cover else DEFAULT_COVER
    base = str(base_url or "").strip().rstrip("/") or DEFAULT_BASE_URL
    moment = _as_beijing(now, tz)

    output = Path(out_dir)
    feeds_dir = output / "feeds"
    feeds_dir.mkdir(parents=True, exist_ok=True)

    rows = _normalize_episodes(episodes, tz)
    by_album = {}
    for row in rows:
        by_album.setdefault(row["album"], []).append(row)
    albums = sorted(by_album)

    feeds = []
    for album in albums:
        items = _make_items(by_album[album])
        filename = feed_filename(album)
        feed_path = feeds_dir / filename
        feed_url = "%s/feeds/%s" % (base, filename)
        root = _build_rss(album, items, feed_url, cover=image, now=moment)
        _write_xml(root, feed_path)
        feeds.append(
            {
                "album": album,
                "path": str(feed_path),
                "episodes": len(items),
                "bytes": feed_path.stat().st_size,
            }
        )

    _prune_stale_feeds(feeds_dir, {feed_filename(album) for album in albums})

    opml_path = output / "ahshgb.opml"
    _write_xml(_build_opml(albums, base, site_title, moment), opml_path)
    (output / ".nojekyll").write_text("", encoding="utf-8")

    return {
        "albums": len(albums),
        "episodes": sum(feed["episodes"] for feed in feeds),
        "feeds": feeds,
        "opml": str(opml_path),
    }
