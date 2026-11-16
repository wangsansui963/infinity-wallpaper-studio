#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Infinity Wallpaper Studio
=========================

A standalone bulk wallpaper downloader built from the *decoded* wallpaper
library of the "Infinity New Tab Pro" browser extension (CRX3, v11.0.41).

Decoded data source (see analysis/findings.json for the evidence trail):

  List API   GET https://api.inftab.com/v2/get_wallpaper_list
             params: client=pc, page (0-based, 50 items/page), source, tag,
                     color, order (like|_id)
  Image CDN  https://infinitypro-img.infinitynewtab.com/...
             rawSrc is the original; appending
             "?imageView2/2/w/<W>/format/webp/interlace/1" produces a scaled
             WebP thumbnail/preview.

No third-party packages are required: Python 3.9+ standard library only.

Usage
-----
  Infinity Wallpaper Studio.exe     # installed build, no Python required
  python app.py                     # start local web UI (auto opens browser)
  python app.py --no-browser        # start server without opening a browser
  python app.py --cli --pages 5 --source Unsplash --out D:\\Wallpapers

Only download wallpapers you are entitled to use. Be gentle with the public
API: keep concurrency low (default 4) and avoid re-crawling the whole catalog.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
import ssl
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

IS_FROZEN = bool(getattr(sys, "frozen", False))
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
SOURCE_DIR = Path(__file__).resolve().parent

if IS_FROZEN:
    # Installed build: never write next to the exe (Program Files is read-only).
    DATA_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Infinity Wallpaper Studio"
    DEFAULT_OUT = Path.home() / "Pictures" / "Infinity Wallpapers"
else:
    # Source checkout: keep the original layout for start.bat / CLI users.
    DATA_DIR = SOURCE_DIR
    DEFAULT_OUT = SOURCE_DIR / "downloads"
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except Exception:  # noqa: BLE001
    DATA_DIR = Path(os.environ.get("TEMP") or str(Path.home()))

# PyInstaller --noconsole builds have sys.stdout/stderr == None. Redirect them
# to a log file so print() never crashes and startup problems are diagnosable.
if sys.stdout is None or sys.stderr is None:
    try:
        _log = open(DATA_DIR / "launcher.log", "a", encoding="utf-8",
                    errors="replace", buffering=1)
        if sys.stdout is None:
            sys.stdout = _log
        if sys.stderr is None:
            sys.stderr = _log
    except Exception:  # noqa: BLE001
        class _NullWriter:
            def write(self, *args):
                return len(args)

            def flush(self):
                pass

        if sys.stdout is None:
            sys.stdout = _NullWriter()
        if sys.stderr is None:
            sys.stderr = _NullWriter()
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

STATIC_DIR = RESOURCE_DIR / "static"
DB_PATH = DATA_DIR / "state.sqlite3"
FINDINGS_PATH = RESOURCE_DIR / "analysis" / "findings.json"

API_LIST = "https://api.inftab.com/v2/get_wallpaper_list"
CDN_HOSTS = {
    "infinitypro-img.infinitynewtab.com",
    "infinityicon.infinitynewtab.com",
}
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
PAGE_SIZE = 50
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

