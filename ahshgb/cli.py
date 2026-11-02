# -*- coding: utf-8 -*-
"""命令行入口：``python -m ahshgb <update|build|all|stats|check>``。

设计目标：GitHub Actions 里零依赖运行（只用 Python 标准库），
所有命令都从仓库根目录执行。
"""
import argparse
import os
import sys

from . import config


def _setup_stdio():
    """Windows 控制台/CI 下尽量用 UTF-8 输出中文日志。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _base_url(value):
    """优先命令行参数，其次环境变量，最后默认地址。"""
    url = value or os.environ.get("PUBLIC_BASE_URL") or config.DEFAULT_BASE_URL
    return url.rstrip("/")


def _print_stats(cache_dir):
    from . import probe, roster as roster_mod
    path = os.path.join(cache_dir, config.PROBE_CACHE_NAME)
    if not os.path.exists(path):
        print("缓存不存在：%s" % path)
        return 2
    cache = probe.ProbeCache(path)
    counts = {}
    last_hit = {}
    hits_by_bucket = {}
    for bucket, date8, hour, _size in cache.hits():
        hits_by_bucket[bucket] = hits_by_bucket.get(bucket, 0) + 1
        if date8 > last_hit.get(bucket, ""):
            last_hit[bucket] = date8
    for row in cache.rows:
        counts[int(row[3])] = counts.get(int(row[3]), 0) + 1
    roster = roster_mod.load_roster(cache_dir)
    programs = roster_mod.load_programs(cache_dir)
    albums = roster_mod.album_list(roster, programs)
    buckets_by_album = {}
    for bucket, album in roster_mod.album_map(roster, programs).items():
        buckets_by_album.setdefault(album, []).append(bucket)
    print("缓存文件  : %s" % path)
    print("缓存行数  : %d" % len(cache))
    print("状态分布  : %s" % "，".join(
        "%s %d" % (probe.STATUS_TEXT.get(k, k), v) for k, v in sorted(counts.items())))
    print("可读命中  : %d" % sum(counts.get(s, 0) for s in probe.HIT_STATUS))
    print("名册节目  : %d（%d 个桶）" % (len(albums), len(roster_mod.album_map(roster, programs))))
    rows = []
    for album in albums:
        n = sum(hits_by_bucket.get(b, 0) for b in buckets_by_album.get(album, ()))
        latest = max((last_hit.get(b, "") for b in buckets_by_album.get(album, ())),
                     default="")
        if n:
            rows.append((latest, album, n))
    rows.sort(reverse=True)
    print("最近更新的节目（可读条数 / 末条日期）:")
    for latest, album, n in rows[:15]:
        print("  %-20s %5d  %s" % (album, n, latest or "-"))
    if len(rows) > 15:
        print("  …… 其余 %d 个节目" % (len(rows) - 15))
    return 0


def _check(cache_dir):
    from . import probe, roster as roster_mod
    problems = []
    probe_path = os.path.join(cache_dir, config.PROBE_CACHE_NAME)
    roster_path = os.path.join(cache_dir, config.ROSTER_NAME)
    for path, label in ((probe_path, "探测缓存"), (roster_path, "节目名册")):
        if not os.path.exists(path):
            problems.append("缺少%s：%s" % (label, path))
    if problems:
        for p in problems:
            print("✗ " + p)
        print("请把 ahshgb-podcast 的历史缓存文件上传到 %s/ 目录："
              "probe_cache.csv、roster.json、discovery.json、ahrtv_episodes.json" % cache_dir)
        return 2
    cache = probe.ProbeCache(probe_path)
    roster = roster_mod.load_roster(cache_dir)
    if not roster:
        print("✗ %s 不是有效的 JSON 数组" % roster_path)
        return 2
    if getattr(cache, "invalid", 0):
        print("✗ %s 中有 %d 行日期/字段非法，已被跳过；请修正后再构建"
              % (probe_path, cache.invalid))
        return 2
    print("✓ 缓存 %d 条，名册 %d 个节目" % (len(cache), len(roster)))
    for name in (config.DISCOVERY_NAME, config.AHRTV_EPISODES_NAME):
        path = os.path.join(cache_dir, name)
        if os.path.exists(path):
            data = roster_mod.load_json(path)
            if data is None:
                print("✗ %s 无法解析为 JSON" % path)
                return 2
            print("✓ %s（%d 条）" % (name, len(data)))
        else:
            print("· %s 不存在（可选）" % path)
    return 0


def _cmd_update(args):
    from . import update
    try:
        stats = update.run(
            args.cache_dir, days=args.days, workers=args.workers,
            recheck_days=args.recheck_days, dry_run=args.dry_run,
            use_api=not args.no_api)
    except FileNotFoundError as exc:
        print("✗ %s" % exc)
        return 2
    print("增量更新完成：探测 %d 点，新增可读 %d 条，缓存共 %d 条"
          % (stats["probed"], stats["hits"], stats["cache_rows"]))
    if stats.get("new_hits_by_album"):
        for album, n in stats["new_hits_by_album"].items():
            print("  + %-20s %d" % (album, n))
    return 0


def _cmd_build(args):
    from . import episodes, probe, roster as roster_mod, site
    probe_path = os.path.join(args.cache_dir, config.PROBE_CACHE_NAME)
    roster_path = os.path.join(args.cache_dir, config.ROSTER_NAME)
    if not os.path.exists(probe_path) or not os.path.exists(roster_path):
        print("✗ 缓存不完整：需要 %s 与 %s" % (probe_path, roster_path))
        return 2
    cache = probe.ProbeCache(probe_path)
    roster = roster_mod.load_roster(args.cache_dir)
    programs = roster_mod.load_programs(args.cache_dir)
    ahrtv = roster_mod.load_ahrtv(args.cache_dir)
    rows, stats = episodes.collect(cache, roster, programs, ahrtv)
    if not rows:
        print("✗ 缓存里没有可播 episode，拒绝生成空订阅源")
        return 3
    result = site.build_site(
        rows, args.out, _base_url(args.base_url), title=args.title)
    print("构建完成：%d 个节目 / %d 条回听 → %s"
          % (result["albums"], result["episodes"], args.out))
    print("  订阅清单：%s" % result["opml"])
    print("  年份分布：%s" % "，".join("%s:%d" % (y, n)
                                     for y, n in stats["by_year"].items()))
    print("  来源分布：%s" % "，".join("%s:%d" % (k, v)
                                     for k, v in stats["by_source"].items()))
    return 0


def _cmd_all(args):
    code = _cmd_update(args)
    if code != 0:
        return code
    return _cmd_build(args)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="ahshgb",
        description="安徽生活广播回听：增量探测缓存 + 生成 RSS/OPML 订阅源")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p):
        p.add_argument("--cache-dir", default=config.DEFAULT_CACHE_DIR,
                       help="缓存目录（默认 cache）")

    u = sub.add_parser("update", help="增量探测并更新探测缓存")
    add_common(u)
    u.add_argument("--days", type=int, default=config.DEFAULT_CATCHUP_DAYS,
                   help="向前回溯天数（默认 %(default)s）")
    u.add_argument("--workers", type=int, default=config.DEFAULT_WORKERS,
                   help="HEAD 探测并发数（默认 %(default)s）")
    u.add_argument("--recheck-days", type=int, default=config.PROBE_RECHECK_DAYS,
                   help="最近 N 天的未命中点重测（默认 %(default)s）")
    u.add_argument("--dry-run", action="store_true", help="只打印计划，不发请求")
    u.add_argument("--no-api", action="store_true", help="跳过接口，只用名册/缓存")
    u.set_defaults(func=_cmd_update)

    b = sub.add_parser("build", help="从缓存生成站点（RSS feeds + ahshgb.opml）")
    add_common(b)
    b.add_argument("--out", default=config.DEFAULT_SITE_DIR, help="输出目录（默认 site）")
    b.add_argument("--base-url", default=None,
                   help="站点根地址（默认取 PUBLIC_BASE_URL 或 %s）" % config.DEFAULT_BASE_URL)
    b.add_argument("--title", default=config.DEFAULT_TITLE, help="OPML 标题")
    b.set_defaults(func=_cmd_build)

    s = sub.add_parser("stats", help="打印缓存统计")
    add_common(s)
    s.set_defaults(func=lambda a: _print_stats(a.cache_dir))

    c = sub.add_parser("check", help="校验缓存文件是否齐全")
    add_common(c)
    c.set_defaults(func=lambda a: _check(a.cache_dir))

    a = sub.add_parser("all", help="update + build")
    add_common(a)
    a.add_argument("--days", type=int, default=config.DEFAULT_CATCHUP_DAYS)
    a.add_argument("--workers", type=int, default=config.DEFAULT_WORKERS)
    a.add_argument("--recheck-days", type=int, default=config.PROBE_RECHECK_DAYS)
    a.add_argument("--dry-run", action="store_true")
    a.add_argument("--no-api", action="store_true")
    a.add_argument("--out", default=config.DEFAULT_SITE_DIR)
    a.add_argument("--base-url", default=None)
    a.add_argument("--title", default=config.DEFAULT_TITLE)
    a.set_defaults(func=_cmd_all)
    return parser


def main(argv=None):
    _setup_stdio()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
