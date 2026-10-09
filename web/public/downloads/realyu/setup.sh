#!/bin/sh
# macOS uses the inbox-only small package. The existing Linux path follows below.
if [ "$(uname -s)" = Darwin ]; then
# Realyu macOS setup 1.4.30: only macOS inbox components and the installed app.
set -eu
umask 077
kind='codex'
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

exit $?
fi
# POSIX bootstrap: system curl/tar/checksum tools only.
set -eu
umask 077
realyu_temp=''
realyu_locked=0
realyu_client_started=0
realyu_silent_check=0
realyu_error_code=BOOTSTRAP_FAILED
realyu_platform=unix
realyu_report_id=$(od -An -N16 -tx1 /dev/urandom 2>/dev/null | tr -d '[:space:]')
export REALYU_DIAGNOSTIC_ID="$realyu_report_id"
report_bootstrap_failure() {
  [ "$realyu_silent_check" = 0 ] || return 0
  printf '%s\n' "失败步骤：准备与下载配置工具 [$realyu_error_code]" "下一步：按上方提示检查网络或目录权限后，重新执行原命令。" "诊断编号：$realyu_report_id" >&2
  realyu_report="{\"id\":\"$realyu_report_id\",\"client_version\":\"1.4.20\",\"platform\":\"$realyu_platform\",\"architecture\":\"\",\"stage\":\"bootstrap\",\"code\":\"$realyu_error_code\"}"
  printf '%s\n' "$realyu_report" >&2
  if [ "${#realyu_report_id}" -ne 32 ]; then printf '%s\n' '未能生成诊断编号，请保留上方错误信息。' >&2; return 0; fi
  if [ "${REALYU_DIAGNOSTICS_UPLOAD:-1}" = 0 ]; then return 0; fi
  realyu_upload_status=$(curl --silent --proto '=https' --connect-timeout 3 --max-time 5 \
    --user-agent 'Realyu-Setup/1.0' --header 'Content-Type: application/json' --data-raw "$realyu_report" \
    -o /dev/null -w '%{http_code}' 'https://api.realyu.fun/api/client/diagnostics' 2>/dev/null) || realyu_upload_status=failed
}
finish() {
  realyu_exit=$1
  trap - EXIT
  if [ "$realyu_silent_check" = 1 ]; then exit "$realyu_exit"; fi
  if [ "$realyu_exit" -ne 0 ] && [ "$realyu_exit" -ne 130 ]; then
    if [ "$realyu_client_started" = 0 ] || [ ! -f "${realyu_home:-}/realyu-diagnostics/$realyu_report_id.json" ]; then report_bootstrap_failure; fi
  fi
  if [ -n "$realyu_temp" ]; then rm -rf -- "$realyu_temp"; fi
  if [ "$realyu_locked" = 1 ]; then rmdir "$realyu_base/.setup-lock"; fi
  exit "$realyu_exit"
}
trap 'finish $?' EXIT
trap 'exit 130' HUP INT TERM
fail() { realyu_error_code=${2:-BOOTSTRAP_FAILED}; printf '%s\n' "设置未完成：$1" >&2; exit 1; }
printf '%s\n' '正在准备配置工具…'
case "$(uname -s):$(uname -m)" in
  Darwin:arm64|Darwin:aarch64) realyu_target=aarch64-apple-darwin ;;
  Darwin:x86_64) realyu_target=x86_64-apple-darwin ;;
  Linux:aarch64|Linux:arm64) realyu_target=aarch64-unknown-linux-gnu ;;
  Linux:x86_64) realyu_target=x86_64-unknown-linux-gnu ;;
  *) fail '暂不支持此系统，请联系管理员。' ;;
esac
case "$realyu_target" in *apple*) realyu_platform=darwin ;; *linux*) realyu_platform=linux ;; esac
# BEGIN PINNED ASSETS
realyu_version='3.13.15-20260924'
case "$realyu_target" in
  aarch64-apple-darwin) realyu_archive='cpython-3.13.15+20260924-aarch64-apple-darwin-install_only_stripped.tar.gz'; realyu_digest='064afb7c2fc0bbf511d886288adf98696af5105e36c138cdf2c199c0146fcf68' ;;
  x86_64-apple-darwin) realyu_archive='cpython-3.13.15+20260924-x86_64-apple-darwin-install_only_stripped.tar.gz'; realyu_digest='327814efd865a0b6a99c149b12a261e9d0ad409183515c745d41bda2d07282e9' ;;
  aarch64-unknown-linux-gnu) realyu_archive='cpython-3.13.15+20260924-aarch64-unknown-linux-gnu-install_only_stripped.tar.gz'; realyu_digest='5ad58156cbec94e5643c13caa792e92df72a23f33d3a6425d4cbff5c4b7a040c' ;;
  x86_64-unknown-linux-gnu) realyu_archive='cpython-3.13.15+20260924-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz'; realyu_digest='d0b640eed27fbdd6f5f2bd33444aee53df2c8863f8b2a96f4094717411e3de9c' ;;
