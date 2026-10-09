#!/bin/sh
# Read-only native Mac probe. No API Key; output only flags/status, never proxy URLs.
set -u
[ "$(uname -s)" = Darwin ] || { printf '%s\n' 'macOS only'; exit 1; }
printf 'macOS: '; /usr/bin/sw_vers -productVersion
printf 'architecture: '; uname -m
/usr/bin/curl --version | /usr/bin/head -n 1
/usr/bin/sqlite3 -version
printf '%s\n' 'Proxy environment presence (values redacted):'
for name in https_proxy HTTPS_PROXY all_proxy ALL_PROXY http_proxy no_proxy NO_PROXY; do
  if /usr/bin/printenv "$name" >/dev/null 2>&1; then printf '%s=set\n' "$name"; else printf '%s=unset\n' "$name"; fi
done
printf '%s\n' 'System proxy enable flags (URLs/hosts redacted):'
/usr/sbin/scutil --proxy | /usr/bin/awk '/^[[:space:]]*(HTTPEnable|HTTPSEnable|SOCKSEnable|ProxyAutoConfigEnable|ProxyAutoDiscoveryEnable)[[:space:]]*:/ { print $1 "=" $3 }'
printf '%s\n' 'JXA binding probe:'
/usr/bin/osascript -l JavaScript -e "ObjC.import('Foundation'); ObjC.bindFunction('rename',['int',['char *','char *']]); ObjC.bindFunction('chmod',['int',['char *','unsigned short']]); ObjC.bindFunction('kill',['int',['int','int']]); 'JXA_BIND_OK';"
printf '%s\n' 'Anonymous status request, current curl environment route:'
code=0
/usr/bin/curl --disable --silent --proto '=https' --connect-timeout 5 --max-time 15 --output /dev/null --write-out 'HTTP=%{http_code} bytes=%{size_download} seconds=%{time_total}\n' https://api.realyu.fun/api/status || code=$?
printf 'curl_exit=%s\n' "$code"
printf '%s\n' 'Anonymous status request, explicitly direct diagnostic route:'
code=0
/usr/bin/curl --disable --silent --proxy '' --proto '=https' --connect-timeout 5 --max-time 15 --output /dev/null --write-out 'HTTP=%{http_code} bytes=%{size_download} seconds=%{time_total}\n' https://api.realyu.fun/api/status || code=$?
printf 'curl_exit=%s\n' "$code"
printf '%s\n' 'This probe does not prove GUI static proxy or PAC routing; curl does not execute PAC.'
