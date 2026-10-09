# macOS agent 冒烟验收：Realyu 1.4.21

请执行本文并回传文末报告。测试过程允许正常配置 Realyu 与小规模图片测试；遇到实际问题先留证，不自行改线上服务。

## 系统与入口

本实现的系统基线为 macOS 12+，同时满足 Codex/WorkBuddy 自身要求。WorkBuddy 至少 5.6.2。记录 `sw_vers`、`uname -m`，验证系统 SQLite 的 JSON 模式：

```sh
/usr/bin/sqlite3 -json :memory: 'select 1 as ok;'
/usr/bin/osascript -l JavaScript -e 'ObjC.import("Foundation"); ObjC.unwrap($.NSProcessInfo.processInfo.operatingSystemVersionString)'
```

先下载成真实文件；不要执行不存在的本地文件。下面命令不包含 Key，执行后终端隐藏输入。

Codex：

```sh
f="$(mktemp -t realyu-codex)" && /usr/bin/curl -fS --connect-timeout 10 --max-time 60 'https://api.realyu.fun/downloads/realyu/setup-macos.sh?v=1.4.21' -o "$f" && /bin/sh "$f"
```

WorkBuddy：

```sh
f="$(mktemp -t realyu-workbuddy)" && /usr/bin/curl -fS --connect-timeout 10 --max-time 60 'https://api.realyu.fun/downloads/realyu/setup-workbuddy.sh?v=1.4.21' -o "$f" && /bin/sh "$f"
```

