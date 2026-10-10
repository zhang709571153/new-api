# 原套餐目录恢复

`legacy-catalog-20261010.json` 来自已导入 PostgreSQL 的原始 RealYu 模板，2026-10-10 21:48 只读提取。文件仅包含套餐与路由组摘要，无客户身份、Key、余额或商户密钥。

已启用的六档个人套餐为 Lite ¥98、Starter ¥198、Pro ¥398、Max ¥698、Ultra ¥1398、Scale ¥2598。全部为 28 天、四个周窗口。恢复保留原整数 quota，不重算既有权益或折算两次。已禁用的免费旧月度模板不会恢复。

当前生产源 `default` 经已审核迁移映射到 native group **2**，与 64 把有效个人 Key 的归属一致。native group 1 的名字虽为 `default`，也不能据此替代 group 2。在另一台机器执行前应再次核对源映射；组 ID 是显式参数。

先运行纯离线预览：

```powershell
$catalog = Resolve-Path .\deploy\sub2api\legacy-catalog-20261010.json
$catalogHash = (Get-FileHash -LiteralPath $catalog -Algorithm SHA256).Hash.ToLowerInvariant()
python .\deploy\sub2api\restore_native_catalog.py --catalog $catalog --catalog-sha256 $catalogHash --group-id 2
```

实际写入必须附 `--execute --expected-version <已验收的UX版本> --credentials <私有管理员凭据JSON> --journal <新的私有执行记录>`。凭据 JSON 使用 `admin_username`、`admin_password`；不要放入 Git 或命令参数。默认目标是 `https://api.realyu.fun`，仅另允许已隔离的 `http://127.0.0.1:29481`。程序在登录前验证版本、Sub2API 单核心身份，在创建前检查 CNY/7 显示合同。

执行走正常管理员 API：原生计划下架创建 → managed entitlement 配置 → 重新读取核对。所有计划保持下架；工具不启用支付、注册、商户，不发放任何权益，也不更改客户余额。下架候选核对、钱包 E2E 和发布验收完成后，才由正常管理页面明确上架。

使用同一 journal 可恢复已知进度。源模板 ID 在数据库中唯一；工具也检查源 ID 重复。若原生 POST 返回不明且存在同名未映射计划，停止并人工核对，绝不盲目重试创建新计划。不要并行运行目录导入。已经上架或被管理员更改的计划会被拒绝覆盖。

验证：`python -m unittest discover -s deploy/sub2api -p test_restore_native_catalog.py -v`，9 项通过，覆盖金额精度、上下限、错误布尔值、启用/禁用、重复来源、顺序、组显式选择与恢复时配置漂移。22:14 已在真实候选 29481 经正常管理员 HTTP 导入六档，并使用同一 journal 重入：仍是相同六个原生计划、全部下架；钱包、订阅、订单、发放计数均未变化，没有调用商户或改注册/支付设置。见[脱敏结果](../../lab/sub2api_e2e/catalog-restore-http-20261010.json)。这是隔离验证，生产目录尚未恢复。
