# 2026-10-11 SG GOST 被 Defender 隔离：公网 1033 已恢复

北京时间 15:04:00 的既有采样首次确认恢复。原生应用仍为 UX5，本次没有发布应用版本、恢复数据库或切换节点。

## 原因与时间线

- 14:26:50 公网直连、经 SG 代理公网、两个 Tunnel 的最后正常样本。
- 14:26:57 Defender 1117 记录成功隔离 `C:\ProgramData\RealYuNetwork\sg-hy2-20261010\bin\gost.exe`；检测名 `Trojan:Win32/Wacatac.B!ml`。四个 19464–19467 转发监听消失，两 Tunnel 无连接；18300 API、18301 bridge 保持健康。
- 14:27:00 两公网路径先返回 502，随后为 530 / Cloudflare 1033。首个直连 CF-Ray `a48bcbeff8aa054f-LHR`，probe `33ca5aef2e4f494aa9824c239341efd6`；代理 CF-Ray `a48bcbe80b54fdb3-SIN`，probe `2787fa5d7cde4599b4730522a15dee5b`。
- 新 SG 安装路径没有精确文件排除项；旧 GOST 路径的保护不覆盖新路径。保留副本的 SHA256 与 SG 发布计划完全一致：`6f6b66cbdc4a5aa5685d0017abd3f65843a28bcc2bb4e5b78d6d12a844a51aa8`。旧官方资产验证记录亦为此摘要；这证明文件来源一致，不单独证明杀毒分类是误报。
- 15:03:44 服务恢复操作完成；15:04:00 两公网路径和两个 Tunnel 首次采样恢复。失败到恢复的采样间隔为 2220.143 秒，约 37 分钟；10 秒采样不能确定精确客户中断起止。

## 已执行恢复

使用 [本事故恢复脚本](Repair-SgGost-20261011.ps1)，通过正常 Windows UAC 提权，仅添加上述 `gost.exe` 的精确文件排除项并恢复同摘要文件。`MpCmdRun -CheckExclusion` 确认生效；Defender 防病毒与实时保护均仍开启。

按真实 SCM 依赖先停止两个已经断线的 Tunnel，再恢复 `RealYuSgHy2EdgeProxy`，随后启动两个 Tunnel。确认四个 loopback 端口由同一 Session 0 GOST worker 持有，父进程为该 SCM wrapper。API、bridge、Mihomo 服务 PID 均保持。

首次恢复核验仍遭 Defender 拦截，第二次因未按 SCM 依赖顺序停止服务而失败；两份失败回执保留，修正后的第三次恢复成功。没有覆盖首次错误，也没有使用广泛的 `-Force` 停服。

## 已验证范围

15:04:00–15:08:20，原有 10 秒采样六条路径各 **27/27** 成功；最大采样间隔 10.014 秒，读取时新鲜度 2.148 秒。两个 Tunnel 各 4 条真实连接。

路径观察器确认 8 条同 ID 的连接都经 `REALYU -> RealYu-SG-HY2`；期间每条连接均有双向增量，合计发送增加 4,210,497 字节、接收增加 8,883,407 字节。实际链路为 Tunnel → GOST 19464–19467 → Mihomo 17897 → SG-HY2 → Cloudflare。无连接期间观察器返回 `unknown_controller_schema`，恢复后自行恢复有效采样；不能把该错误当作零连接或健康证据。

额外验证 `/api/status` 的精确 UX5 版本、完整首页 HTML、一次真实 `gpt-5.6-sol` Responses SSE：HTTP 200、18 个事件、`response.completed`、文本增量与最终结果一致且随机标记匹配。共 1 次模型请求、0 重试。这不是全功能/账务重新验收，也不是长期稳定保证。

脱敏摘要：[恢复采样和公网调用](../../lab/sub2api_e2e/network-recovery-20261011.json)。完整首错、Defender 事件、三次修复回执与私有测试输出留在本机 `C:\srv\realyu-observability\incidents\outage-20261011-1454`，不上传凭据或响应原文。

## 后续部署必须覆盖的检查

网络安装目录变化时，重新核验发布来源和文件摘要，再审核实际目标文件的精确 Defender 排除项；旧目录上的排除项不会继承。保持实时保护，不使用目录、扩展名或全局进程排除。

`RealYuSgHy2EdgeProxy` 的 WinSW wrapper 在子进程被隔离后曾仍显示 Running，日志含 `SignalStopped` 的 SCM 权限错误。本轮用精确文件恢复与手动服务恢复解决中断，**没有宣称修复此自动恢复缺口**。后续需单独验证 worker 异常退出时的服务状态/告警和受限恢复，避免扩大 LocalService 的全局 SCM 权限。

本脚本绑定本机、本次目录及审核过的摘要，不是通用安装器；本次已经完成，不要在健康生产环境重放。新机按 [SG 服务交接](SG-HY2-SERVICE-HANDOFF.md) 准备自身计划并核验有效路径、文件保护、监听归属、公网完整响应及真实调用。