SOURCES = [
    {"value": "all", "label": "全部来源"},
    {"value": "InfinityLandscape", "label": "Infinity 风景"},
    {"value": "Infinity", "label": "Infinity 漫画"},
    {"value": "bing", "label": "Bing 每日壁纸"},
    {"value": "Unsplash", "label": "Unsplash"},
    {"value": "Life Of Pix", "label": "Life Of Pix"},
    {"value": "MMT", "label": "MMT"},
    {"value": "Realistic Shots", "label": "Realistic Shots"},
    {"value": "Jay Mantri", "label": "Jay Mantri"},
    {"value": "Free Nature Stock", "label": "Free Nature Stock"},
    {"value": "Skitter Photo", "label": "Skitter Photo"},
    {"value": "Startup Stock Photos", "label": "Startup Stock Photos"},
    {"value": "Barn Images", "label": "Barn Images"},
    {"value": "Picography", "label": "Picography"},
]
TAGS = [
    {"value": "", "label": "全部主题"},
    {"value": "nature", "label": "自然"},
    {"value": "ocean", "label": "海洋"},
    {"value": "architecture", "label": "建筑"},
    {"value": "animals", "label": "动物"},
    {"value": "travel", "label": "旅行"},
    {"value": "food-drink", "label": "美食"},
    {"value": "anime", "label": "动漫"},
    {"value": "athletics", "label": "运动"},
    {"value": "technology", "label": "科技"},
    {"value": "street-photography", "label": "街拍"},
]
COLORS = [
    {"value": "", "label": "全部颜色", "hex": ""},
    {"value": "c00018", "label": "红", "hex": "#c00018"},
    {"value": "de8930", "label": "橙", "hex": "#de8930"},
    {"value": "f7d946", "label": "黄", "hex": "#f7d946"},
    {"value": "cbe582", "label": "嫩绿", "hex": "#cbe582"},
    {"value": "506f37", "label": "深绿", "hex": "#506f37"},
    {"value": "60a8d8", "label": "浅蓝", "hex": "#60a8d8"},
    {"value": "184878", "label": "深蓝", "hex": "#184878"},
    {"value": "be7ab9", "label": "紫", "hex": "#be7ab9"},
]
ORDERS = [
    {"value": "", "label": "默认排序"},
    {"value": "like", "label": "按点赞数"},
    {"value": "_id", "label": "按收录顺序"},
]
WIDTHS = [
    {"value": 0, "label": "原始大图（推荐）"},
    {"value": 1280, "label": "1280px · WebP"},
    {"value": 1920, "label": "1920px · WebP"},
    {"value": 2560, "label": "2560px · WebP"},
    {"value": 3840, "label": "3840px · WebP"},
]


# --------------------------------------------------------------------------
# Decoded library client
# --------------------------------------------------------------------------

class ApiError(Exception):
    pass


