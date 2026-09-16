#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 把文档里的仓库占位符 <owner>/<repo> 替换成实际地址（shell 入口）。
#
#   用法:  bash tools/set-repo.sh <用户名>/<仓库名>
#   示例:  bash tools/set-repo.sh shuyue01370/wb2api-dashboard
#
#   有 Python 时走 set_repo.py（跨平台、UTF-8 安全）；没有则退回内置 sed 实现。
#   Windows 的 cmd / PowerShell 里请用 tools\set-repo.cmd —— 那里 `bash` 往往
#   指向 WSL 转发器（C:\Windows\System32\bash.exe），未装发行版会直接报错。
# ---------------------------------------------------------------------------
set -euo pipefail

SLUG="${1:-}"
if [ -z "$SLUG" ]; then
  echo "用法: bash tools/set-repo.sh <用户名>/<仓库名>"
  echo "  例: bash tools/set-repo.sh shuyue01370/wb2api-dashboard"
  exit 1
fi

case "$SLUG" in
  */*) ;;
  *) echo "[错误] 需要 owner/repo 形式，例如 shuyue01370/wb2api-dashboard"; exit 1 ;;
esac

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 优先用 Python 实现。
# 注意：必须 cd 到脚本目录后用「相对文件名」调用 —— Git Bash 下 $HERE 是 MSYS 风格
# （/d/utils/...），直接把它拼进参数传给原生 Windows 版 python.exe 会被解析成
# D:\d\utils\... 而报 "can't open file"。
if command -v python3 >/dev/null 2>&1; then
  cd "$HERE"
  exec python3 set_repo.py "$SLUG"
elif command -v python >/dev/null 2>&1; then
  cd "$HERE"
  exec python set_repo.py "$SLUG"
fi

# ---- 无 Python 时的兜底：纯 shell 实现 ----
echo "[提示] 未找到 Python，使用内置 sed 实现"
ROOT="$(dirname "$HERE")"
PLACEHOLDER="<owner>/<repo>"
changed=0

for f in README.md 使用教程.md; do
  p="$ROOT/$f"
  [ -f "$p" ] || continue
  if ! grep -qF "$PLACEHOLDER" "$p"; then
    echo "  $f: 无占位符，跳过"
    continue
  fi
  n="$(grep -cF "$PLACEHOLDER" "$p")"
  # 用临时文件而非 sed -i：避免 GNU / BSD sed 的就地编辑语法差异
  sed "s|$PLACEHOLDER|$SLUG|g" "$p" > "$p.tmp"
  mv "$p.tmp" "$p"
  echo "  $f: 替换 $n 处"
  changed=1
done

echo
if [ "$changed" = 0 ]; then
  echo "没有需要替换的内容。"
else
  echo "完成，仓库地址: $SLUG"
  echo "自检:"
  grep -nF "$SLUG" "$ROOT/README.md" 2>/dev/null | sed 's/^/  /' || true
  echo
  echo "接下来别忘了: git add -A && git commit -m 'docs: 填上仓库地址'"
fi
