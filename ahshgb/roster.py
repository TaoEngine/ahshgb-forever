# -*- coding: utf-8 -*-
"""名册 / 发现结果 / ahrtv 补源的读取，以及排期（liveTime）解析。

``cache/roster.json``（用户上传）结构::

    [{"album": "1055乐光宝盒",
      "buckets": [{"bucket": "c_3785", "hours": ["1400"], "mode": "known",
                   "programIds": ["13324"], "liveTime": ["已停播"],
                   "scan": ["2015-04-01", "2016-12-31"]}]}]

``cache/discovery.json`` 是逐桶 24 小时扫描的发现结果::

    {"c_26123": {"album": "世界民谣简史",
                 "hours": {"2000": {"hit": ["20200105", ...], "lock": []}},
                 "span": ["20200105", "20200905"]}}

``cache/programs.json`` 由 ``ahshgb update`` 每晚从接口刷新，记录当前在播节目
及其 CDN 桶/时段，用于在名册之外发现新节目/新桶。
"""
import datetime as dt
import json
import os
import re
from collections import Counter

from . import config

_SCHEDULE_RE = re.compile(
    r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*\(([0-9,\-\s]+)\)")


# ---------------------------------------------------------------- 读文件
def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    """原子写 JSON（UTF-8，不转义中文）。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, path)


def path_in(cache_dir, name):
    return os.path.join(cache_dir, name)


def load_roster(cache_dir):
    data = load_json(path_in(cache_dir, config.ROSTER_NAME), [])
    return data if isinstance(data, list) else []


def load_discovery(cache_dir):
    data = load_json(path_in(cache_dir, config.DISCOVERY_NAME), {})
    return data if isinstance(data, dict) else {}


def load_ahrtv(cache_dir):
    data = load_json(path_in(cache_dir, config.AHRTV_EPISODES_NAME), [])
    return data if isinstance(data, list) else []


def load_programs(cache_dir):
    """返回 ``{bucket: {album, hours:set[int], cover, brand_id, program_id}}``。"""
    data = load_json(path_in(cache_dir, config.PROGRAMS_NAME), {})
    result = {}
    if not isinstance(data, dict):
        return result
    entries = data.get("programs") if isinstance(data.get("programs"), list) else []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        album = (entry.get("album") or "").strip()
        for bucket, info in (entry.get("buckets") or {}).items():
            hours = set()
            for h in (info.get("hours") or []):
                hh = hour_int(h)
                if hh is not None:
                    hours.add(hh)
            result[probe_norm_bucket(bucket)] = {
                "album": album,
                "hours": hours,
                "cover": entry.get("cover"),
                "brand_id": entry.get("brand_id"),
                "program_id": entry.get("program_id"),
            }
    return result


def probe_norm_bucket(bucket):
    b = str(bucket).strip()
    return b if b.startswith("c_") else "c_" + b


# ---------------------------------------------------------------- 排期解析
def hour_int(value):
    """把 ``'1700'`` / ``'17'`` / 17 统一成小时整数；无法解析返回 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= 23 else None
    s = str(value).strip()
    if not s:
        return None
    try:
        n = int(s)
    except ValueError:
        return None
    if len(s) >= 3:                 # '1700' -> 17
        return n // 100 if 0 <= n // 100 <= 23 else None
    return n if 0 <= n <= 23 else None


def parse_schedule(text):
    """解析 ``"17:00-19:00(1-5)"`` 这类排期，返回 ``[{start,end,days}]``。"""
    segments = []
    for m in _SCHEDULE_RE.finditer(str(text or "")):
        start = int(m.group(1))
        end = int(m.group(3))
        days = parse_days(m.group(5))
        if days:
            segments.append({"start": start, "end": end, "days": days})
    return segments


def parse_days(text):
    """``"1-5"`` / ``"1-4,6,7"`` -> ``{1,2,3,4,6,7}``（1=周一 … 7=周日）。"""
    days = set()
    for part in str(text or "").replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            try:
                a, b = part.split("-", 1)
                days.update(range(int(a), int(b) + 1))
            except ValueError:
                continue
        else:
            try:
                days.add(int(part))
            except ValueError:
                continue
    return {d for d in days if 1 <= d <= 7}


def weekdays_for_hour(live_times, hour):
    """从 roster 的 liveTime 列表里取某个小时对应的星期集合。

    返回 ``set[int]`` 或 None（未知/已停播）。
    """
    if hour is None:
        return None
    days = set()
    found = False
    for text in live_times or []:
        if "停播" in str(text):
            continue
        for seg in parse_schedule(text):
            if seg["start"] <= hour < max(seg["end"], seg["start"] + 1):
                found = True
                days.update(seg["days"])
            elif seg["end"] <= seg["start"] and (hour >= seg["start"] or hour < seg["end"]):
                found = True               # 跨零点档
                days.update(seg["days"])
    return days if found else None


def weekdays_from_dates(dates):
    """按命中日期分布推断星期集合（≥最大频次 15% 的星期）。"""
    cnt = Counter()
    for d in dates:
        try:
            cnt[dt.datetime.strptime(str(d).replace("-", ""), "%Y%m%d").isoweekday()] += 1
        except ValueError:
            continue
    if not cnt:
        return None
    top = max(cnt.values())
    return {d for d, c in cnt.items() if c >= max(1, top * 0.15)}


def trap_aware_weekdays(dates, declared=None, ahrtv_days=None):
    """沿用地版本 build_opml 的星期判定（周末档陷阱只存在于 2016–2018）。

    优先级：声明排期 > ahrtv 实际分布 > 「2019 年后只剩工作日」判陷阱 > 命中分布。
    返回 ``set[int]`` 或 None。
    """
    if declared:
        return set(declared)
    if ahrtv_days:
        return set(ahrtv_days)
    cnt = Counter()
    wd_after_2018 = we_after_2018 = 0
    for d in dates:
        try:
            day = dt.datetime.strptime(str(d).replace("-", ""), "%Y%m%d")
        except ValueError:
            continue
        weekday = day.isoweekday()
        cnt[weekday] += 1
        if day.strftime("%Y%m%d") >= "20190101":
            if weekday >= 6:
                we_after_2018 += 1
            else:
                wd_after_2018 += 1
    if not cnt:
        return None
    if wd_after_2018 and not we_after_2018:
        return {d for d in cnt if d < 6}
    return {d for d, c in cnt.items() if c >= max(1, max(cnt.values()) * 0.15)}


# ---------------------------------------------------------------- 名册辅助
def album_map(roster, programs=None):
    """``{bucket: album}``。roster 的命名优先，programs 只补 roster 里没有的桶。"""
    result = {}
    for bucket, info in (programs or {}).items():
        if info.get("album"):
            result[probe_norm_bucket(bucket)] = info["album"]
    for entry in roster or []:
        album = (entry.get("album") or "").strip()
        if not album:
            continue
        for bucket in entry.get("buckets") or []:
            b = probe_norm_bucket(bucket.get("bucket") or "")
            if b != "c_":
                result[b] = album
    return result


def roster_buckets(roster):
    """``{bucket: {"album","hours","live_times","scan"}}``。"""
    result = {}
    for entry in roster or []:
        album = (entry.get("album") or "").strip()
        for bucket in entry.get("buckets") or []:
            b = probe_norm_bucket(bucket.get("bucket") or "")
            if b == "c_":
                continue
            hours = {hour_int(h) for h in (bucket.get("hours") or [])}
            result[b] = {
                "album": album,
                "hours": {h for h in hours if h is not None},
                "live_times": list(bucket.get("liveTime") or []),
                "scan": list(bucket.get("scan") or []),
            }
    return result


def album_list(roster, programs=None):
    names = {e.get("album") for e in roster or [] if e.get("album")}
    names.update(info.get("album") for info in (programs or {}).values()
                 if info.get("album"))
    return sorted(names)