def http_get(url: str, timeout: int = 30, retries: int = 3) -> tuple[bytes, str]:
    """Plain GET with retry + politeness delay. Returns (body, content-type)."""
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": UA, "Accept": "*/*"},
            )
            with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
                return resp.read(), resp.headers.get("Content-Type", "application/octet-stream")
        except Exception as exc:  # noqa: BLE001 - network errors are expected
            last = exc
            if attempt + 1 < retries:
                time.sleep(0.6 * (attempt + 1))
    raise ApiError(f"请求失败: {url} -> {type(last).__name__}: {last}")


def build_query(options: dict) -> str:
    """Only allow parameters the decoded API accepts (unknown ones are rejected)."""
    params = {"client": "pc", "page": int(options.get("page", 0) or 0)}
    source = (options.get("source") or "all").strip()
    if source and source != "all":
        params["source"] = source
    for key in ("tag", "color", "order"):
        value = (options.get(key) or "").strip()
        if value:
            params[key] = value
    return urllib.parse.urlencode(params)


def fetch_page(options: dict) -> dict:
    body, _ = http_get(f"{API_LIST}?{build_query(options)}")
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise ApiError(f"返回不是 JSON: {exc}") from exc
    if payload.get("code") != 0:
        raise ApiError(f"API {payload.get('code')}: {payload.get('message')}")
    data = payload.get("data") or {}
    return {
        "count": data.get("count", 0),
        "totalPages": data.get("totalPages", 0),
        "nextPage": data.get("nextPage", 1),
        "items": [x for x in (normalize(i) for i in data.get("list") or []) if x],
    }


def parse_dimensions(text: str) -> tuple[int, int]:
    nums = re.findall(r"(\d+)\s*px", text or "", flags=re.I)
    if len(nums) >= 2:
        return int(nums[0]), int(nums[1])
    nums = re.findall(r"\d+", text or "")
    if len(nums) >= 2:
        return int(nums[0]), int(nums[1])
    return 0, 0


def scaled_url(raw: str, width: int) -> str:
    """Qiniu imageView2 processing, exactly as the extension builds previews."""
    if not width:
        return raw
    return f"{raw}?imageView2/2/w/{int(width)}/format/webp/interlace/1"


def normalize(item: dict) -> dict | None:
    src = item.get("src") or {}
    raw = src.get("rawSrc") or item.get("rawUrl") or item.get("url") or ""
    if not raw or not raw.startswith("http"):
        return None
    width, height = parse_dimensions(item.get("dimensions") or "")
    rid = str(item.get("_id") or hashlib.md5(raw.encode()).hexdigest())
    return {
        "id": rid,
        "imgId": str(item.get("imgId") or ""),
        "source": str(item.get("source") or ""),
        "like": int(item.get("like") or 0),
        "rate": item.get("rate") or 0,
        "dimensions": item.get("dimensions") or "",
        "width": width,
        "height": height,
        "colors": item.get("colors") or [],
        "rawUrl": raw,
        "thumbUrl": scaled_url(raw, 420),
    }


def matches(rec: dict, filters: dict) -> bool:
    """Client-side refinement: the API has no free-text / resolution filter."""
    q = (filters.get("q") or "").strip().lower()
    if q and q not in f"{rec['source']} {rec['imgId']} {rec['id']} {rec['dimensions']}".lower():
        return False
    min_w = int(filters.get("minWidth") or 0)
    if min_w and rec["width"] and rec["width"] < min_w:
        return False
    min_like = int(filters.get("minLikes") or 0)
    if min_like and rec["like"] < min_like:
        return False
    return True


# --------------------------------------------------------------------------
# Download state / persistence
# --------------------------------------------------------------------------

class StateDB:
    def __init__(self, path: Path):
        self._lock = threading.Lock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS downloads (
                   id TEXT PRIMARY KEY, img_id TEXT, source TEXT, url TEXT,
                   path TEXT, size INTEGER, width INTEGER, height INTEGER, ts REAL)"""
        )
        self.db.commit()

    def has(self, rid: str) -> bool:
        with self._lock:
            cur = self.db.execute("SELECT 1 FROM downloads WHERE id=?", (rid,))
            return cur.fetchone() is not None

    def add(self, rec: dict, path: Path, size: int) -> None:
        with self._lock:
            self.db.execute(
                "INSERT OR REPLACE INTO downloads VALUES (?,?,?,?,?,?,?,?,?)",
                (rec["id"], rec["imgId"], rec["source"], rec["rawUrl"],
                 str(path), size, rec["width"], rec["height"], time.time()),
            )
            self.db.commit()

    def count(self) -> int:
        with self._lock:
            return self.db.execute("SELECT COUNT(*) FROM downloads").fetchone()[0]

    def bytes(self) -> int:
        with self._lock:
            return self.db.execute("SELECT COALESCE(SUM(size),0) FROM downloads").fetchone()[0]

    def forget(self, rid: str) -> None:
        with self._lock:
            self.db.execute("DELETE FROM downloads WHERE id=?", (rid,))
            self.db.commit()


DB = StateDB(DB_PATH)


def sniff_ext(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:2] == b"BM":
        return "bmp"
    return None


def safe_filename(rec: dict, ext: str) -> str:
    source = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", rec.get("source") or "wallpaper")
    img_id = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", rec.get("imgId") or "")
    parts = [source.strip("_") or "wallpaper", img_id.strip("_"), rec["id"][:8]]
    name = "_".join(p for p in parts if p)
    return name[:150] + "." + ext


def write_manifest(out: Path, records: list[dict]) -> None:
    """Append-only CSV of everything this tool has downloaded (deduplicated)."""
    path = out / "wallpapers.csv"
    header = ["id", "imgId", "source", "like", "dimensions", "rawUrl"]
    exists = path.exists() and path.stat().st_size > 0
    known: set[str] = set()
    if exists:
        try:
            with path.open("r", newline="", encoding="utf-8-sig") as fh:
                reader = csv.reader(fh)
                next(reader, None)
                known = {row[0] for row in reader if row}
        except Exception:  # noqa: BLE001
            known = set()
    with path.open("a" if exists else "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        if not exists:
            writer.writerow(header)
        for r in records:
            if r["id"] in known:
                continue
            writer.writerow([r["id"], r["imgId"], r["source"], r["like"],
                             r["dimensions"], r["rawUrl"]])
            known.add(r["id"])


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------

@dataclass
class Job:
    id: str
    out_dir: str
    width: int = 0
    concurrency: int = 4
    skip_existing: bool = True
    overwrite: bool = False
    save_manifest: bool = True
    phase: str = "queued"
    status: str = "running"
    total: int = 0
    done: int = 0
    ok: int = 0
    failed: int = 0
    skipped: int = 0
    bytes_done: int = 0
    message: str = ""
    logs: list = field(default_factory=list)
    cancel: threading.Event = field(default_factory=threading.Event)
    lock: threading.RLock = field(default_factory=threading.RLock)
    started: float = field(default_factory=time.time)

    def log(self, text: str) -> None:
        with self.lock:
            self.logs.append({"t": round(time.time() - self.started, 1), "m": text})
            if len(self.logs) > 250:
                self.logs = self.logs[-250:]

    def public(self) -> dict:
        with self.lock:
            return {
                "id": self.id, "phase": self.phase, "status": self.status,
                "total": self.total, "done": self.done, "ok": self.ok,
                "failed": self.failed, "skipped": self.skipped,
                "bytes": self.bytes_done, "message": self.message,
                "outDir": self.out_dir, "logs": list(self.logs[-60:]),
                "elapsed": round(time.time() - self.started, 1),
            }


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()


def download_one(job: Job, rec: dict) -> tuple[str, int]:
    """Download one wallpaper. Returns (status, bytes_written)."""
    if job.cancel.is_set():
        return "cancelled", 0
    out = Path(job.out_dir)
    url = scaled_url(rec["rawUrl"], job.width)
    stem = safe_filename(rec, "*").rsplit(".", 1)[0]
    if job.skip_existing and not job.overwrite:
        existing = [p for p in out.glob(stem + ".*") if p.stat().st_size > 1024]
        if existing or DB.has(rec["id"]):
            return "skipped", 0

    body, ctype = b"", ""
    for attempt in range(3):
        try:
            body, ctype = http_get(url, timeout=90, retries=1)
            break
        except ApiError as exc:
            if attempt == 2:
                raise
            time.sleep(0.8 * (attempt + 1))
    ext = sniff_ext(body)
    if not ext or len(body) < 1024:
        raise ApiError(f"不是有效图片 ({ctype}, {len(body)} bytes)")
    if "image/" not in ctype.lower():
        raise ApiError(f"非图片响应: {ctype}")

    path = out / safe_filename(rec, ext)
    tmp = out / (path.name + ".part")
    tmp.write_bytes(body)
    if path.exists() and job.overwrite:
        path.unlink()
    os.replace(str(tmp), str(path))
    DB.add(rec, path, len(body))
    return "ok", len(body)


def run_job(job: Job, items: list[dict] | None, criteria: dict | None, pages: int, start_page: int) -> None:
    try:
        out = Path(job.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        records = list(items or [])
        if criteria is not None:
            job.phase = "fetching"
            job.message = "正在读取图库目录"
            seen = {r["id"] for r in records}
            for i in range(max(1, pages)):
                if job.cancel.is_set():
                    break
                page = start_page + i
                job.message = f"正在抓取第 {page + 1} 页 / 共 {start_page + pages} 页"
                result = fetch_page({**criteria, "page": page})
                fresh = [r for r in result["items"] if r["id"] not in seen and matches(r, criteria)]
                for r in fresh:
                    seen.add(r["id"])
                records.extend(fresh)
                job.log(f"第 {page + 1} 页: 返回 {len(result['items'])} 张, 新增 {len(fresh)} 张 (图库共 {result['count']} 张)")
                if not result["items"] or page + 1 >= result["totalPages"]:
                    break
                time.sleep(0.2)  # be polite to the public API
        if job.cancel.is_set():
            job.status = "cancelled"
            job.message = "已取消"
            return

        # deterministic de-duplication inside this job
        uniq, seen = [], set()
        for r in records:
            if r["id"] not in seen:
                seen.add(r["id"])
                uniq.append(r)
        records = uniq
        job.total = len(records)
        job.phase = "downloading"
        job.message = f"下载中 · 并发 {job.concurrency}"
        if not records:
            job.status = "completed"
            job.message = "没有符合条件的壁纸"
            return

        def work(rec):
            try:
                res, size = download_one(job, rec)
                return rec, res, size, None
            except Exception as exc:  # noqa: BLE001
                return rec, "failed", 0, f"{type(exc).__name__}: {exc}"

        with ThreadPoolExecutor(max_workers=max(1, min(8, job.concurrency))) as pool:
            futures = [pool.submit(work, r) for r in records]
            for fut in as_completed(futures):
                if job.cancel.is_set():
                    for f in futures:
                        f.cancel()
                    break
                rec, res, size, err = fut.result()
                with job.lock:
                    job.done += 1
                    if res == "ok":
                        job.ok += 1
                        job.bytes_done += size
                    elif res == "skipped":
                        job.skipped += 1
                    elif res == "cancelled":
                        pass
                    else:
                        job.failed += 1
                # log() takes the same lock - always call it outside the block
                if res == "ok":
                    job.log(f"✓ {rec['source']} / {rec['imgId']} ({rec['dimensions']})")
                elif res == "failed":
                    job.log(f"✗ {rec['imgId']}: {err}")

        if job.save_manifest:
            try:
                write_manifest(out, records)
            except Exception as exc:  # noqa: BLE001
                job.log(f"清单写入失败: {exc}")

        job.status = "cancelled" if job.cancel.is_set() else "completed"
        job.message = f"完成：成功 {job.ok} · 跳过 {job.skipped} · 失败 {job.failed}"
        job.log(job.message)
    except Exception as exc:  # noqa: BLE001
        job.status = "failed"
        job.message = f"{type(exc).__name__}: {exc}"
        job.log(job.message)


def create_job(payload: dict) -> Job:
    jid = time.strftime("%H%M%S") + "-" + hashlib.md5(str(time.time()).encode()).hexdigest()[:5]
    job = Job(
        id=jid,
        out_dir=os.path.abspath(payload.get("outDir") or str(DEFAULT_OUT)),
        width=int(payload.get("width") or 0),
        concurrency=int(payload.get("concurrency") or 4),
        skip_existing=bool(payload.get("skipExisting", True)),
        overwrite=bool(payload.get("overwrite", False)),
        save_manifest=bool(payload.get("saveManifest", True)),
    )
    items = payload.get("items") or None
    criteria = payload.get("criteria") or None
    pages = int(payload.get("pages") or 1)
    start_page = int(payload.get("startPage") or 0)
    with JOBS_LOCK:
        JOBS[jid] = job
        if len(JOBS) > 20:
            for k in list(JOBS)[:-20]:
                JOBS.pop(k, None)
    thread = threading.Thread(
        target=run_job, args=(job, items, criteria, pages, start_page), daemon=True
    )
    thread.start()
    return job


# --------------------------------------------------------------------------
# Local HTTP server
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "WallpaperStudio/1.0"

    def log_message(self, fmt, *args):
        if os.environ.get("WS_VERBOSE"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ---- helpers ---------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _file(self, path: Path, ctype: str):
        if not path.exists() or not path.is_file():
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        self._send(200, path.read_bytes(), ctype)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    # ---- GET -------------------------------------------------------------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)
        try:
            if path in ("/", "/index.html"):
                return self._file(RESOURCE_DIR / "index.html", "text/html; charset=utf-8")
            if path.startswith("/static/"):
                name = path[len("/static/"):]
                target = (STATIC_DIR / name).resolve()
                if not str(target).startswith(str(STATIC_DIR.resolve())):
                    return self._send(403, b"forbidden", "text/plain")
                ctype = {
                    ".js": "application/javascript; charset=utf-8",
                    ".css": "text/css; charset=utf-8",
                    ".svg": "image/svg+xml",
                }.get(target.suffix.lower(), "application/octet-stream")
                return self._file(target, ctype)
            if path == "/api/config":
                return self._json({
                    "api": API_LIST, "pageSize": PAGE_SIZE,
                    "sources": SOURCES, "tags": TAGS, "colors": COLORS,
                    "orders": ORDERS, "widths": WIDTHS,
                    "defaultOut": str(DEFAULT_OUT),
                    "downloaded": DB.count(), "downloadedBytes": DB.bytes(),
                })
            if path == "/api/wallpapers":
                opts = {k: v[0] for k, v in qs.items()}
                result = fetch_page(opts)
                result["items"] = [r for r in result["items"] if matches(r, opts)]
                result["page"] = int(opts.get("page") or 0)
                return self._json(result)
            if path == "/api/image":
                url = qs.get("url", [""])[0]
                host = urllib.parse.urlparse(url).hostname or ""
                if host not in CDN_HOSTS:
                    return self._send(403, b"host not allowed", "text/plain")
                body, ctype = http_get(url, timeout=40, retries=2)
                return self._send(200, body, ctype or "image/jpeg",
                                  {"Cache-Control": "public, max-age=86400"})
            if path == "/api/jobs":
                jid = qs.get("id", [""])[0]
                with JOBS_LOCK:
                    if jid and jid in JOBS:
                        return self._json(JOBS[jid].public())
                    return self._json({"jobs": [j.public() for j in JOBS.values()][-10:]})
            if path == "/api/stats":
                return self._json({"downloaded": DB.count(), "bytes": DB.bytes()})
            if path == "/api/findings":
                if FINDINGS_PATH.exists():
                    return self._json(json.loads(FINDINGS_PATH.read_text("utf-8")))
                return self._json({})
            return self._send(404, b"not found", "text/plain; charset=utf-8")
        except ApiError as exc:
            return self._json({"error": str(exc)}, 502)
        except Exception as exc:  # noqa: BLE001
            return self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    # ---- POST ------------------------------------------------------------
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        payload = self._body()
        try:
            if path == "/api/download":
                items = payload.get("items") or []
                criteria = payload.get("criteria")
                if not items and not criteria:
                    return self._json({"error": "没有可下载的壁纸"}, 400)
                job = create_job(payload)
                return self._json(job.public())
            if path == "/api/cancel":
                with JOBS_LOCK:
                    job = JOBS.get(payload.get("id") or "")
                if not job:
                    return self._json({"error": "任务不存在"}, 404)
                job.cancel.set()
                job.message = "正在取消…"
                return self._json(job.public())
            if path == "/api/open-folder":
                folder = Path(os.path.abspath(payload.get("outDir") or str(DEFAULT_OUT)))
                folder.mkdir(parents=True, exist_ok=True)
                if sys.platform.startswith("win"):
                    os.startfile(str(folder))  # noqa: S606 - local user action
                elif sys.platform == "darwin":
                    os.system(f'open "{folder}"')
                else:
                    os.system(f'xdg-open "{folder}"')
                return self._json({"ok": True, "path": str(folder)})
            if path == "/api/reset-history":
                with DB._lock:
                    DB.db.execute("DELETE FROM downloads")
                    DB.db.commit()
                return self._json({"ok": True})
            return self._send(404, b"not found", "text/plain")
        except Exception as exc:  # noqa: BLE001
            return self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)


def find_running_instance(port: int) -> int | None:
    """Reuse an already running studio instead of starting a second server."""
    for candidate in range(port, port + 12):
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{candidate}/api/config", timeout=0.35
            ) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if data.get("api") == API_LIST and data.get("pageSize") == PAGE_SIZE:
                return candidate
        except Exception:  # noqa: BLE001 - port closed or another service
            continue
    return None


def serve(port: int, open_browser: bool) -> None:
    existing = find_running_instance(port)
    if existing is not None:
        url = f"http://127.0.0.1:{existing}/"
        print("=" * 62)
        print("  Infinity Wallpaper Studio 已经在运行")
        print(f"  打开: {url}")
        print("  不会重复启动服务；需要重启请先关闭原窗口。")
        print("=" * 62)
        if open_browser:
            webbrowser.open(url)
        return

    httpd = None
    for candidate in range(port, port + 12):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", candidate), Handler)
            port = candidate
            break
        except OSError:
            continue
    if httpd is None:
        raise SystemExit("无法绑定本地端口")
    url = f"http://127.0.0.1:{port}/"
    print("=" * 62)
    print("  Infinity Wallpaper Studio · 批量壁纸下载器")
    print(f"  打开: {url}")
    print(f"  默认下载目录: {DEFAULT_OUT}")
    print("  关闭窗口或按 Ctrl+C 结束")
    print("=" * 62)
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()


# --------------------------------------------------------------------------
# Headless CLI
# --------------------------------------------------------------------------

def run_cli(args) -> None:
    criteria = {
        "source": args.source, "tag": args.tag, "color": args.color,
        "order": args.order, "q": args.query, "minWidth": args.min_width,
        "minLikes": args.min_likes,
    }
    out = Path(os.path.abspath(args.out)).resolve()
    out.mkdir(parents=True, exist_ok=True)
    print(f"[图库] 抓取 {args.pages} 页 (每页 {PAGE_SIZE} 张) -> {out}")
    records, seen = [], set()
    for i in range(args.pages):
        page = args.start_page + i
        result = fetch_page({**criteria, "page": page})
        fresh = [r for r in result["items"] if r["id"] not in seen and matches(r, criteria)]
        for r in fresh:
            seen.add(r["id"])
        records.extend(fresh)
        print(f"  第 {page + 1:>4} 页: {len(result['items']):>2} 张, 采纳 {len(fresh):>2} 张 "
              f"(库内 {result['count']} 张)")
        if not result["items"] or page + 1 >= result["totalPages"]:
            break
        time.sleep(0.25)
    if args.limit:
        records = records[:args.limit]
    print(f"[下载] {len(records)} 张, 并发 {args.concurrency}, 宽度 {args.width or '原图'}")
    if args.dry_run:
        for r in records[:10]:
            print(f"  {r['source']:<20} {r['imgId']:<28} {r['rawUrl']}")
        print("  (--dry-run: 未实际下载)")
        return

    job = Job(
        id="cli", out_dir=str(out), width=args.width,
        concurrency=args.concurrency, skip_existing=not args.overwrite,
        overwrite=args.overwrite, save_manifest=True,
    )
    job.total = len(records)
    with ThreadPoolExecutor(max_workers=max(1, min(8, args.concurrency))) as pool:
        futures = {pool.submit(download_one, job, r): r for r in records}
        for fut in as_completed(futures):
            rec = futures[fut]
            try:
                res, _size = fut.result()
            except Exception as exc:  # noqa: BLE001
                res, msg = "failed", str(exc)
            else:
                msg = ""
            if res == "ok":
                job.ok += 1
            elif res == "skipped":
                job.skipped += 1
            else:
                job.failed += 1
            print(f"  [{job.ok + job.failed + job.skipped}/{len(records)}] "
                  f"{res:<8} {rec['source']} / {rec['imgId']} {msg[:80]}")
    write_manifest(out, records)
    print(f"[完成] 成功 {job.ok} · 跳过 {job.skipped} · 失败 {job.failed} -> {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Infinity Wallpaper Studio")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--cli", action="store_true", help="命令行批量下载模式")
    parser.add_argument("--pages", type=int, default=1, help="抓取页数 (每页 50 张)")
    parser.add_argument("--start-page", type=int, default=0, help="起始页 (0 基)")
    parser.add_argument("--source", default="all")
    parser.add_argument("--tag", default="")
    parser.add_argument("--color", default="")
    parser.add_argument("--order", default="")
    parser.add_argument("--query", default="")
    parser.add_argument("--min-width", type=int, default=0)
    parser.add_argument("--min-likes", type=int, default=0)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--width", type=int, default=0, help="缩放宽度, 0=原图")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.cli:
        run_cli(args)
    else:
        serve(args.port, not args.no_browser)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as _exc:  # noqa: BLE001 - surface startup errors in windowed builds
        _msg = f"{type(_exc).__name__}: {_exc}"
        print("[FATAL] " + _msg, flush=True)
        if IS_FROZEN:
            try:
                import tkinter.messagebox as _mb

                _mb.showerror(
                    "Infinity Wallpaper Studio",
                    "启动失败：\n" + _msg + f"\n\n日志文件：{DATA_DIR / 'launcher.log'}",
                )
            except Exception:  # noqa: BLE001
                pass
        sys.exit(1)
