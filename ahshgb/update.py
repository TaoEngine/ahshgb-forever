# -*- coding: utf-8 -*-
"""每晚增量更新探测缓存。

流程：
1. 从阿基米德接口刷新「在播节目 → CDN 桶/时段」映射（``cache/programs.json``）；
2. 把接口返回的最近回听补进探测缓存；
3. 对名册里仍活跃的 (桶, 时段)，探测最近 ``days`` 天里缓存还没答案的日期；
4. 把结果写回 ``cache/probe_cache.csv``。

只做增量：缓存里已命中的点永久跳过；历史回填由用户上传的历史缓存负责。
"""
import datetime as dt
import os

from . import ajmide, config, probe, roster as roster_mod

# 每个档位探测时的星期白名单来源优先级：
#   名册 liveTime（权威） > 缓存命中分布（推断） > 不限制（全探）
MAX_POINTS_SAFETY = 20000


def _log_default(message):
    print(message, flush=True)


def _bucket_hours_from_discovery(discovery, bucket):
    hours = set()
    info = (discovery or {}).get(bucket) or {}
    for hh, value in (info.get("hours") or {}).items():
        if value.get("hit"):
            hh_int = roster_mod.hour_int(hh)
            if hh_int is not None:
                hours.add(hh_int)
    return hours


def _scan_cache_hits(cache):
    """扫描缓存，返回 ``(last_hit, hit_hours, hit_dates)``。

    * last_hit: ``{(bucket, hour): 'YYYYMMDD'}`` 最近命中日期
    * hit_hours: ``{bucket: set(hour)}`` 有命中的时段
    * hit_dates: ``{(bucket, hour): [date8, ...]}`` 用于推断星期
    """
    last_hit = {}
    hit_hours = {}
    hit_dates = {}
    for bucket, date8, hour, _size in cache.hits():
        key = (bucket, hour)
        if date8 > last_hit.get(key, ""):
            last_hit[key] = date8
        hit_hours.setdefault(bucket, set()).add(hour)
        hit_dates.setdefault(key, []).append(date8)
    return last_hit, hit_hours, hit_dates


def _hours_for_bucket(bucket, roster_info, discovery, programs, hit_hours):
    hours = set((roster_info or {}).get("hours") or ())
    hours |= _bucket_hours_from_discovery(discovery, bucket)
    info = (programs or {}).get(bucket) or {}
    hours |= set(info.get("hours") or ())
    hours |= hit_hours.get(bucket, set())
    return {h for h in hours if h is not None}


def _probe_weekdays(bucket, hour, roster_info, hit_dates, api_weekdays):
    """探测时使用的星期白名单；None 表示不限。"""
    live_times = (roster_info or {}).get("live_times") or []
    days = roster_mod.weekdays_for_hour(live_times, hour)
    if days:
        return days
    dates = hit_dates.get((bucket, hour)) or []
    days = roster_mod.trap_aware_weekdays(
        dates, ahrtv_days=None) if dates else None
    if days:
        return days
    return api_weekdays


def _api_weekdays_by_bucket(channel_programs):
    """接口最近回听日期 → 每个桶可能的星期（信息不足则 None）。"""
    result = {}
    for program in channel_programs or []:
        for ep in program.get("recent") or []:
            bucket = ep.get("bucket")
            if not bucket:
                continue
            result.setdefault(bucket, set())
            try:
                result[bucket].add(
                    dt.datetime.strptime(ep["date"], "%Y%m%d").isoweekday())
            except (KeyError, ValueError):
                continue
    out = {}
    for bucket, days in result.items():
        # 最近 5 条若覆盖 3 个以上星期，多半是日播/周多播，直接全探更保险
        out[bucket] = days if 0 < len(days) <= 2 else None
    return out


def _active(bucket, roster_info, programs, last_hit, today, days):
    """判断某个桶现在是否还值得每晚探测。"""
    if bucket in (programs or {}):
        return True
    for text in (roster_info or {}).get("live_times") or []:
        if "停播" not in str(text):
            return True
    recent_cutoff = (today - dt.timedelta(days=int(days))).strftime("%Y%m%d")
    for (b, _hour), date8 in last_hit.items():
        if b == bucket and date8 >= recent_cutoff:
            return True
    return False


