#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 把文档里的仓库占位符 <owner>/<repo> 一次性替换成实际地址。
#
#   用法:  bash tools/set-repo.sh <用户名>/<仓库名>
#   示例:  bash tools/set-repo.sh sy0137/wb2api-dashboard
#
# 影响文件: README.md、使用教程.md（存在占位符才改）
# ---------------------------------------------------------------------------
set -euo pipefail

SLUG="${1:-}"
if [ -z "$SLUG" ]; then
  echo "用法: bash tools/set-repo.sh <用户名>/<仓库名>"
  echo "  例: bash tools/set-repo.sh sy0137/wb2api-dashboard"
  exit 1
fi

case "$SLUG" in
  */*) ;;
  *) echo "[错误] 需要 owner/repo 形式，例如 sy0137/wb2api-dashboard"; exit 1 ;;
esac

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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
  echo "完成，替换为: $SLUG"
  echo "自检:"
  grep -nF "$SLUG" "$ROOT/README.md" 2>/dev/null | sed 's/^/  /' || true
  echo
  echo "接下来别忘了: git add -A && git commit -m 'docs: 填上仓库地址'"
fi