esac
realyu_cert='cacert-2026-09-25.pem'
realyu_cert_digest='a41b5d356aea97a529fe27e0f7316d2f9d946d75927476cf9cf1b90637d00505'
realyu_setup_digest='8cd90436ea472835166388c66b45c6dcc5a047c2b8109d7bd7bdde3dc6ac8eae'
realyu_images_digest='34b361bf70ee7283cbf854cfa4ab0545cd4aa5b6a24bff5afe477285e18ce957'
realyu_models_digest='f3b7151480ae83f595bbceedca125d69db49cac3121b9a350957935cdcb7a6ad'
# END PINNED ASSETS
# BEGIN IMAGE SDK
case "$realyu_target" in
  aarch64-apple-darwin) realyu_sdk_archive='image-sdk-1.4.17-aarch64-apple-darwin.zip'; realyu_sdk_digest='21aae5d5b8ddb0f53ff81eafe71c65a062972885739b78bfbeca613941548cd5' ;;
  aarch64-unknown-linux-gnu) realyu_sdk_archive='image-sdk-1.4.17-aarch64-unknown-linux-gnu.zip'; realyu_sdk_digest='abba906e397cd86c0da63f46aefa2563ad95b5892cf1d77c05501e1ba00b5ae9' ;;
  x86_64-apple-darwin) realyu_sdk_archive='image-sdk-1.4.17-x86_64-apple-darwin.zip'; realyu_sdk_digest='1e3d34e2b82fa6b067f5bae49987d84a85d0ed9a70e90a9731a3453f60c9e5b8' ;;
  x86_64-unknown-linux-gnu) realyu_sdk_archive='image-sdk-1.4.17-x86_64-unknown-linux-gnu.zip'; realyu_sdk_digest='dd866be4473bc352b423ffc638154a675b78648e037365db4358b4ebf3e672f7' ;;
esac
# END IMAGE SDK

for realyu_tool in curl tar mktemp; do
  command -v "$realyu_tool" >/dev/null 2>&1 || fail "缺少系统工具 $realyu_tool，请联系管理员。"
done
if command -v shasum >/dev/null 2>&1; then
  realyu_hash_tool=shasum
elif command -v sha256sum >/dev/null 2>&1; then
  realyu_hash_tool=sha256sum
else
  fail '缺少文件校验工具，请联系管理员。'
fi
verify() {
  if [ "$realyu_hash_tool" = shasum ]; then
    realyu_actual=$(shasum -a 256 "$1") || fail '文件校验失败。'
  else
    realyu_actual=$(sha256sum "$1") || fail '文件校验失败。'
  fi
  [ "${realyu_actual%% *}" = "$2" ] || fail '下载文件不完整，请重新运行命令。原配置未变。'
}
download() {
  realyu_cached="$realyu_cache/$3"
  realyu_partial="$realyu_cached.part"
  [ ! -L "$realyu_cached" ] && [ ! -L "$realyu_partial" ] || fail '下载缓存是符号链接，请联系管理员。'
  if [ -f "$realyu_cached" ]; then
    if (realyu_silent_check=1; verify "$realyu_cached" "$3" >/dev/null 2>&1); then
      cp "$realyu_cached" "$2"
      return
    fi
    printf '%s\n' '缓存校验未通过，正在重新下载…'
    rm -f -- "$realyu_cached"
  fi
  if [ -s "$realyu_partial" ] && (realyu_silent_check=1; verify "$realyu_partial" "$3" >/dev/null 2>&1); then
    mv "$realyu_partial" "$realyu_cached"
    cp "$realyu_cached" "$2"
    return
  fi
  for realyu_attempt in 1 2 3; do
    if [ -s "$realyu_partial" ]; then printf '%s\n' '正在续传已下载的内容…'; fi
    realyu_download_code=0
    # Each invocation re-reads the current offset. curl --retry alone can restart
    # at the original offset; keep partial bytes even when this script exits.
    curl --fail --show-error --progress-bar --continue-at - --proto '=https' \
      --connect-timeout 20 --max-time 1800 --speed-time 45 --speed-limit 1024 \
      --header 'Accept-Encoding: identity' --user-agent 'Realyu-Setup/1.0' \
      "https://api.realyu.fun/downloads/realyu/$1?v=$3&download=2" -o "$realyu_partial" || realyu_download_code=$?
    if [ "$realyu_download_code" = 0 ]; then
      # A corrupt complete file must not become a reusable or executable cache.
      if (realyu_silent_check=1; verify "$realyu_partial" "$3"); then
        mv "$realyu_partial" "$realyu_cached"
        cp "$realyu_cached" "$2"
        return
      fi
      rm -f -- "$realyu_partial"
      fail '文件校验未通过，请重新运行命令。原配置未变。' DOWNLOAD_CHECKSUM_FAILED
    fi
    [ "$realyu_download_code" != 33 ] || fail '下载服务暂不支持续传，进度已保留，请稍后重试。'
    [ "$realyu_download_code" != 23 ] || fail '无法写入下载文件，请检查磁盘空间和目录权限。'
    if [ "$realyu_attempt" != 3 ]; then
      printf '%s\n' '下载中断，保留进度后自动重试…'
      sleep 2
    fi
  done
  fail '下载暂未完成，进度已保留。重新运行同一命令即可继续，原配置未变。' DOWNLOAD_INTERRUPTED
}

