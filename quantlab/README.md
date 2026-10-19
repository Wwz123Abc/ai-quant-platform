# QuantLab 量化研究工作站

美股和港股的量化研究工具：**回测**、**参数扫描与样本外检验**、**每日信号**、**模拟盘**。可以在命令行使用，也可以打开网页工作台。

- **数据**：Yahoo Finance 日线，按拆股和分红复权，缓存在本地 SQLite。
- **回测**：按收盘价算信号，**下一个交易日开盘成交**，计入滑点和手续费、按整股成交，并和同期“买入持有”对比。
- **样本外检验**：在前一段历史上挑参数，在后一段历史上检验，判断“最优参数”是不是过拟合。
- **模拟盘**：每个交易日收盘后记录策略的决定，写入后不可修改，是真正的前向测试。
- **工程化**：分层架构、FastAPI 接口（自动生成 OpenAPI 文档）、React + TypeScript 工作台、访问令牌、安全响应头、数据库迁移、单元测试和接口测试、CI、Docker。

> 仅用于学习和研究，不会下单，也不构成投资建议。回测结果不代表未来表现。

![QQQ 回测](docs/qqq.png)

## 快速开始

需要 Python 3.11 或以上版本。网页工作台需要先构建一次，需要 Node.js 20 或以上版本。

```bash
pip install -e ".[dev]"
```

```bash
cd web && npm ci && npm run build
```

启动服务（默认只允许本机访问）：

```bash
python -m quantlab serve
```

终端里会显示一个带令牌的链接，例如 `http://127.0.0.1:8000/?token=…`，用浏览器打开就能进入工作台。接口文档在 `http://127.0.0.1:8000/api/docs`。

## 网页工作台

| 页面 | 功能 |
| --- | --- |
| 总览 | 模拟账户的收益，以及持仓股票最新的均线信号 |
| 均线回测 | 价格与均线（标出买卖点）、账户价值、回撤、指标对比、交易明细 |
| 参数扫描 | 样本内和样本外两张热力图（同一色阶）、排名表，以及自动生成的结论 |
| 模拟盘 | 创建账户、记录每日决定、持仓、待执行订单、账户价值曲线、成交记录 |

## 命令行

```bash
python -m quantlab backtest QQQ --start 2005-01-01
python -m quantlab scan QQQ --start 2005-01-01 --split 2019-01-01
python -m quantlab signal QQQM VOO 700
python -m quantlab paper new 核心 --symbols QQQM,VOO --cash 10000
python -m quantlab paper run
```

每个命令都可以加 `-h` 查看全部参数。港股直接写数字代码，比如 `700` 表示腾讯控股。

### 回测示例

```
QQQ  均线 50/200  2005-01-03 → 2026-10-16（21.8 年）
                 均线策略                            买入持有
  期末资产       $113,940                            $207,980
  年化收益       +11.8%                              +14.9%
  夏普比率       0.74                                0.76
  最大回撤       -28.5%（2020-02-19 → 2020-03-16）   -53.4%（2007-10-31 → 2008-11-20）
```

### 参数扫描：一个关于过拟合的发现

在 2005–2018 年的数据上，给 75 组均线组合按夏普比率排名，再看它们在 2019–2026 年的表现：

| 标的 | 样本内第 1 名 | 它在样本外的排名 | 两段排名的相关系数 | 跑赢同期买入持有？ |
| --- | --- | --- | --- | --- |
| QQQ | 40/125 | 49 / 75 | 0.07 | 没有 |
| SPY | 15/175 | 11 / 75 | 0.24 | 没有 |
| MCD | 100/250 | 39 / 75 | −0.02 | 没有 |

两段排名几乎不相关，说明挑出来的“最优参数”主要是在拟合噪声。

![QQQ 参数扫描](docs/qqq_scan.png)

### 模拟盘怎么保证记录可信

