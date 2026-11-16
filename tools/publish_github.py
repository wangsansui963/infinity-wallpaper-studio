#!/usr/bin/env python3
"""把 wallpaper-studio 发布到 GitHub（仅依赖 api.github.com，不需要 git push）。

用法:
    GH_TOKEN=ghp_xxx python tools/publish_github.py [--repo NAME] [--private]

做的事:
  1. 校验 token，读取账号信息
  2. 创建仓库（已存在则复用）
  3. 通过 Git Data API 一次性提交所有受版本控制的文件（单个 commit）
  4. 创建 Release v1.0.0 并上传 dist/ 下的安装包与绿色版
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
ROOT = pathlib.Path(__file__).resolve().parent.parent

# 需要发布的文件（相对项目根目录）
FILES = [
    ".gitattributes",
    ".gitignore",
    "README.md",
    "app.py",
    "index.html",
    "installer.nsi",
    "start.bat",
    "static/app.js",
    "static/style.css",
    "analysis/findings.json",
    "assets/app.ico",
    "assets/app.png",
    "docs/screenshot.png",
    "tools/publish_github.py",
]

COMMIT_MSG = """feat: Infinity Wallpaper Studio 1.0.0

从 Infinity New Tab Pro (v11.0.41) 插件中解码壁纸图库接口，并实现批量下载工具。

