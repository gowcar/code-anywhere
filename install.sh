#!/usr/bin/env bash
# code-anywhere 一行安装 / 升级：
#   curl -fsSL https://raw.githubusercontent.com/gowcar/code-anywhere/main/install.sh | bash
# 每次都会拉取最新代码（第一次克隆到 ~/workspace/code-anywhere，之后原地更新），然后运行 ./setup。
# 可选环境变量：CODE_ANYWHERE_DIR（安装目录）、CODE_ANYWHERE_REPO（仓库地址，例如用部署钥匙的 ssh 别名）
set -euo pipefail

DIR="${CODE_ANYWHERE_DIR:-$HOME/workspace/code-anywhere}"
REPOS=("${CODE_ANYWHERE_REPO:-}" "https://github.com/gowcar/code-anywhere.git" "git@github.com:gowcar/code-anywhere.git")

command -v git >/dev/null || { echo "需要 git：macOS 运行 xcode-select --install，Linux 用包管理器安装 git 后重试。" >&2; exit 1; }

if [ -d "$DIR/.git" ]; then
  echo "更新 $DIR …"
  git -C "$DIR" pull --ff-only
else
  mkdir -p "$(dirname "$DIR")"
  cloned=
  for url in "${REPOS[@]}"; do
    [ -n "$url" ] || continue
    echo "下载 code-anywhere（$url）…"
    if GIT_TERMINAL_PROMPT=0 git clone -q "$url" "$DIR" 2>/dev/null; then cloned=1; break; fi
  done
  [ -n "$cloned" ] || { echo "拉不到代码：这台机器没有仓库的访问权限（私有仓库需要先配好 GitHub 的 SSH 钥匙）。" >&2; exit 1; }
fi

# 通过管道运行时标准输入是 curl 的输出；安装过程中要输入 bot token 等，改从终端读
if [ ! -t 0 ] && (exec < /dev/tty) 2>/dev/null; then exec "$DIR/setup" "$@" < /dev/tty; fi
exec "$DIR/setup" "$@"
