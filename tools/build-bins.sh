#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 从上游 workbuddy2api 源码交叉编译出本面板需要的 4 个 Windows 二进制。
#
#   用法:  bash tools/build-bins.sh [上游仓库路径]
#
#   上游路径的确定顺序:
#     1. 命令行第 1 个参数
#     2. 环境变量 WB2API_DIR
#     3. <本项目>/../workbuddy2api
#
#   产物:  <本项目>/bin/{wb2api,signin_bin,login,credit}.exe
#          <本项目>/bin/config.json（若不存在则生成，指向上游仓库的 auths / data）
#
#   依赖:  仅需 Docker（会用 golang:1.23-alpine 镜像，无需本机装 Go）
# ---------------------------------------------------------------------------
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="$(dirname "$HERE")"
SRC="${1:-${WB2API_DIR:-$(dirname "$PROJECT")/workbuddy2api}}"
OUT="$PROJECT/bin"

if [ ! -d "$SRC/cmd" ]; then
  echo "[错误] 未找到上游源码: $SRC"
  echo "       该目录需包含 cmd/（server / signin / login / credit）。"
  echo "       先克隆: git clone https://github.com/Sliverkiss/workbuddy2api"
  exit 1
fi

mkdir -p "$OUT"

# Git Bash / MSYS 下把 /d/xxx 转成 D:/xxx，否则 Docker 认不出挂载源
if command -v cygpath >/dev/null 2>&1; then
  SRC_M="$(cygpath -m "$SRC")"
  OUT_M="$(cygpath -m "$OUT")"
else
  SRC_M="$SRC"
  OUT_M="$OUT"
fi

IMAGE="${GO_IMAGE:-golang:1.23-alpine}"
echo "上游源码 : $SRC_M"
echo "输出目录 : $OUT_M"
echo "构建镜像 : $IMAGE"
echo

docker run --rm \
  -v "$SRC_M":/src \
  -v "$OUT_M":/out \
  -w /src \
  -e CGO_ENABLED=0 -e GOOS=windows -e GOARCH=amd64 \
  "$IMAGE" sh -c '
    set -e
    build() {
      printf "  -> %s.exe\n" "$2"
      go build -trimpath -ldflags="-s -w" -o "/out/$2.exe" "./cmd/$1"
    }
    build server wb2api
    build signin signin_bin
    build login  login
    build credit credit
  '

# 双击 bin/wb2api.exe 时，它的工作目录就是 bin/，那里必须有一份 config.json
CFG="$OUT/config.json"
if [ ! -f "$CFG" ]; then
  cat > "$CFG" <<JSON
{
  "listen": ":7863",
  "api_key": "test_key",
  "auth_dir": "$SRC_M/auths",
  "state_file": "$SRC_M/data/state.json"
}
JSON
  echo
  echo "已生成 $CFG（与上游仓库共用同一份账号池与状态）"
fi

echo
echo "完成。产物:"
ls -1 "$OUT"
