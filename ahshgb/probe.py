# -*- coding: utf-8 -*-
"""阿基米德 CDN 探测缓存（probe_cache.csv）的读写与 HEAD 探测。

缓存格式与 ``ajmide-radio-audio`` skill 保持一致::

    bucket,date,hour,status,size_mb,checked_at,source
    c_8894,20260921,17,200,21.17,2026-09-27 23:25:11,probe

状态约定：
* ``200/206`` = 文件在、可读；
* ``403``     = 文件在，但 OSS 归档锁定（InvalidObjectState），匿名读不了；
* ``404``     = 文件不存在；
* ``0``       = 网络异常（未定，下次重试）。

规则：
* 命中（200/206）永久有效；
* 未命中（403/404）默认 30 天后重测；最近 ``recheck_days`` 天内的一律重测
  （CDN 上传有延迟，昨天探不到不代表今天探不到）。
"""
import concurrent.futures as cf
import csv
import datetime as dt
import io
import os
import re
import urllib.error
import urllib.request

from . import config

FIELDS = ["bucket", "date", "hour", "status", "size_mb", "checked_at", "source"]
HIT_STATUS = (200, 206)
STATUS_TEXT = {200: "可读", 206: "可读", 403: "归档锁", 404: "无文件", 0: "异常"}

_URL_RE = re.compile(
    r"//(?P<host>[^/]+)/(?P<bucket>c_\d+)/(?P<d8>\d{8})/"
    r"(?P<prefix>[A-Za-z0-9]+)_\d{8}_(?P<hhmm>\d{4})\.[A-Za-z0-9]+"
)


# ---------------------------------------------------------------- 基础工具
def norm_bucket(bucket):
    """统一成 ``c_1234`` 形式。"""
    b = str(bucket).strip()
    return b if b.startswith("c_") else "c_" + b


def norm_date(value):
    """把 date/datetime/'YYYY-MM-DD'/'YYYYMMDD' 统一成 'YYYYMMDD'。"""
    if isinstance(value, dt.datetime):
        return value.strftime("%Y%m%d")
    if isinstance(value, dt.date):
        return value.strftime("%Y%m%d")
    s = str(value).strip()
    return s.replace("-", "").replace("/", "")


def url_of(bucket, date, hour):
    """拼出某天的整期录音直链（http，避免部分环境 TLS 握手失败）。"""
    b = norm_bucket(bucket)
    prefix = b[2:]
    d8 = norm_date(date)
    return "http://%s/%s/%s/%s_%s_%02d00.m4a" % (
        config.CDN_HOST, b, d8, prefix, d8, int(hour))


def parse_url(url):
    """从 CDN 直链解析 ``(bucket, 'YYYYMMDD', hour)``；解析失败返回 None。"""
    m = _URL_RE.search(url or "")
    if not m:
        return None
    return m.group("bucket"), m.group("d8"), int(m.group("hhmm")[:2])


def parse_checked_at(text):
    """解析 checked_at 字段；失败返回 None。"""
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(str(text).strip(), fmt)
        except ValueError:
            continue
    return None


