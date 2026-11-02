# -*- coding: utf-8 -*-
"""ahshgb-forever —— 安徽生活广播（安徽城市之声）回听缓存与播客订阅源生成工具。

职责：
1. 每晚探测阿基米德 CDN，把新出现的整期录音增量写入 ``cache/probe_cache.csv``；
2. 从缓存合并阿基米德 + ahrtv 两个来源，生成播客 RSS 订阅源与 OPML 清单。
"""

__version__ = "0.1.0"