清单：[macos-setup.json](https://api.realyu.fun/downloads/realyu/macos-setup.json)。ZIP：[realyu-setup-macos.zip](https://api.realyu.fun/downloads/realyu/realyu-setup-macos.zip)。默认 Codex 目录 `~/.codex`，WorkBuddy `~/.workbuddy`；隔离测试可分别设置 CODEX_HOME / WORKBUDDY_CONFIG_DIR，实际桌面也须读取同目录。

额外必测：在隐藏输入 Key 时按 Ctrl+C，确认终端恢复回显；仅用中文/空格文件名的原图做一次编辑；检查 JXA 子进程退出和 MCP 连续调用不挂死。清单通过前，macOS 为待真机验收状态。

网络读数（不带 Key）：

```sh
/usr/bin/curl -fS -o /dev/null --connect-timeout 10 --max-time 60 -w '\nhttp=%{http_code} ip=%{remote_ip} dns=%{time_namelookup}s tls=%{time_appconnect}s first=%{time_starttransfer}s total=%{time_total}s bytes=%{size_download} speed=%{speed_download}B/s\n' 'https://api.realyu.fun/downloads/realyu/realyu-setup-macos.zip?v=1.4.21'
```

## 执行范围与判定

目标：Realyu 一键配置 1.4.21，官网服务发布版本 realyu-provider-v3.9.2.6-20261007。分别验证 Codex 和 WorkBuddy；没有安装其中某个应用时标 BLOCKED，不能凭另一应用通过判定全部通过。不要为这次测试安装 Python、Node 或独立运行时。开发 agent 自己拥有这些工具不等于安装器依赖它们，应观察实际子进程和下载。

安装应用本体、真实生成的图片和模型 API 流量不计入配置下载的 200,000 bytes 门槛；配置脚本、模型目录、图片桥接组件全部计入。历史 31.9 MB 文件仍留作兼容与回退，新安装入口及首次图片调用不得获取它。不能仅因缓存已有大包而判 PASS。

使用测试账户或用户指定的有效 Realyu Key。通过隐藏输入输入 Key，不写入报告、命令行参数、截图或完整配置转储。每个应用先生成 1 张、编辑 1 次；不要在失败后自动重复收费调用。原生工具批准由用户按应用提示确认，不关闭全局审批、不自动信任任意工具。

先记录原配置和会话是否存在，并在用户私有目录备份将被改动的文件。不要删会话、清空应用数据、恢复整份数据库、杀死用户工作进程或卸载系统软件。优先独立测试用户/测试配置目录；桌面验收必须明确桌面实际读取的目录。若正在验收的 agent 本身运行于 Codex，只能由用户重启应用，或改用另一 agent 执行；不可终止自己或其他任务。

## 必测清单（两款应用分别记录）

| ID | 操作 | 通过标准 |
| --- | --- | --- |
| A01 | 记录 OS 版本/架构、应用版本、所在路径、使用的配置目录、直连或代理网络 | 不输出密钥；能确认是待测机器和真实桌面应用 |
| A02 | 下载平台清单、脚本和 ZIP，核对字节数与 SHA256 | 与发布清单一致；入口脚本 + ZIP 小于 200,000 bytes；同次测试保留实际哈希 |
| A03 | 在无 Realyu 旧缓存的独立配置目录首次配置 | 显示总大小与 curl 速度/进度；无 Python、Node、独立 EXE/runtime 下载；明确完成或具体错误原因 |
| A04 | 同一目录重复配置一次 | 成功；原有其他服务、模型、注释/可保留设置和会话仍在；不存在重复图片工具；不覆盖冲突的第三方 image_generation |
| A05 | 重启真实桌面，选择 Realyu 文本模型，发“只回复 REALYU_SMOKE_OK” | 桌面有真实回复；记录模型和耗时，不能用 /api/status 或 CLI 回复替代 |
| A06 | 在该对话发送一个随机短标记，关闭并重开应用，继续询问该标记 | 同一对话成功续聊且保留上下文，无新建对话冒充续聊 |
| A07 | 检查图片工具 image_generation | 能发现 generate_image、edit_image、diagnose_image；需要批准时记录并由用户批准；初始化/工具列表不触发生图或运行包下载 |
| A08 | 执行 diagnose_image | 返回检查结果；不产生生图请求。失败保留诊断编号和具体错误码 |
| A09 | 中文提示“生成一张浅蓝背景上的红色杯子，1024×1024，低质量即可”，明确使用本次安装的 generate_image | 桌面真正展示图片；工具返回完整原图路径及小预览路径；原图可打开；首次调用没有大组件下载 |
| A10 | 对 A09 原图调用 edit_image：“保留构图，把红色杯子改为绿色”，传原图绝对路径 | 桌面显示新图；原文件哈希不变；新原图独立保存；不得用 512px 预览作编辑输入 |
| A11 | 图片后继续文字对话，然后重启并续聊 | 不报 HTTP 413、图片超限或 MCP 无法启动；能继续，不能只验证生图返回 200 |
| A12 | 检查原图、预览和源文件 | 预览最长边不超过 512px；原图未缩成预览；PNG/JPEG/WebP 编辑至少验证一种，其他格式标待测；文件路径含中文/空格用例至少一例 |
| A13 | 对无关配置做一个可恢复的小改动再重复配置，保留旧会话/归档样本 | 无关配置保留。Codex 根会话迁移前后模型、推理强度、消息保持，归档与子会话不被覆盖。迁移失败不得阻止其他可迁移会话；如缺测试样本标 BLOCKED |
| A14 | 在隔离目录输入格式错误 Key；再用无效但格式正确的测试 Key | 明确 KEY_FORMAT_INVALID / KEY_REJECTED 等原因和下一步；不能显示成功；原 config/auth/models/mcp 文件哈希不变 |
| A15 | 在隔离目录模拟下载失败/截断/校验不一致（如可控本地服务器） | 不执行未验证包；无自动重试/续传循环；有下载原因/校验原因。无法注入则标 NOT_RUN，不破坏系统网络 |
| A16 | 编辑工具传不存在路径或相对路径 | 明确文件或路径错误；不发收费图片请求；日志不泄露 Key、完整认证文件或上游响应正文 |
| A17 | 检查下载及进程证据，统计实际新下载组件 | 新路径没有 realyu-client.exe、CPython、Node、uv、pip/npm 安装；Win 使用系统 PowerShell/.NET，Mac 使用系统 JXA/curl/sips/sqlite3。已安装应用的内置程序允许复用 |

Codex 保持打开时，正在写入的会话可明确提示 SESSIONS_BUSY/跳过迁移；不要将这种提示改写为“所有会话迁移成功”。退出应用后可按提示重试。macOS 旧 schema1 恢复日志可能提示迁移需人工处理；连接配置成功与旧日志迁移完成分开记录。

WorkBuddy 请另外记录：全局 mcp.json 是否被真实桌面加载、首次批准后是否能连接、重启后批准状态是否保留。只用手动 --mcp-config 注入或独立 SDK 成功，不满足桌面自动发现验收。

## 网络慢时的取证

只读记录 DNS、TCP/TLS、首字节时间、平均速度、HTTP 状态、下载字节数。保留首次失败，不用删缓存或循环重试掩盖。可在同一机器比较默认网络与用户已有且明确地址的代理；不要假设 7890 一定有代理。同机结果不等于全球网络结论。

## 回传报告模板

```text
结果：PASS / FAIL / PARTIAL
时间与时区：
OS/架构：
Codex 版本与路径：
WorkBuddy 版本与路径：
发布版本/安装器版本：
网络：默认/直连/代理（不要包含认证）
测试配置目录：用户路径脱敏
脚本 bytes / SHA256：
ZIP bytes / SHA256：
总组件下载 bytes：
是否首次安装/是否残留旧缓存：
Python/Node 是否预装；本次是否调用/下载：

ID | 应用 | PASS/FAIL/BLOCKED/NOT_RUN | 实际现象 | 脱敏证据文件
A01 | Codex | ...
...

配置/CLI 协议：
真实桌面文本与续聊：
真实桌面图片生成、编辑、重启续聊：
首个失败阶段/错误码/诊断编号/请求编号：
失败复现步骤（不含 Key）：
图片调用数与是否自动重试：
原图尺寸/哈希，编辑后原图哈希，预览尺寸：
审批与应用重启是否由用户完成：
未覆盖项和原因：
```

没有真实桌面操作能力的 agent 应交 PARTIAL，并列明需要用户完成的 GUI 项；禁止把协议通过、手工注册或模拟 API 结果写成桌面端到端通过。首次失败可以回传，不必等全部跑完。