- **只存决定，写入后不能修改。** 每个交易日收盘后，对每只股票记录一次“持有”或“空仓”。
- **从创建之后才开始交易。** 第一批决定按创建时最新的收盘价做出，下一个交易日开盘才模拟成交。
- **漏跑几天没关系。** 下次运行时会补录，每天只用当天及以前的数据，结果和按时记录的完全一样。
- **成交和资产每次都从决定重新计算**，所以分红导致复权价格调整时，账户不会对不上。显示的成交价是复权价。

想每个交易日自动记录，可以用 Windows 任务计划程序在北京时间早上运行（路径换成你的）：

```
schtasks /create /tn "QuantLab 模拟盘" /sc weekly /d TUE,WED,THU,FRI,SAT /st 07:30 /tr "cmd /c cd /d C:\path\to\quant-lab && python -m quantlab paper run >> paper.log 2>&1"
```

## 接口

除 `/api/health` 外，所有接口都需要请求头 `Authorization: Bearer <令牌>`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查（不需要令牌） |
| GET | `/api/session` | 检查令牌 |
| GET | `/api/market/{symbol}/bars` | 复权日线 |
| GET | `/api/signals?symbols=QQQM,VOO` | 最新的均线信号 |
| POST | `/api/backtests` | 均线回测 |
| POST | `/api/scans` | 参数扫描与样本外检验（一次最多 200 组） |
| GET / POST | `/api/paper/accounts` | 列出 / 创建模拟账户 |
| GET / DELETE | `/api/paper/accounts/{name}` | 账户详情 / 删除 |
| POST | `/api/paper/accounts/{name}/record` | 记录最新的决定 |

错误统一返回 `{"detail": "说明"}`：参数不对是 400 或 422，找不到是 404，名字重复是 409，行情源出错是 502。

## 配置

全部设置都可以用环境变量或 `.env` 文件提供，见 [.env.example](.env.example)。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `QUANTLAB_DATA_DIR` | `data` | 数据库和令牌的存放目录 |
| `QUANTLAB_REPORTS_DIR` | `reports` | 命令行输出的图表和 CSV |
| `QUANTLAB_HOST` / `QUANTLAB_PORT` | `127.0.0.1` / `8000` | 监听地址和端口 |
| `QUANTLAB_API_TOKEN` | 自动生成 | 访问令牌 |
| `QUANTLAB_LOG_FORMAT` | `text` | 设为 `json` 时每行输出一个 JSON，便于日志系统收集 |

## 开发

```bash
pytest
```

```bash
ruff check src tests scripts
```

```bash
cd web && npm run typecheck && npm run lint && npm test
```

前端开发时，先启动 `python -m quantlab serve`，再在 `web` 目录运行 `npm run dev`；接口请求会自动转发到 8000 端口。

修改了接口以后，重新生成前端类型（CI 会检查两者是否一致）：

```bash
python scripts/export_openapi.py && cd web && npm run gen:api
```

## 部署（Docker）

```bash
docker compose up -d --build
```

容器里的服务只映射到本机的 `127.0.0.1:8000`，数据保存在 `quantlab-data` 卷里。用 `docker compose logs` 可以看到带令牌的启动链接。

## 项目结构

```
src/quantlab/
  core/        领域层：数据、策略、回测、指标、扫描、模拟盘（不依赖 Web）
  api/         FastAPI 接口：路由、请求和响应模型、令牌校验
  cli.py       命令行
  config.py    配置        errors.py  领域错误       storage.py  SQLite 迁移
web/           React + TypeScript 工作台（构建结果放进 src/quantlab/web）
tests/         Python 单元测试和接口测试
docs/          架构说明和图片
```

设计和取舍见 [docs/architecture.md](docs/architecture.md)，其中也说明了与 TradingAgents、DeltaFStation 两个开源项目的关系。

## 路线图

- [x] 回测、参数扫描与样本外检验、每日信号
- [x] 模拟盘（不可改写的每日决定）
- [x] 接口服务和网页工作台
- [ ] AI 研究层：以锁定版本的依赖接入 TradingAgents，加入费用预算和费用台账，AI 评级作为模拟盘的信号来源
- [ ] 服务内置定时任务（每个交易日自动记录）
- [ ] 滚动检验（walk-forward）、更多策略、组合回测

## 许可

MIT