def _merge_programs_payload(old_programs, channel_programs, generated_at):
    """把接口结果与已有 programs.json 合并成可落盘的 payload。"""
    entries = {}       # bucket -> {album, brand_id, program_id, cover, hours, last_seen}
    for bucket, info in (old_programs or {}).items():
        entries.setdefault(bucket, {
            "album": info.get("album"), "brand_id": info.get("brand_id"),
            "program_id": info.get("program_id"), "cover": info.get("cover"),
            "hours": set(info.get("hours") or ()), "last_seen": "",
        })
    for program in channel_programs or []:
        for ep in program.get("recent") or []:
            bucket = ep.get("bucket")
            if not bucket:
                continue
            rec = entries.setdefault(bucket, {
                "album": program.get("album"), "brand_id": program.get("brand_id"),
                "program_id": program.get("program_id"),
                "cover": program.get("cover"), "hours": set(), "last_seen": "",
            })
            rec["album"] = program.get("album") or rec.get("album")
            rec["brand_id"] = program.get("brand_id") or rec.get("brand_id")
            rec["program_id"] = program.get("program_id") or rec.get("program_id")
            rec["cover"] = program.get("cover") or rec.get("cover")
            rec["hours"].add(int(ep["hour"]))
            if ep["date"] > rec.get("last_seen", ""):
                rec["last_seen"] = ep["date"]

    programs = []
    grouped = {}
    for bucket, info in sorted(entries.items()):
        album = info.get("album") or "未命名节目"
        key = (album, info.get("brand_id"), info.get("program_id"), info.get("cover"))
        rec = grouped.setdefault(key, {"album": album,
                                       "brand_id": info.get("brand_id"),
                                       "program_id": info.get("program_id"),
                                       "cover": info.get("cover"),
                                       "buckets": {}})
        rec["buckets"][bucket] = {"hours": sorted(info["hours"]),
                                  "last_seen": info.get("last_seen") or ""}
    for rec in grouped.values():           # 桶按名字排序，保证文件内容稳定
        rec["buckets"] = {k: rec["buckets"][k] for k in sorted(rec["buckets"])}
    programs = sorted(grouped.values(), key=lambda r: (r.get("album") or "",
                                                       r.get("brand_id") or 0))
    return {"generated_at": generated_at, "programs": programs}


def _canonical(obj):
    """递归排序 dict key，便于比较两个 JSON 结构是否等价。"""
    if isinstance(obj, dict):
        return {k: _canonical(obj[k]) for k in sorted(obj)}
    if isinstance(obj, list):
        return [_canonical(x) for x in obj]
    return obj


def _same_payload_ignoring_time(old, new):
    if not isinstance(old, dict) or not isinstance(new, dict):
        return False
    a = dict(old)
    b = dict(new)
    a.pop("generated_at", None)
    b.pop("generated_at", None)
    return _canonical(a) == _canonical(b)


