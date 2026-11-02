# -*- coding: utf-8 -*-
"""全局常量与默认路径。

所有时间都以北京时间（UTC+8，无夏令时）为准。
阿基米德接口用 http:// 而不是 https://（部分环境 TLS 握手失败，HTTP 全站可用）。
"""
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------- 频率信息
CHANNEL_BRAND_ID = 10609075          # 安徽生活广播（频率品牌）
CHANNEL_NAME = "安徽生活广播"
CHANNEL_ALIAS = "安徽城市之声"        # 2016-03-07 改版前的台名
AHRTV_WINDOW = ("20200101", "20221014")   # ahrtv 有音频的窗口（历史补源）

# ---------------------------------------------------------------- 网络
API_BASE = "http://a.ajmide.com/"
CDN_HOST = "ia-bk-i.ajmide.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------- 时区
TZ = timezone(timedelta(hours=8))

# ---------------------------------------------------------------- 默认参数
DEFAULT_BASE_URL = "https://taoengine.github.io/ahshgb-forever"   # 与 README 中的地址一致
DEFAULT_TITLE = "安徽生活广播(安徽城市之声) 整期回听"
DEFAULT_CACHE_DIR = "cache"
DEFAULT_SITE_DIR = "site"

DEFAULT_CATCHUP_DAYS = 120     # 每次都探测最近 N 天，缓存里已有的点会跳过
DEFAULT_WORKERS = 8            # HEAD 探测并发数（skill 实测 8~12 安全）
PROBE_TIMEOUT = 15
PROBE_MISS_TTL_DAYS = 30       # 未命中缓存 30 天后重测（归档态/可读窗口会滚动）
PROBE_RECHECK_DAYS = 3         # 最近 3 天的未命中点每次都重测（防延迟上传）

# ---------------------------------------------------------------- 缓存文件名
PROBE_CACHE_NAME = "probe_cache.csv"
ROSTER_NAME = "roster.json"
DISCOVERY_NAME = "discovery.json"
AHRTV_EPISODES_NAME = "ahrtv_episodes.json"
PROGRAMS_NAME = "programs.json"       # 每晚从接口刷新的「在播节目 → 桶/时段」映射

# ---------------------------------------------------------------- 封面
COVER_URL = (
    "https://imagev2.xmcdn.com/storages/99c7-audiofreehighqps/DE/08/"
    "GKwRIUEHzoM9AAB23gH7yZtk.jpg"
)


def today_beijing():
    """北京时间的今天（date）。"""
    return datetime.now(TZ).date()


def now_beijing():
    """北京时间的当前时间（aware datetime）。"""
    return datetime.now(TZ)
