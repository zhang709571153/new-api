#!/bin/sh
# Realyu macOS setup 1.4.30: only macOS inbox components and the installed app.
set -eu
umask 077
kind='workbuddy'
digest='ac6dabb470675f991b8f5a0fb980c44c78b1dd2e7093506876aa73f97158394e'
bytes='120342'
version='1.4.30'
locked=0
temp=''
terminal_state=''
fail() { printf '%s\n' "失败步骤：准备配置工具 [$1]" "原因：$2" "下一步：$3" >&2; exit 1; }
finish() {
  status=$?
  trap - EXIT
  if [ -n "$terminal_state" ]; then stty "$terminal_state" </dev/tty 2>/dev/null || :; fi
  if [ -n "$temp" ] && [ -d "$temp" ]; then rm -rf -- "$temp"; fi
  if [ "$locked" = 1 ]; then rmdir "$base/.setup-lock" 2>/dev/null || :; fi
  exit "$status"
}
trap finish EXIT
trap 'exit 130' HUP INT TERM
[ "$(uname -s)" = Darwin ] || fail MAC_PLATFORM_UNSUPPORTED '此入口用于 macOS。' 'Windows 请使用 PowerShell 配置命令。'
for tool in /usr/bin/osascript /usr/bin/curl /usr/bin/unzip /usr/bin/shasum /usr/bin/sips /usr/bin/sqlite3; do
  [ -x "$tool" ] || fail MAC_SYSTEM_TOOL_MISSING "缺少系统组件：$tool" '请修复或更新 macOS。无需安装 Python 或 Node。'
done
if [ "$kind" = workbuddy ]; then
  home=${WORKBUDDY_CONFIG_DIR:-${CODEBUDDY_CONFIG_DIR:-"$HOME/.workbuddy"}}
else
  home=${CODEX_HOME:-"$HOME/.codex"}
fi
case "$home" in /*) ;; *) home="$PWD/$home" ;; esac
check=$home
while [ "$check" != / ]; do
  [ ! -L "$check" ] || fail PATH_UNSAFE '配置目录包含符号链接。' '使用当前用户拥有的普通目录。'
  check=$(dirname "$check")
done
mkdir -p "$home" || fail FILESYSTEM_ACCESS_DENIED '无法创建配置目录。' '检查用户权限和磁盘空间。'
home=$(cd "$home" && pwd -P)
base="$home/realyu-runtime"
[ ! -L "$base" ] || fail PATH_UNSAFE '组件目录包含符号链接。' '检查 realyu-runtime 目录。'
mkdir -p "$base"
mkdir "$base/.setup-lock" 2>/dev/null || fail SETUP_BUSY '另一个配置任务正在运行，或上次异常退出留下锁目录。' '等待任务完成；确认无配置任务后移除 realyu-runtime/.setup-lock 空目录。'
locked=1
temp=$(mktemp -d "$base/.setup.XXXXXXXX")
runtime="$base/scripts/$digest"
[ ! -L "$base/scripts" ] && [ ! -L "$runtime" ] || fail PATH_UNSAFE '脚本目录包含符号链接。' '使用普通目录后重新运行。'
printf '%s\n' "配置、模型目录与图片组件共 $bytes bytes。无需 Python 或 Node，无后续大包下载。"
archive="$temp/package.zip"
url="https://api.realyu.fun/downloads/realyu/realyu-setup-macos.zip?v=$digest"
download_code=0
/usr/bin/curl --disable --fail --show-error --proto '=https' --connect-timeout 60 --max-time 300 --max-filesize "$bytes" --output "$archive" "$url" || download_code=$?
if [ "$download_code" != 0 ]; then
  case "$download_code" in
    6) reason='DNS 无法解析下载域名。' ;;
    7) reason='无法连接下载服务器。' ;;
    22) reason='下载服务器返回 HTTP 错误，具体状态见上方 curl 输出。' ;;
    23) reason='无法写入下载文件，可能空间不足或没有权限。' ;;
    28) reason='下载连接或传输超时（连接最多等待 60 秒，整个下载最多 300 秒）。' ;;
    35|60) reason='TLS 连接或 HTTPS 证书校验失败。' ;;
    *) reason="下载中断，curl 错误码 ${download_code}。" ;;
  esac
  fail DOWNLOAD_FAILED "$reason" '未自动重试。检查上述原因后重新执行原命令。'
fi
actual=$(/usr/bin/shasum -a 256 "$archive")
[ "${actual%% *}" = "$digest" ] || fail PACKAGE_INVALID '下载包 SHA256 不匹配，未执行。' '重新下载；若反复出现请联系支持。'
[ "$(wc -c < "$archive" | tr -d '[:space:]')" = "$bytes" ] || fail PACKAGE_INVALID '下载包大小不匹配。' '重新运行下载。'
/usr/bin/unzip -q "$archive" -d "$temp/extracted" || fail PACKAGE_INVALID '配置包无法解压。' '重新下载。'
mkdir -p "$base/scripts"
if [ -e "$runtime" ]; then
  for file in mac.js models.json tools.json errors.json; do
    [ ! -L "$runtime/$file" ] && cmp -s "$runtime/$file" "$temp/extracted/$file" || fail HELPER_CACHE_INVALID '现有组件与已校验版本不一致。' '保留诊断信息，请联系支持检查缓存。'
  done
else
  mv "$temp/extracted" "$runtime"
fi
if [ "$#" -gt 1 ]; then fail KEY_FORMAT_INVALID '参数过多。' '从工作台复制完整的一键配置命令。'; fi
run_setup() {
  setup_code=0
  /usr/bin/osascript -l JavaScript "$runtime/mac.js" setup "$kind" "$home" "$runtime" || setup_code=$?
  if [ "$setup_code" != 0 ]; then
    case "$setup_code" in
      134|139) fail MAC_RUNTIME_CRASH "macOS JavaScript 系统组件异常退出（退出码 ${setup_code}）。" '保留上方系统错误及 macOS 版本，请联系支持；无需安装 Python 或 Node。' ;;
      *) fail MAC_SETUP_FAILED "配置程序返回错误（退出码 ${setup_code}），具体原因见上方失败阶段与诊断编号。" '按上方具体错误处理后再运行；未自动重试。' ;;
    esac
  fi
}
printf '[启动] 配置包校验通过，正在加载 macOS 系统组件（%s）…\n' "${version}"
if [ "$#" = 1 ]; then
  printf '%s\n' "$1" | run_setup
else
  printf '请输入 API Key（不回显）：' >&2
  terminal_state=$(stty -g </dev/tty)
  stty -echo </dev/tty
  IFS= read -r key </dev/tty || key=''
  stty "$terminal_state" </dev/tty
  terminal_state=''
  printf '\n' >&2
  printf '%s\n' "$key" | run_setup
  unset key
fi
