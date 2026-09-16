#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把文档里的仓库占位符 <owner>/<repo> 替换成实际地址。

用法:
    python tools/set_repo.py <用户名>/<仓库名>
    python tools/set_repo.py shuyue01370/wb2api-dashboard

影响文件: README.md、使用教程.md（只改含占位符的）
对比 shell 版的好处: 不依赖 bash / sed，Windows 上可直接用 cmd 或 PowerShell 跑，
且显式按 UTF-8 读写、按 LF 写回，不会因系统代码页把中文文件名或内容写坏。
"""

from __future__ import annotations

import os
import sys

PLACEHOLDER = "<owner>/<repo>"
TARGETS = ("README.md", "使用教程.md")


def main(argv: list) -> int:
    if len(argv) < 2 or not argv[1].strip():
        print("用法: python tools/set_repo.py <用户名>/<仓库名>")
        print("  例: python tools/set_repo.py shuyue01370/wb2api-dashboard")
        return 1

    slug = argv[1].strip().strip("/")
    parts = slug.split("/")
    if len(parts) != 2 or not all(parts):
        print("[错误] 需要 owner/repo 形式，例如 shuyue01370/wb2api-dashboard")
        return 1

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    changed = 0

    for name in TARGETS:
        path = os.path.join(root, name)
        if not os.path.isfile(path):
            continue

        with open(path, encoding="utf-8") as fh:
            text = fh.read()

        hits = text.count(PLACEHOLDER)
        if not hits:
            print("  %s: %s，跳过" % (name, "已是 %s" % slug if slug in text else "无占位符"))
            continue

        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text.replace(PLACEHOLDER, slug))
        print("  %s: 替换 %d 处" % (name, hits))
        changed += hits

    print()
    if not changed:
        print("没有需要替换的内容。")
        return 0

    print("完成，仓库地址: %s" % slug)
    print("下载链接: https://github.com/%s/releases/latest/download/WorkBuddy2API.exe" % slug)
    print()
    print('接下来: git add -A && git commit -m "docs: 填上仓库地址"')
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
