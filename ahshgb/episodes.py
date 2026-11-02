# -*- coding: utf-8 -*-
"""从探测缓存（+ ahrtv 补源）选出可播 episode 清单。

这一步是原 ``ahshgb-podcast/build_opml.py`` 的核心逻辑移植：
* 只收可播（200/206）的整期录音；
* 2020-01-01 ~ 2022-10-14 优先用 ahrtv 源，该日没有 ahrtv 才回填阿基米德；
* 过滤「周末档陷阱」：2016–2018 年工作日节目在周末录到的是别的节目；
* 同一 URL 只出现一次。
"""
import datetime as dt
from collections import Counter, defaultdict

from . import config, probe, roster as roster_mod

# 现播节目声明的播出日（表2/接口排期；1=周一 … 7=周日）
DECLARED_DAYS = {
    "那些歌儿": {1, 2, 3, 4, 5, 6, 7},
    "马大姐话健康": {1, 2, 3, 4, 5, 6, 7},
    "汽车315": {1, 2, 3, 4, 5, 6, 7},
    "徐徐时光": {1, 2, 3, 4, 5, 6, 7},
    "大牌主打歌": {1, 2, 3, 4, 5, 6, 7},
    "走进挂牌企业": {5},
    "国元证券时间": {1, 2, 3, 4, 5},
    '心花一路"fun"': {1, 2, 3, 4, 5},
    "城市morning call": {1, 2, 3, 4, 5},
    "资讯有故事": {1, 2, 3, 4, 6, 7},
    "爱上乐生活": {6, 7},
}

ALL_DAYS = set(range(1, 8))
AHRTV_START, AHRTV_END = config.AHRTV_WINDOW


def _weekday(date8):
    """1=周一 … 7=周日。"""
    return dt.datetime.strptime(str(date8).replace("-", ""), "%Y%m%d").isoweekday()


def _norm_ahrtv(raw_rows):
    """把 ahrtv_episodes.json 规范成 ``{album: [row]}`，日期统一 YYYYMMDD。"""
    by_album = defaultdict(list)
    for row in raw_rows or []:
        if not isinstance(row, dict):
            continue
        album = (row.get("album") or "").strip()
        if not album or album.startswith("?"):
            continue
        date8 = probe.norm_date(row.get("date") or "")
        if len(date8) != 8 or not date8.isdigit():
            continue
        try:
            dt.datetime.strptime(date8, "%Y%m%d")
        except ValueError:
            continue
        status = int(row.get("status") or 0)
        by_album[album].append({
            "album": album,
            "date": date8,
            "url": row.get("url") or "",
            "status": status,
            "duration": row.get("duration") or 0,
        })
    return by_album


def _ahrtev_weekdays(by_album):
    """ahrtv 各专辑实际播出的星期分布（用于周末档判定）。"""
    result = {}
    for album, rows in by_album.items():
        cnt = Counter(_weekday(r["date"]) for r in rows)
        if not cnt:
            continue
        top = max(cnt.values())
        result[album] = {d for d, c in cnt.items() if c >= top * 0.15}
    return result


def collect(cache, roster, programs, ahrtv):
    """返回 ``(episodes, stats)``。

    episodes: ``[{album,date,hour,url,source,size_mb,bucket}]``，按 (date,url) 升序；
    stats: 供命令行/日志展示的统计。
    """
    albums_by_bucket = roster_mod.album_map(roster, programs)
    ahrtv_by_album = _norm_ahrtv(ahrtv)
    ahrtv_weekdays = _ahrtev_weekdays(ahrtv_by_album)

    # ---- 从探测缓存收集命中：按 (bucket, hour) 聚合 ----
    hit_dates = defaultdict(list)             # (bucket,hour) -> [date8]
    hit_size = {}                             # (bucket,date,hour) -> MB
    album_combos = defaultdict(set)           # album -> {(bucket,hour)}
    for bucket, date8, hour, size_mb in cache.hits():
        album = albums_by_bucket.get(bucket)
        if not album:
            continue
        key = (bucket, hour)
        hit_dates[key].append(date8)
        hit_size[(bucket, date8, hour)] = size_mb
        album_combos[album].add(key)

    # ---- 判定每个档位「本该播出的星期」与活跃区间 ----
    expected_days = {}
    for (bucket, hour), dates in hit_dates.items():
        album = albums_by_bucket[bucket]
        cnt = Counter(_weekday(d) for d in dates)
        we_after_2018 = [d for d in dates if _weekday(d) >= 6 and d >= "20190101"]
        wd_after_2018 = [d for d in dates if _weekday(d) < 6 and d >= "20190101"]
        if album in DECLARED_DAYS:
            days = set(DECLARED_DAYS[album])
        elif album in ahrtv_weekdays:
            days = set(ahrtv_weekdays[album])
        elif wd_after_2018 and not we_after_2018:
            days = {d for d in cnt if d < 6}
        else:
            top = max(cnt.values())
            days = {d for d, c in cnt.items() if c >= max(1, top * 0.15)} or set(cnt)
        expected_days[(bucket, hour)] = days

    weekend_policy = {}
    for album in set(albums_by_bucket.values()) | set(ahrtv_by_album):
        days = set()
        for key in album_combos.get(album, ()):   # noqa: PLC0206 - 只需并集
            days |= expected_days.get(key, ALL_DAYS)
        weekend_policy[album] = days or set(ALL_DAYS)

    # ---- 按专辑挑选可播 episode ----
    episodes = []
    dropped = Counter()
    ahrtv_dates = {
        album: {r["date"] for r in rows if r["status"] in probe.HIT_STATUS}
        for album, rows in ahrtv_by_album.items()
    }
    all_albums = sorted(set(weekend_policy) | set(ahrtv_by_album))
    for album in all_albums:
        picked = []
        seen_urls = set()
        # 1) 阿基米德 CDN
        rows = []
        for bucket, hour in album_combos.get(album, ()):
            for date8 in hit_dates.get((bucket, hour), ()):
                rows.append((date8, hour, bucket))
        rows.sort()
        for date8, hour, bucket in rows:
            url = probe.url_of(bucket, date8, hour)
            if url in seen_urls:
                continue
            if AHRTV_START <= date8 <= AHRTV_END and date8 in ahrtv_dates.get(album, ()):
                dropped["ahrtv优先"] += 1
                continue
            if _weekday(date8) not in weekend_policy.get(album, ALL_DAYS):
                dropped["周末档陷阱"] += 1
                continue
            seen_urls.add(url)
            picked.append({
                "album": album, "date": date8, "hour": "%02d00" % int(hour),
                "url": url, "source": "ajmide", "bucket": bucket,
                "size_mb": hit_size.get((bucket, date8, hour), 0),
            })
        # 2) ahrtv 补源（2020-2022）
        for row in sorted(ahrtv_by_album.get(album, []),
                          key=lambda r: (r["date"], r["url"])):
            if row["status"] not in probe.HIT_STATUS:
                continue
            url = row["url"]
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            picked.append({
                "album": album, "date": row["date"], "hour": "",
                "url": url, "source": "ahrtv", "bucket": "",
                "size_mb": 0,
            })
        picked.sort(key=lambda r: (r["date"], r["url"]))
        episodes.extend(picked)

    stats = {
        "albums": len({e["album"] for e in episodes}),
        "episodes": len(episodes),
        "by_source": dict(Counter(e["source"] for e in episodes)),
        "by_year": dict(sorted(Counter(e["date"][:4] for e in episodes).items())),
        "dropped": dict(dropped),
    }
    return episodes, stats
