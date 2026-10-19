# QuantLab 架构说明

## 分层

```
web/ (React + TypeScript)          浏览器里的工作台：总览、回测、参数扫描、模拟盘
        │  HTTPS/HTTP + Bearer 令牌，类型由 OpenAPI 生成
        ▼
src/quantlab/api/ (FastAPI)        接口层：校验输入、调用领域层、转换结果；不含业务逻辑
src/quantlab/cli.py                命令行：同样只负责展示
        │
        ▼
src/quantlab/core/                 领域层：不依赖 Web 或命令行
  data.py       Yahoo 复权日线 + SQLite 缓存
  strategy.py   均线交叉：价格 → 每天的目标仓位
  backtest.py   逐日回测引擎：次日开盘成交、滑点、手续费、整股
  metrics.py    年化、波动率、夏普、最大回撤、胜率
  scan.py       参数网格、样本内/样本外划分、秩相关
  research.py   回测、扫描、信号三个工作流，供命令行和接口共用
  paper.py      模拟盘：不可改写的每日决定 + 从决定重放账户
  charts.py     命令行用的 matplotlib 图表
        │
        ▼
src/quantlab/storage.py            SQLite 连接 + 版本化迁移（PRAGMA user_version）
src/quantlab/config.py             QUANTLAB_* 环境变量 / .env
src/quantlab/errors.py             领域错误 → HTTP 状态码
```

依赖方向只能向下：`core` 不导入 `api` 或 `cli`。

## 数据正确性

这些规则都有单元测试保护：

| 规则 | 原因 | 测试 |
| --- | --- | --- |
| 价格按拆股和分红复权 | 拆股会让未复权均线出现假信号；不复权会漏掉分红 | — |
| 信号用当天收盘价算，**下一个交易日开盘**成交 | 防止“偷看未来”：收盘价出来之前不可能按它成交 | `test_trades_at_next_open_not_same_close` |
| 计入滑点和手续费，按整股成交 | 回测不能比现实更便宜 | `test_fee_and_slippage` |
| 交易中的当天 K 线不参与计算 | 盘中价格不是收盘价 | `test_unfinished_session_bar_dropped` |
| 模拟盘的决定写入后不可修改 | 前向测试不能事后改参数 | `test_one_decision_per_close_and_never_rewritten` |
| 补录的决定与按时记录的完全一致 | 每个决定只看当天及以前的数据 | `test_late_decisions_match_on_time_ones` |
| 参数扫描的所有组合用同一段日期比较 | 公平比较 | `test_windows_split_after_warm_up_without_overlap` |

## 安全模型

- 默认只监听 `127.0.0.1`；改成其他地址时启动日志会给出提示。
- 除 `/api/health` 外，所有接口都要 `Authorization: Bearer <令牌>`，比较时用常数时间（`hmac.compare_digest`）。令牌可以由环境变量提供；没有提供时首次启动生成，保存在 `data/api_token`，文件只允许所有者读写。
- 网页从启动链接里取出令牌，存进 sessionStorage（关掉标签页就失效），并从地址栏删掉；`Referrer-Policy: no-referrer` 防止令牌通过 Referer 泄露。
- 安全响应头：CSP（只允许本站脚本）、`X-Frame-Options: DENY`、`nosniff`；接口响应 `Cache-Control: no-store`。
- 股票代码在进入 URL 或数据库之前，先按白名单格式校验；静态文件服务拒绝访问 web 目录以外的路径。
- 代码库里不保存任何密钥；`.env` 在 `.gitignore` 中。
- 不会执行用户或大模型提供的代码；不包含实盘下单功能。

## 与上游开源项目的关系

| 项目 | 采用方式 | 原因 |
| --- | --- | --- |
| [TradingAgents](https://github.com/TauricResearch/TradingAgents)（Apache-2.0） | 计划作为**锁定版本的依赖**，通过适配层接入（第二批） | 代码质量高、更新频繁；复制源码会收不到上游修复 |
| [DeltaFStation](https://github.com/Delta-F/deltafstation) / [deltafq](https://github.com/Delta-F/deltafq)（MIT） | **只借鉴功能设计，不复制代码** | 审阅时发现：仓库里写死了 API 密钥；默认在 0.0.0.0 上开启 Flask 调试模式；AI 工具会把大模型生成的代码写成文件并执行；回测按信号当天的收盘价成交，滑点参数未生效；依赖 pandas 2，与 TradingAgents 要求的 pandas 3 冲突 |

借鉴 DeltaFStation 的部分：网页工作台的页面划分（回测、交易/模拟、策略运行、AI 助手）、模拟账户的概念、MCP 和 AI 工具调用的思路（后续批次）。

## 依赖策略

- Python 依赖在 `pyproject.toml` 声明最低版本；前端依赖在 `package.json` 锁定精确版本，并提交 `package-lock.json`。
- 新增依赖时只选发布至少两周的版本，并检查对等依赖是否兼容。
- 前端的接口类型由 `web/openapi.json` 生成；CI 会检查它和后端代码是否一致。

## 数据库迁移

每个数据库在代码里有一个按顺序排列的迁移列表。`PRAGMA user_version` 记录已执行到第几步，连接时补齐缺少的步骤；每一步在一个事务里执行，要么全部成功，要么完全不生效。新增迁移只能**追加**到列表末尾，不能修改已经发布的迁移。