def _http_head(url, timeout=config.PROBE_TIMEOUT):
    """HEAD 探测，返回 ``(status, size_bytes)``；网络异常返回 ``(0, 0)``。"""
    req = urllib.request.Request(
        url, method="HEAD", headers={"User-Agent": config.USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, int(resp.headers.get("Content-Length") or 0)
    except urllib.error.HTTPError as exc:
        return exc.code, 0
    except Exception:                       # noqa: BLE001 - 网络问题一律记为 0
        return 0, 0


# ---------------------------------------------------------------- 缓存
class ProbeCache:
    """probe_cache.csv 的内存表示：保持原始行顺序，新结果追加到末尾。

    * 读入时后出现的重复行覆盖先前的；
    * 写回时保持原顺序，因此每日增量只会在文件末尾追加，git diff 干净；
    * 不用「没大小」的记录覆盖「有大小」的同类记录。
    """

    def __init__(self, path):
        self.path = path
        self.rows = []                  # list[list[str]]，7 个字段
        self.index = {}                 # (bucket, date, hour) -> row index
        self.invalid = 0                # 解析失败被跳过的行数（check 会提示）
        self.newline = "\r\n"           # 跟随原文件的行尾，避免跨平台整文件 diff
        self.load()

    # -- 读取 ------------------------------------------------------
    def load(self):
        self.rows = []
        self.index = {}
        self.invalid = 0
        if not os.path.exists(self.path):
            return self
        with open(self.path, "rb") as fh:
            head = fh.read(65536)
        self.newline = "\r\n" if b"\r\n" in head else "\n"
        with open(self.path, encoding="utf-8-sig", newline="") as fh:
            reader = csv.reader(fh)
            header = next(reader, None)
            if header and [h.strip() for h in header] != FIELDS:
                # 容错：按字段名映射，缺列补空
                cols = [h.strip() for h in header]
                for raw in reader:
                    rec = dict(zip(cols, raw))
                    self._append_parsed(rec, [rec.get(f, "") for f in FIELDS])
            else:
                for raw in reader:
                    if len(raw) < len(FIELDS):
                        raw = raw + [""] * (len(FIELDS) - len(raw))
                    raw = raw[:len(FIELDS)]
                    self._append_parsed(dict(zip(FIELDS, raw)), raw)
        return self

    def _append_parsed(self, rec, raw_row=None):
        """读入一行；``raw_row`` 用于原样保留旧行的文本格式（git diff 只出现新行）。"""
        try:
            bucket = norm_bucket(rec.get("bucket"))
            date = norm_date(rec.get("date"))
            dt.datetime.strptime(date, "%Y%m%d")     # 日期必须真实存在
            hour = int(float(str(rec.get("hour", "0")).strip() or 0))
            status = int(float(str(rec.get("status", "0")).strip() or 0))
            size_mb = float(str(rec.get("size_mb", "0") or 0))
        except (TypeError, ValueError):
            self.invalid += 1
            return
        if not bucket:
            self.invalid += 1
            return
        row = list(raw_row) if raw_row is not None else [
            bucket, date, str(hour), str(status),
            ("%.2f" % size_mb), str(rec.get("checked_at") or ""),
            str(rec.get("source") or "")]
        self._store((bucket, date, hour), row)

    def _store(self, key, row):
        old = self.index.get(key)
        if old is None:
            self.index[key] = len(self.rows)
            self.rows.append(row)
        else:
            self.rows[old] = row

    # -- 查询/写入 -------------------------------------------------
    def get(self, bucket, date, hour):
        """返回 ``(status, size_mb, checked_at, source)`` 或 None。"""
        idx = self.index.get((norm_bucket(bucket), norm_date(date), int(hour)))
        if idx is None:
            return None
        row = self.rows[idx]
        return int(row[3]), float(row[4] or 0), row[5], row[6]

    def put(self, bucket, date, hour, status, size_bytes, source="probe", when=None):
        """写入一条探测结果（原地更新已有行，否则追加）。"""
        b = norm_bucket(bucket)
        d = norm_date(date)
        h = int(hour)
        mb = round((size_bytes or 0) / 1048576.0, 2)
        checked = when or config.now_beijing().strftime("%Y-%m-%d %H:%M:%S")
        key = (b, d, h)
        old_idx = self.index.get(key)
        if old_idx is not None:
            old = self.rows[old_idx]
            old_status, old_mb = int(old[3]), float(old[4] or 0)
            # 别用「没大小」的记录覆盖「有大小」的同类记录
            if mb == 0 and old_mb > 0 and int(status) == old_status:
                return key
            row = [b, d, str(h), str(int(status)), "%.2f" % mb, checked, source]
            self.rows[old_idx] = row
        else:
            self._store(key, [b, d, str(h), str(int(status)),
                              "%.2f" % mb, checked, source])
        return key

    def save(self):
        """原子写回 CSV（保留源文件的行顺序）。"""
        parent = os.path.dirname(os.path.abspath(self.path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        buf = io.StringIO(newline="")
        writer = csv.writer(buf, lineterminator=self.newline)
        writer.writerow(FIELDS)
        writer.writerows(self.rows)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:
            fh.write(buf.getvalue())
        os.replace(tmp, self.path)

    # -- 便捷 ------------------------------------------------------
    def __len__(self):
        return len(self.rows)

    def hits(self):
        """遍历所有命中行 ``(bucket, date, hour, size_mb)``。"""
        for row in self.rows:
            if int(row[3]) in HIT_STATUS:
                yield row[0], row[1], int(row[2]), float(row[4] or 0)


# ---------------------------------------------------------------- 增量探测
def needs_probe(cache, bucket, date, hour, *,
                today=None, recheck_days=config.PROBE_RECHECK_DAYS,
                miss_ttl_days=config.PROBE_MISS_TTL_DAYS):
    """判断某个 (桶, 日期, 时段) 是否需要（重新）发请求。"""
    row = cache.get(bucket, date, hour)
    if row is None:
        return True
    status, _mb, checked_at, _src = row
    if status in HIT_STATUS:
        return False
    checked = parse_checked_at(checked_at)
    if checked is None:
        return True
    today = today or config.today_beijing()
    d = dt.datetime.strptime(norm_date(date), "%Y%m%d").date()
    if d >= today - dt.timedelta(days=int(recheck_days)):
        return True                     # 最近的未命中：每次都重测（防上传延迟）
    age_days = (config.now_beijing().replace(tzinfo=None) - checked).days
    return age_days > int(miss_ttl_days)


def probe_many(points, cache, workers=config.DEFAULT_WORKERS,
               timeout=config.PROBE_TIMEOUT, log=None, save_every=500):
    """批量 HEAD 探测并写缓存。

    ``points`` 为 ``(bucket, 'YYYYMMDD', hour)`` 可迭代对象（可重复，内部去重）。
    返回 ``{(bucket, date, hour): (status, size_mb)}``。
    """
    todo = []
    seen = set()
    for bucket, date, hour in points:
        key = (norm_bucket(bucket), norm_date(date), int(hour))
        if key in seen:
            continue
        seen.add(key)
        todo.append(key)

    result = {}
    if not todo:
        return result
    if log:
        log("开始探测 %d 个点（%d 并发）…" % (len(todo), workers))

    def work(key):
        return key, _http_head(url_of(*key), timeout=timeout)

    done = 0
    try:
        with cf.ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
            for key, (status, size) in pool.map(work, todo):
                cache.put(key[0], key[1], key[2], status, size)
                result[key] = (status, round(size / 1048576.0, 2))
                done += 1
                if save_every and done % int(save_every) == 0:
                    cache.save()
                    if log:
                        log("  已探测 %d/%d，缓存已落盘" % (done, len(todo)))
    finally:
        cache.save()                    # 中途异常也要落盘，已探测的不白测
    if log:
        hits = sum(1 for st, _ in result.values() if st in HIT_STATUS)
        log("探测完成：%d 点，其中可读 %d 个" % (len(result), hits))
    return result
