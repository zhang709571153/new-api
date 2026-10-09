# macOS 1.4.27 冒烟提示词

请记录macOS版本、Intel/Apple Silicon架构、系统curl/sqlite和Codex/WorkBuddy版本，运行官方setup.sh?v=2，确认1.4.27、大小/SHA、四阶段与完成状态。重开应用验证默认Astra、目录含6.1 Sol，新建和继续旧聊天；保留现有聊天及备份。

需要代理的机器应在安装终端使用已核实的标准HTTPS_PROXY/https_proxy或ALL_PROXY，不猜测端口，不输出代理凭据。该路由会写入私有图片MCP环境供GUI启动使用；当前未自动导入系统GUI静态代理或执行PAC，不能将仅在GUI打开代理视为已配置curl代理。无Key只读探针：probe-macos-1.4.27.sh。

图片先diagnose_image，再只提交一次生成；分别核对真实原图文件、512px预览和桌面显示，记录失败阶段/received_bytes/诊断编号，不自动重试。检查含中文或空格路径下的文件可打开。

Windows宿主上的逻辑、旧SQLite和入口隔离测试已通过，但不能替代真实macOS的JXA/Foundation/sips、原生/bin/sh、应用启动或桌面UI验收；请逐项报告PASS/FAIL/未测。