# Versioned runtime remains available to image tools after setup and to older configs.
realyu_home=${CODEX_HOME:-"$HOME/.codex"}
case "$realyu_home" in /*) ;; *) realyu_home="$PWD/$realyu_home" ;; esac
realyu_base="$realyu_home/realyu-runtime"
[ ! -L "$realyu_base" ] || fail '运行组件目录是符号链接，请联系管理员。'
mkdir -p "$realyu_base" || fail '无法创建配置目录，请检查目录权限。'
realyu_base=$(cd "$realyu_base" && pwd -P)
realyu_cache="$realyu_base/downloads"
[ ! -L "$realyu_cache" ] || fail '下载缓存目录是符号链接，请联系管理员。'
mkdir -p "$realyu_cache"
realyu_runtime="$realyu_base/$realyu_version-$realyu_target"
[ ! -L "$realyu_runtime" ] || fail '运行组件目录是符号链接，请联系管理员。'
realyu_temp=$(mktemp -d "$realyu_base/.setup.XXXXXX") || fail '无法创建临时目录。'
mkdir "$realyu_base/.setup-lock" 2>/dev/null || fail '另一个配置任务正在运行，请稍后重试。'
realyu_locked=1
if [ ! -d "$realyu_runtime" ]; then
  printf '%s\n' '首次使用，正在下载运行组件（约 25–35 MB）…'
  download "runtime/$realyu_archive" "$realyu_temp/runtime.tar.gz" "$realyu_digest"
  mkdir "$realyu_temp/runtime"
  tar -xzf "$realyu_temp/runtime.tar.gz" -C "$realyu_temp/runtime" || fail '运行组件解压失败，请重新运行命令。'
  download "runtime/$realyu_cert" "$realyu_temp/runtime/cacert.pem" "$realyu_cert_digest"
  mv "$realyu_temp/runtime" "$realyu_runtime" || fail '无法保存运行组件，请检查目录权限。'
else
  printf '%s\n' '运行组件已就绪。'
fi
realyu_python="$realyu_runtime/python/bin/python3.13"
[ -x "$realyu_python" ] || fail '运行组件不可用，请联系管理员。'
verify "$realyu_runtime/cacert.pem" "$realyu_cert_digest"
export SSL_CERT_FILE="$realyu_runtime/cacert.pem"
export REALYU_CA_BUNDLE="$SSL_CERT_FILE"
# Ignore unrelated Python settings; do not install packages or change global PATH.
"$realyu_python" -I -B -c 'import ssl, tomllib, urllib.request' 2>/dev/null || fail '运行组件无法启动，请确认系统版本受支持。'
download setup.py "$realyu_temp/setup.py" "$realyu_setup_digest"
download realyu_images.py "$realyu_temp/realyu_images.py" "$realyu_images_digest"
download models.json "$realyu_temp/models.json" "$realyu_models_digest"
printf '%s\n' '正在准备图片组件…'
download "runtime/$realyu_sdk_archive" "$realyu_temp/image-sdk.zip" "$realyu_sdk_digest"
realyu_image_runtime="$realyu_base/image-sdk-$realyu_sdk_digest"
[ ! -L "$realyu_image_runtime" ] || fail '图片组件目录不可用，请联系管理员。'
if [ ! -d "$realyu_image_runtime" ]; then
  "$realyu_python" -I -B - "$realyu_temp/image-sdk.zip" "$realyu_temp/image-sdk" <<'REALYU_EXTRACT'
import pathlib, stat, sys, zipfile
source, target = map(pathlib.Path, sys.argv[1:])
target.mkdir()
with zipfile.ZipFile(source) as archive:
    for info in archive.infolist():
        path = pathlib.PurePosixPath(info.filename)
        if path.is_absolute() or '..' in path.parts or '\\' in info.filename or stat.S_ISLNK(info.external_attr >> 16):
            raise ValueError('Invalid SDK archive entry')
    archive.extractall(target)
REALYU_EXTRACT
  mv "$realyu_temp/image-sdk" "$realyu_image_runtime" || fail '无法保存图片组件，请检查目录权限。'
fi
export REALYU_IMAGE_RUNTIME="$realyu_image_runtime"
printf '%s\n' '配置工具已就绪。'
realyu_client_started=1
realyu_error_code=CLIENT_FAILED
if [ "$#" -gt 0 ]; then
  printf '%s\n' "$1" | "$realyu_python" -I -B "$realyu_temp/setup.py"
else
  "$realyu_python" -I -B "$realyu_temp/setup.py"
fi
