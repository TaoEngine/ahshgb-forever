# -*- coding: utf-8 -*-
"""阿基米德 PHP 接口的最小封装（只用到取节目表 / 最近回听两件事）。

接口基址固定 ``http://a.ajmide.com/``。数据来源见 skill ``ajmide-radio-audio``。
"""
import json
import time
import urllib.parse
import urllib.request

from . import config, probe


def api_get(path, params=None, *, timeout=30, retries=2, log=None):
    """GET 一个 .php 接口并解析 JSON；失败返回 None（不抛异常，便于夜间任务容错）。"""
    url = config.API_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    last_err = None
    for attempt in range(1, int(retries) + 1):
        req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except Exception as exc:        # noqa: BLE001 - 网络/JSON 问题都重试
            last_err = exc
            if attempt < retries:
                time.sleep(2 * attempt)
    if log:
        log("接口失败 %s: %s" % (path, last_err))
    return None


def _ok_data(resp):
    """接口 code 为 '0' 时返回 data。"""
    if not isinstance(resp, dict) or str(resp.get("code")) != "0":
        return None
    return resp.get("data")


def channel_programs(log=None):
    """取安徽生活广播频率下的「在播节目」及其最近回听。

    返回::

        [{"album": "心花一路\\"fun\\"", "brand_id": 10602253,
          "program_id": 18219, "cover": "https://...",
          "recent": [{"date": "20261102", "hour": 17,
                      "bucket": "c_8894", "url": "http://..."}]}, ...]
    """
    data = _ok_data(api_get("v33/brand_head.php",
                            {"brandId": config.CHANNEL_BRAND_ID, "o": ""}, log=log))
    if not data:
        return []
    programs = []
    for capacity in data.get("capacity") or []:
        if str(capacity.get("capacityId")) != "12":
            continue
        for item in capacity.get("program") or []:
            schema = item.get("schema") or ""
            brand_id = _extract_int(schema, r"brandId=(\d+)")
            if not brand_id:
                continue
            entry = {"album": (item.get("name") or "").strip() or "节目%s" % brand_id,
                     "brand_id": brand_id, "program_id": None,
                     "cover": item.get("image") or None, "recent": []}
            head = _ok_data(api_get("v33/brand_head.php",
                                    {"brandId": brand_id, "o": ""}, log=log))
            if head:
                entry["program_id"] = head.get("programId")
                entry["cover"] = head.get("brand_img") or entry["cover"]
                entry["album"] = (head.get("brand_name") or entry["album"]).strip()
            recent = _ok_data(api_get("v27/getBrandPlayList.php",
                                      {"brandId": brand_id}, log=log))
            for it in (recent or {}).get("list") or []:
                parsed = _recent_episode(it)
                if parsed:
                    entry["recent"].append(parsed)
            programs.append(entry)
    return programs


def _extract_int(text, pattern):
    import re
    m = re.search(pattern, text or "")
    return int(m.group(1)) if m else None


def _recent_episode(item):
    """把 getBrandPlayList 的一条回听转成 {date,hour,bucket,url}。"""
    url = item.get("liveUrl") or ""
    parsed = probe.parse_url(url)
    if not parsed:
        return None
    bucket, date, hour = parsed
    # 以 CDN 直链里的日期为准（比 postTime 更可靠）
    return {"date": date, "hour": hour, "bucket": bucket,
            "url": probe.url_of(bucket, date, hour)}