def run(cache_dir, *, days=config.DEFAULT_CATCHUP_DAYS,
        workers=config.DEFAULT_WORKERS, recheck_days=config.PROBE_RECHECK_DAYS,
        dry_run=False, use_api=True, log=None):
    """执行一次增量更新，返回统计 dict。

    缓存文件缺失时抛出 ``FileNotFoundError``，由调用方决定如何提示。
    """
    log = log or _log_default
    probe_path = os.path.join(cache_dir, config.PROBE_CACHE_NAME)
    roster_path = os.path.join(cache_dir, config.ROSTER_NAME)
    if not os.path.exists(probe_path):
        raise FileNotFoundError(
            "缺少探测缓存 %s，请先把历史缓存上传到 %s/" % (probe_path, cache_dir))
    if not os.path.exists(roster_path):
        raise FileNotFoundError(
            "缺少节目名册 %s，请先把 roster.json 上传到 %s/" % (roster_path, cache_dir))

    today = config.today_beijing()
    start = today - dt.timedelta(days=max(1, int(days)))
    end = today - dt.timedelta(days=1)

    cache = probe.ProbeCache(probe_path)
    roster = roster_mod.load_roster(cache_dir)
    discovery = roster_mod.load_discovery(cache_dir)
    old_programs = roster_mod.load_programs(cache_dir)
    roster_info = roster_mod.roster_buckets(roster)
    log("缓存：%s（%d 条）" % (os.path.basename(probe_path), len(cache)))
    log("名册：%d 个节目 / %d 个桶" % (len(roster), len(roster_info)))
    log("增量区间：%s ~ %s" % (start, end))

    # ---- 1) 刷新在播节目映射并收集最近回听 ----
    channel_programs = ajmide.channel_programs(log=log) if use_api else []
    if channel_programs:
        log("接口：在播节目 %d 个，最近回听 %d 条" % (
            len(channel_programs),
            sum(len(p["recent"]) for p in channel_programs)))
    else:
        log("接口：未取到在播节目（使用已有 programs.json / 名册继续）")
    programs = old_programs
    if channel_programs:
        payload = _merge_programs_payload(old_programs, channel_programs,
                                          config.now_beijing().isoformat())
        new_programs = {}
        for entry in payload["programs"]:
            for bucket, info in entry["buckets"].items():
                new_programs[probe.norm_bucket(bucket)] = {
                    "album": entry.get("album"),
                    "hours": set(info.get("hours") or ()),
                    "cover": entry.get("cover"),
                    "brand_id": entry.get("brand_id"),
                    "program_id": entry.get("program_id"),
                }
        programs = new_programs
        if not dry_run:
            old_payload = roster_mod.load_json(
                os.path.join(cache_dir, config.PROGRAMS_NAME))
            if _same_payload_ignoring_time(old_payload, payload):
                payload["generated_at"] = (old_payload or {}).get(
                    "generated_at", payload["generated_at"])
            roster_mod.save_json(os.path.join(cache_dir, config.PROGRAMS_NAME),
                                 payload)
    else:
        payload = None

    last_hit, hit_hours, hit_dates = _scan_cache_hits(cache)
    albums_by_bucket = roster_mod.album_map(roster, programs)
    api_weekdays = _api_weekdays_by_bucket(channel_programs)

    # ---- 2) 组装探测计划 ----
    points = set()
    plan_meta = {}
    buckets = set(roster_info) | set(programs) | set(hit_hours)
    for bucket in sorted(buckets):
        info = roster_info.get(bucket)
        if not _active(bucket, info, programs, last_hit, today, days):
            continue
        hours = _hours_for_bucket(bucket, info, discovery, programs, hit_hours)
        for hour in sorted(hours):
            weekdays = _probe_weekdays(bucket, hour, info, hit_dates,
                                       api_weekdays.get(bucket))
            key = (bucket, hour)
            plan_meta[key] = (albums_by_bucket.get(bucket), weekdays)
            date = start
            while date <= end:
                if weekdays is None or date.isoweekday() in weekdays:
                    date8 = date.strftime("%Y%m%d")
                    if probe.needs_probe(cache, bucket, date8, hour,
                                         today=today, recheck_days=recheck_days):
                        points.add((bucket, date8, hour))
                date += dt.timedelta(days=1)

    # ---- 3) 接口最近回听（补接口日期可能比探测范围新/旧的情况）----
    api_points = set()
    for program in channel_programs or []:
        for ep in program.get("recent") or []:
            api_points.add((ep["bucket"], ep["date"], int(ep["hour"])))
    for bucket, date8, hour in api_points:
        if probe.needs_probe(cache, bucket, date8, hour, today=today,
                             recheck_days=max(recheck_days, 7)):
            points.add((bucket, date8, hour))
    points = sorted(points)

    plans = len({(b, h) for b, h in plan_meta})
    log("探测计划：%d 个档位，待探测 %d 点（接口最近回听 %d 点）" % (
        plans, len(points), len(api_points)))
    if len(points) > MAX_POINTS_SAFETY:
        log("警告：待探测点 %d 超过安全阈值 %d，请检查名册/增量区间" % (
            len(points), MAX_POINTS_SAFETY))

    stats = {
        "cache_rows": len(cache),
        "plans": plans,
        "points": len(points),
        "api_episodes": len(api_points),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "probed": 0,
        "hits": 0,
        "new_hits_by_album": {},
        "status": {},
        "programs": len(channel_programs),
    }
    if dry_run:
        log("dry-run：不写缓存、不发探测请求")
        return stats
    if not points:
        log("没有需要探测的点，缓存已是最新")
        return stats

    # ---- 4) 探测并落盘 ----
    results = probe.probe_many(points, cache, workers=workers, log=log)
    status_count = {}
    new_hits_by_album = {}
    for (bucket, date8, hour), (status, _size) in results.items():
        status_count[status] = status_count.get(status, 0) + 1
        if status in probe.HIT_STATUS:
            album = plan_meta.get((bucket, hour), (None, None))[0] \
                or albums_by_bucket.get(bucket)
            new_hits_by_album[album or bucket] = \
                new_hits_by_album.get(album or bucket, 0) + 1
    stats["probed"] = len(results)
    stats["hits"] = sum(1 for st, _ in results.values() if st in probe.HIT_STATUS)
    stats["status"] = {probe.STATUS_TEXT.get(k, str(k)): v
                       for k, v in sorted(status_count.items())}
    stats["new_hits_by_album"] = dict(sorted(new_hits_by_album.items()))
    log("本次新增可读 %d 条，状态分布 %s" % (stats["hits"], stats["status"]))
    return stats