- 解码: /v2/get_wallpaper_list 公开只读接口，51,632 张 / 1,033 页
- 后端: Python 标准库 (http.server + urllib + sqlite3)，零第三方依赖
- 界面: 本地 Web UI，多维筛选、缩略图浏览、并发下载、断点去重
- 打包: PyInstaller 单文件 exe + NSIS 简体中文安装程序
- 证据: analysis/findings.json 共 13 条编号证据
"""


def die(msg: str, code: int = 1) -> None:
    print(f"[失败] {msg}")
    raise SystemExit(code)


class GH:
    def __init__(self, token: str) -> None:
        self.token = token

    def request(self, method: str, path_or_url: str, payload=None, raw=None,
                ctype="application/json", timeout=60, retries=3):
        url = path_or_url if path_or_url.startswith("http") else API + path_or_url
        body = raw
        if payload is not None:
            body = json.dumps(payload).encode()
        last = None
        for attempt in range(retries):
            req = urllib.request.Request(url, data=body, method=method)
            req.add_header("Authorization", f"Bearer {self.token}")
            req.add_header("Accept", "application/vnd.github+json")
            req.add_header("User-Agent", "wallpaper-studio-publisher")
            req.add_header("X-GitHub-Api-Version", "2022-11-28")
            if body:
                req.add_header("Content-Type", ctype)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    data = r.read()
                    return json.loads(data) if data else {}
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:400]
                last = f"HTTP {exc.code}: {detail}"
                if exc.code in (403, 429) and attempt < retries - 1:
                    time.sleep(3 * (attempt + 1))
                    continue
                die(f"{method} {url} -> {last}")
            except Exception as exc:  # noqa: BLE001
                last = f"{type(exc).__name__}: {exc}"
                if attempt < retries - 1:
                    time.sleep(2)
                    continue
                die(f"{method} {url} -> {last}")
        die(f"{method} {url} -> {last}")


def collect_files() -> list[tuple[str, pathlib.Path]]:
    out = []
    for rel in FILES:
        p = ROOT / rel
        if not p.is_file():
            print(f"[警告] 缺少文件，跳过: {rel}")
            continue
        out.append((rel, p))
    return out


def main() -> None:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        die("未提供 GH_TOKEN 环境变量")

    args = sys.argv[1:]
    repo_name = "infinity-wallpaper-studio"
    private = False
    for i, a in enumerate(args):
        if a == "--repo" and i + 1 < len(args):
            repo_name = args[i + 1]
        if a == "--private":
            private = True

    gh = GH(token)

    # 1. 身份
    me = gh.request("GET", "/user")
    owner = me["login"]
    email = f"{me['id']}+{owner}@users.noreply.github.com"
    print(f"[1/5] 账号: {owner} (id {me['id']})")

    # 2. 仓库
    try:
        repo = gh.request("GET", f"/repos/{owner}/{repo_name}")
        print(f"[2/5] 仓库已存在: {repo['html_url']}")
    except SystemExit:
        repo = None
    if repo is None:
        repo = gh.request("POST", "/user/repos", {
            "name": repo_name,
            "description": "从 Infinity New Tab Pro 解码壁纸图库，并提供批量下载工具（Windows 安装包 / 绿色版 / CLI）",
            "homepage": "",
            "private": private,
            "has_issues": True,
            "has_wiki": False,
            "auto_init": False,
        })
        print(f"[2/5] 仓库已创建: {repo['html_url']}")
    full = repo["full_name"]
    default_branch = repo.get("default_branch") or "main"

    topics = ["wallpaper", "wallpaper-downloader", "infinity-new-tab", "python",
              "reverse-engineering", "pyinstaller", "nsis", "windows"]
    try:
        gh.request("PUT", f"/repos/{full}/topics", {"names": topics})
        print("      topics 已设置")
    except SystemExit:
        pass

    # 3. Git Data API: blobs -> tree -> commit -> ref
    files = collect_files()
    print(f"[3/5] 准备提交 {len(files)} 个文件…")
    tree_items = []
    for rel, path in files:
        content = base64.b64encode(path.read_bytes()).decode()
        blob = gh.request("POST", f"/repos/{full}/git/blobs", {
            "content": content, "encoding": "base64",
        })
        tree_items.append({"path": rel.replace(os.sep, "/"), "mode": "100644",
                           "type": "blob", "sha": blob["sha"]})
        print(f"      blob {rel:<26} {path.stat().st_size:>9,} B")

    # 已有文件时基于现有 tree 覆盖，避免丢失
    base_tree = None
    existing_sha = None
    try:
        ref = gh.request("GET", f"/repos/{full}/git/ref/heads/{default_branch}")
        existing_sha = ref["object"]["sha"]
    except SystemExit:
        pass
    if existing_sha:
        commit = gh.request("GET", f"/repos/{full}/git/commits/{existing_sha}")
        base_tree = commit["tree"]["sha"]
        print(f"      基于已有提交 {existing_sha[:8]} 更新")

    tree_payload = {"tree": tree_items}
    if base_tree:
        tree_payload["base_tree"] = base_tree
    tree = gh.request("POST", f"/repos/{full}/git/trees", tree_payload)

    commit_payload = {
        "message": COMMIT_MSG,
        "tree": tree["sha"],
        "author": {"name": owner, "email": email},
        "committer": {"name": owner, "email": email},
    }
    if existing_sha:
        commit_payload["parents"] = [existing_sha]
    new_commit = gh.request("POST", f"/repos/{full}/git/commits", commit_payload)

    if existing_sha:
        gh.request("PATCH", f"/repos/{full}/git/refs/heads/{default_branch}",
                   {"sha": new_commit["sha"], "force": False})
    else:
        gh.request("POST", f"/repos/{full}/git/refs",
                   {"ref": f"refs/heads/{default_branch}", "sha": new_commit["sha"]})
    print(f"[4/5] 提交完成: {new_commit['sha'][:12]} -> {default_branch}")

    # 4. Release（可选：dist 下有产物才发）
    assets = [
        ("InfinityWallpaperStudio-Setup-1.0.0.exe", ROOT / "dist" / "InfinityWallpaperStudio-Setup-1.0.0.exe"),
        ("Infinity Wallpaper Studio.exe", ROOT / "dist" / "Infinity Wallpaper Studio.exe"),
    ]
    assets = [(n, p) for n, p in assets if p.is_file()]
    tag = "v1.0.0"
    if assets:
        print(f"[5/5] 创建 Release {tag} 并上传 {len(assets)} 个附件…")
        try:
            rel = gh.request("GET", f"/repos/{full}/releases/tags/{tag}")
            print("      Release 已存在，复用")
        except SystemExit:
            rel = gh.request("POST", f"/repos/{full}/releases", {
                "tag_name": tag,
                "target_commitish": default_branch,
                "name": "Infinity Wallpaper Studio 1.0.0",
                "body": (
                    "## 下载\n\n"
                    "- **InfinityWallpaperStudio-Setup-1.0.0.exe** — 安装包（推荐，需本机无 Python）\n"
                    "- **Infinity Wallpaper Studio.exe** — 免安装绿色版\n\n"
                    "## 说明\n\n"
                    "- 图库 51,632 张，无需登录\n"
                    "- 安装后壁纸默认存到 `图片\\Infinity Wallpapers`\n"
                    "- 未签名，SmartScreen 提示「未知发布者」时点「更多信息 → 仍要运行」\n\n"
                    "完整说明见 README。"
                ),
                "draft": False,
                "prerelease": False,
            })
            print(f"      Release: {rel['html_url']}")

        upload_base = rel["upload_url"].split("{")[0]
        for name, path in assets:
            ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
            url = f"{upload_base}?{urllib.parse.urlencode({'name': name})}"
            gh.request("POST", url, raw=path.read_bytes(), ctype=ctype, timeout=300)
            print(f"      已上传 {name} ({path.stat().st_size/1048576:.1f} MB)")
    else:
        print("[5/5] dist/ 下无产物，跳过 Release")

    print("\n完成:")
    print(f"  仓库: {repo['html_url']}")
    print(f"  提交: {new_commit['html_url'] if 'html_url' in new_commit else repo['html_url'] + '/commit/' + new_commit['sha']}")


if __name__ == "__main__":
    main()
