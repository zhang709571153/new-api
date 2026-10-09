# WorkBuddy 自定义安装目录修复：Windows 1.4.30

重新运行工作台中的原 WorkBuddy 配置命令即可。安装器会检查默认目录、当前用户和计算机的 App Paths / 卸载注册表，并读取 InstallLocation、DisplayIcon，覆盖32/64位注册表视图。

未注册的便携或自定义安装可以先指定完整程序路径，再运行原配置命令：

```powershell
$env:REALYU_WORKBUDDY_EXE = 'D:\Softwares\WorkBuddy\WorkBuddy.exe'
```

指定路径后只验证该程序，不静默切换到别的安装。最低版本仍为5.6.2；无需重装应用，无需Python或Node。路径不需要加入系统PATH。只读取注册表，不修改安装记录，不运行卸载命令。

回传时提供安装器版本、四阶段结果、诊断编号即可，不附带API Key。关闭后重开WorkBuddy，分别检查模型和图片MCP；配置成功不等于桌面图片已验收。

本机已复现旧函数漏检，验证PS5.1/PS7的17项发现测试，以及本地API夹具下自定义路径完整安装与重复运行。客户D盘电脑的最终安装结果仍需现场重跑。
