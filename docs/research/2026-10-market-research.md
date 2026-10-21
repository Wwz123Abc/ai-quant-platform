# 市场调研：同类产品和数据接口（2026-10）

调研日期：2026-10-21。目的：在新服务器备案期间，弄清楚两件事，用来排定备案后的开发顺序。

1. 同类产品和开源项目做了什么、做得怎样，我们的平台缺什么、哪些值得做。
2. 在国内服务器上，美股数据和券商接口有哪些能用：免费额度、能否访问、条款和合规风险。

**方法**：网页检索（官方页面优先，媒体报道其次，第三方汇总只作参考），加上在新服务器（腾讯云广州）上实测每个接口能否访问。免费额度变化很快，表里的数字以各家官网为准。

> 本文是产品和技术调研，不是投资建议，也不是法律意见。涉及监管的内容只转述公开报道，具体以监管部门和券商的正式公告为准。

## 结论摘要

1. **行业方向：券商把 AI 做成“智能体 + 技能接口”。** 长桥把 PortAI 升级为 LongbridgeAI，并通过 API、MCP 和技能接入外部 AI 助手[^lb]；moomoo 推出 Moomoo Skills，把行情和交易能力接到 Claude 等外部智能体[^moomoo]；老虎有官方的 tigeropen-skill[^tiger-skill]；东方财富把妙想的数据、估值模型和交易工具做成可调用的技能插件[^mx]。
2. **我们的差异在“规则和可核验”。** 大厂的 AI 主要是问答、研报和选股。我们的执行端只按本人设定的规则给出建议订单，信号只作提醒；每份 AI 研报都有数据来源和费用记录；机器学习结论按预登记的标准检验。这些是大厂产品没有强调、也不容易做的地方。
3. **值得补的功能**：持仓“体检”规则（集中度、币种、单只风险）、和指数的业绩对比、财报日和重大公告提醒、个股分析里的内部人交易。这几项数据都已经能拿到。另外可以做一个只读的 MCP 接口，让 AI 助手直接查询自己的持仓和分析结果，和行业方向一致。
4. **券商连接器（路线图阶段 5）要改方向。** 2026 年 5 月起，富途、老虎、长桥的境内业务被认定为非法经营证券业务；6 月 12 日起，三家对内地存量账户只卖不买、只出不进[^cnr][^21jj]。盈透从 2025 年 9 月起，要求内地居民提供境外工作或生活证明才能开户[^yicai]。所以“接入境外券商自动下单”这条路对内地居民不可行。
5. **数据：现在的组合够用，但要加备用源和健康检查。** 腾讯和新浪是非官方接口，没有公开条款，随时可能变；东方财富的数据和腾讯逐项一致，适合作为交叉校验和备用。Stooq 有人机验证，排除。Alpha Vantage 的新闻接口对免费 key 不开放（实测）。

## 一、同类产品

### 国内券商和财经平台

| 产品 | AI 能力 | 持仓相关 | 对我们的启示 |
| --- | --- | --- | --- |
| 同花顺 i问财 | 自然语言选股，把模糊描述转成量化选股条件；多维度的 AI 股票分析[^wencai] | 交易多跳转合作券商[^wencai-report] | 自然语言选股是它的强项，我们不和它比 |
| 东方财富 妙想 | 自研金融大模型；AI 研究员、深度研究；技能插件化；持仓风险诊断[^mx][^mx-36kr] | 打通行情、交易和投研 | 深度研究按次数收费（体验版每天 3 次，付费版每季度 518/818 元，以官方为准）[^mx-36kr]；我们每次约 ¥1、每月预算硬上限，成本透明 |
| 雪球 AI | 基于 DeepSeek 开源模型的问答，结合雪球社区内容[^xq] | 组合、社区 | 社区内容是它的护城河，我们不做社区 |
| 富途 moomoo AI | 分析师式研究报告、自然语言预填交易、跟踪机构和名人持仓（Smart Money）、Moomoo Skills 接外部智能体[^moomoo] | 组合监控 | “跟踪机构持仓和内部人交易”我们已能拿到数据，可以加进个股分析 |
| 长桥 LongbridgeAI | 自建投资智能体，捕捉异动并关联事件和情绪；MCP 和技能接入外部 AI[^lb] | 交易 | MCP 接口是低成本、高价值的方向 |
| 老虎 TigerAI | 搜资讯、总结业绩会、个股异动推送[^tiger] | 交易 | 事件推送值得做（我们有 8-K 公告数据） |

共同点：这些 AI 都声明“不构成投资建议”，例如 moomoo 写明它不是投资顾问、不直接给出投资建议[^moomoo-ai]。我们的定位一致：建议订单只来自用户自己的规则。

### 海外持仓追踪工具

| 产品 | 特点 | 价格（第三方资料） |
| --- | --- | --- |
| Sharesight | 自动跟踪股票、ETF、分红、汇率、拆股；和指数或个股做业绩对比[^trackers] | 约 $11.25/月起（年付）[^trackers] |
| Snowball Analytics | 侧重分红分析和再平衡，接入 1000 多家券商[^snowball] | 约 $149.99/年[^snowball] |

启示：**和指数的业绩对比**、**分红日历**是这类工具的标配，我们的账本有收益走势和资金加权收益率，但还没有“同期买 QQQ/SPY 会怎样”的对比。

### 开源项目（GitHub 数据取于 2026-10-21）

| 项目 | Stars | 许可 | 定位 | 和我们的关系 |
| --- | --- | --- | --- | --- |
| [TradingAgents](https://github.com/TauricResearch/TradingAgents) | 112k | Apache-2.0 | 多智能体 LLM 交易研究框架 | 已接入（锁定版本 + 国内数据适配） |
| [OpenBB](https://github.com/OpenBB-finance/OpenBB) | 74k | 见仓库 | 面向分析师和 AI 智能体的开放数据平台 | 数据层的参考；它也在做“给智能体用的数据” |
| [ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) | 64k | MIT | 多个“投资大师”智能体做决策的概念验证，仅供教育，不真实交易 | 同类 AI 研报项目；数据来自付费的 Financial Datasets |
| [Qlib](https://github.com/microsoft/qlib) | 50k | MIT | 微软的 AI 量化研究平台 | 机器学习选股的参考框架 |
| [vn.py](https://github.com/vnpy/vnpy) | 46k | MIT | Python 量化交易框架，以国内市场为主 | 若以后走境内券商路线可参考 |
| [NautilusTrader](https://github.com/nautechsystems/nautilus_trader) | 30k | LGPL-3.0 | Rust 内核的事件驱动交易引擎 | 回测和实盘一致性的参考 |
| [Ghostfolio](https://github.com/ghostfolio/ghostfolio) | 9.5k | AGPL-3.0 | 开源财富管理：交易记录、多账户、含分红的收益、**组合风险静态分析**、导入导出 | 最接近我们的“持仓账本”；风险静态分析值得借鉴 |
| [vectorbt](https://github.com/polakowo/vectorbt) | 9.5k | 见仓库 | 向量化回测 | 参数扫描提速的参考 |
| [FinRobot](https://github.com/AI4Finance-Foundation/FinRobot) | 8.3k | Apache-2.0 | 金融 AI 智能体平台 | 同类 |

**我们的定位**：没有一个开源项目同时做“真实持仓记账 + 规则化调仓建议 + 多智能体 AI 研报 + 可核验的量化研究”，并且能部署在国内服务器上。这是平台写进简历时最清楚的一句话。

## 二、数据和接口

### 新服务器实测（2026-10-21，腾讯云广州）

“需要 key”指能连上，但没有 key 时返回 401/403。

| 来源 | 用途 | 实测 | 免费额度（以官网为准） | 条款和风险 |
| --- | --- | --- | --- | --- |
| 腾讯 K 线 / 行情 | 日线、名称 | ✅ 0.05 秒 | 无公开额度 | 非官方接口，没有公开条款，可能随时变化 |
| 新浪复权因子 | 拆股、分红调整 | ✅ | 无公开额度 | 同上；已发现漏记拆股并自动补正 |
| 东方财富 K 线 | 备用、交叉校验 | ✅ QQQM 最近 3 天与腾讯逐项一致 | 无公开额度 | 非官方接口 |
| SEC EDGAR | 财报、登记信息、公告 | ✅ | 免费；每秒不超过 10 次，需在请求头写联系人 | 官方，最可靠 |
| FRED | 宏观数据 | ✅（需 key） | 免费 key | 官方 |
| Finnhub | 新闻、内部人交易 | ✅（已配 key，实测新闻和内部人交易都有数据） | 约每分钟 60 次[^finnhub] | 免费版限个人、非商业用途[^finnhub] |
| Alpha Vantage | 新闻 | ❌ 新闻接口对免费 key 显示“付费专用”（实测） | — | 放弃 |
| Massive（原 Polygon） | 日线 | 能连上（需 key） | 约每分钟 5 次[^apis] | 免费版限个人或评估用途 |
| Twelve Data | 日线 | 能连上（需 key） | 每分钟 8 次、每天 800 次，延迟 15 分钟[^apis] | 同上 |
| FMP | 行情、财报 | 能连上（需 key） | 约每天 250 次[^apis] | 同上 |
| EODHD | 日线 | 能连上（需 key） | 约每天 20 次[^apis] | 同上 |
| Tiingo | 日线 | ✅ 能连上 | 未能核实 | 同上 |
| Marketaux | 新闻 | 能连上（需 key） | 未核实 | — |
| Stooq | 日线 | ❌ 人机验证页 | — | 排除（不绕过验证） |
| Yahoo Finance | 日线、新闻 | ❌ 限流 429 | — | 国内服务器不可用 |
| NewsAPI、GDELT、Google News | 新闻 | ❌ 超时 | — | 不可用 |

**结论**

- 主力组合保持不变：腾讯 + 新浪（行情）、SEC（财报和公告）、FRED（宏观）、Finnhub（新闻和内部人交易）。全部免费，个人使用符合各家的免费条款。
- 腾讯和新浪是最大的单点风险：加一个“数据健康检查”，每天用东方财富交叉校验最新收盘价，偏差超过阈值时提醒；腾讯失效时切换到东方财富。
- 如果需要一个有正式条款的备用源，Twelve Data 或 Massive 的免费版够每天两次分析，但要多管理一个 key。

## 三、券商接口和合规

### 境外互联网券商

| 券商 | 开放接口 | 模拟环境 | 内地居民现状 |
| --- | --- | --- | --- |
| 富途 | Futu OpenAPI，需运行 OpenD 网关[^futu-opend] | 下单默认在模拟环境，切实盘要交易密码和二次确认[^futu-skill] | 2026-06-12 起内地账户暂停买入和入金，可卖出和出金；已停止为内地身份开户[^cnr][^21jj] |
| 老虎 | tigeropen（Python 等 SDK）[^tiger-pypi] | 未查到模拟账户的开通条件 | 同上；自 2023 年起已停止内地身份新开户[^21jj] |
| 长桥 | OpenAPI，App Key + Access Token（有效期三个月）[^lb-qa] | 有模拟账户，不开实盘也能调试行情和交易接口[^lb-qa] | 同上[^cnr]；新服务器访问其接口超时 |
| 盈透 IBKR | 有 | 有 | 2025-09 起，内地居民开户需提供近 3 个月的境外工作或生活证明[^yicai] |

监管背景（据媒体报道）：2026 年 5 月 22 日，证监会会同多部门发布整治方案，认定三家的境内业务构成非法经营证券业务；6 月 12 日起执行“只卖不买、只出不进”；整治期两年，2028 年 5 月前有序撤离[^21jj][^cnr]。

### 境内渠道

- **QDII 基金和 ETF**：在 A 股上市，用国内券商账户交易，是内地居民投资美股的合法渠道之一。2026 年 9 月以来，纳指 100 ETF 多次溢价，部分超过 10%，10 月 13 日有 15 只相关 ETF 盘中临时停牌；额度紧张，限购频繁调整[^qdii]。
- **国内券商的程序化接口**（QMT、PTrade 等）：沪深交易所的程序化交易管理实施细则自 2025-07-07 施行，**未经报告不得进行程序化交易**；每秒 300 笔或每天 2 万笔以上属于高频交易，从严管理[^sse][^wscn]。开通门槛各券商不同，网上说法从 10 万到 50 万元日均资产不等，未能从官方核实。

### 对平台的影响

- **路线图阶段 5（券商连接器）原计划接入境外券商下单，对内地居民不可行**，应该改写。可行的方向：
  1. 继续用“系统给建议、本人逐条确认、自己下单”的方式（现在的做法），不接任何券商的下单接口。
  2. 只读对账：导入券商对账单或截图（账本已有截图导入），核对账本和券商的持仓是否一致。
  3. 如果以后真的要做境内自动化，前提是先完成程序化交易报告，并且只用合规渠道。这是你自己的决定，平台只做技术准备。
- **执行端的建议订单**：如果某个持仓所在的券商对内地账户“只卖不买”，那么规则给出的**买入**建议在该账户里无法执行。可以考虑在账本里给账户标注“只能卖出”，执行端据此只给卖出或调整建议。
- **自动交易模块**：维持模拟券商，不接实盘。

## 四、对路线图的建议

按“价值 / 工作量”排序，供你决定。前三项不需要域名，备案期间就能做：

| 优先级 | 事项 | 理由 |
| --- | --- | --- |
| 1 | 数据健康检查：每天用东方财富交叉校验收盘价，腾讯失效时自动切换 | 行情是所有功能的地基，目前依赖两个非官方接口 |
| 2 | 持仓体检：集中度、单只上限、币种、相关性等静态规则，结果进“我的持仓”提醒 | 参考 Ghostfolio 的风险静态分析；数据都已具备 |
| 3 | 个股分析补充：内部人交易、最近的 8-K 公告、下次财报日 | Finnhub 和 SEC 已接通，主要是展示 |
| 4 | 只读 MCP 接口：让 AI 助手查询持仓、规则检查结果和研报 | 行业方向（长桥、moomoo、老虎、东方财富都在做技能和 MCP），也适合写进简历 |
| 5 | 业绩基准对比：账本收益和同期 QQQ/SPY 对比 | 海外持仓工具的标配 |
| 6 | 阶段 5 改写：只读对账 + 账户“只能卖出”标注；删除“接入境外券商下单” | 合规现状 |

[^lb]: [KrASIA：Longbridge rolls out external AI integrations](https://amp.kr-asia.com/longbridge-rolls-out-external-ai-integrations-as-brokerages-rethink-the-trading-experience)；[Korea Herald：Longbridge wins "AI-Brokerage" at the 2026 SBR Technology Excellence Awards](https://www.koreaherald.com/article/10716151)
[^moomoo]: [Beansprout：A first look at the upgraded Moomoo AI（MooFest 2026）](https://growbeansprout.com/moomoo-ai-moofest2026)
[^moomoo-ai]: [moomoo AI features](https://www.moomoo.com/sg/events/ai-features)
[^tiger-skill]: [tigerfintech/tigeropen-skill](https://github.com/tigerfintech/tigeropen-skill)
[^tiger]: [竹子网：老虎证券推 TigerGPT](https://thebambooworks.com/cn/老虎证券推tigergpt-人工智能助投资决策/)
[^tiger-pypi]: [PyPI：tigeropen](https://pypi.org/project/tigeropen/)
[^mx]: [格隆汇：东方财富 AI 嘉年华](https://m.gelonghui.com/p/6111316)
[^mx-36kr]: [36氪：东方财富妙想](https://www.36kr.com/p/3435025501720194)
[^wencai]: [App Store：同花顺 i问财](https://apps.apple.com/cn/app/id1222204519)
[^wencai-report]: [格隆汇：同花顺 2026 中报点评](https://m.gelonghui.com/news/5299000)
[^xq]: [雪球 AI 服务声明](https://xueqiu.com/law/xueqiu-ai/deepseek)
[^trackers]: [StockAnalysis：Best stock portfolio tracker](https://stockanalysis.com/article/best-stock-portfolio-tracker/)
[^snowball]: [StockUnlock：Snowball Analytics alternatives (2026)](https://stockunlock.com/snowball-analytics-alternatives.html)
[^cnr]: [央广网：富途等三家涉罚境外券商落地整改，6 月 12 日起境内暂停新开仓及入金](https://finance.cnr.cn/ycbd/20260605/t20260605_527649089.shtml)
[^21jj]: [21 经济网：监管重拳半月后，富途、老虎、长桥宣布限购](https://www.21jingji.com/article/20260605/herald/46c67c0f120bcfc59b77497e0791a21c.html)；[新浪财经：老虎、长桥、富途先后公告，整改集体落地](https://finance.sina.com.cn/tech/roll/2026-06-05/doc-iniaikax1877356.shtml)
[^yicai]: [第一财经：全球最大互联网券商收紧内地居民开户条件](https://www.yicai.com/news/102816355.html)；[21 经济网：盈透证券跟进](https://www.21jingji.com/article/20250911/herald/e1e6938b5d572b056e1ed36bd5852c01.html)
[^futu-opend]: [Futu OpenAPI：OpenD 概述](https://openapi.futunn.com/futu-api-doc/en/opend/opend-intro.html)
[^futu-skill]: [富途牛牛 Skill：OpenAPI](https://www.futunn.com/skillhub/openapi)
[^lb-qa]: [长桥 OpenAPI 常见问题](https://open.longportapp.com/docs/qa/general)
[^finnhub]: [apicostcalc：Finnhub](https://apicostcalc.com/finnhub.html)；[QVeris：免费股票 API 对比](https://qveris.ai/guides/stock-api-free-comparison/)
[^apis]: [QVeris：Free Financial API Comparison (2026)](https://qveris.ai/guides/free-financial-api-comparison)；[Avogrowth：Free Stock Market Data APIs (2026)](https://avogrowth.com/resources/blog/free-stock-market-data-api)
[^qdii]: [同花顺财经：多只 QDII 新额度告急](https://news.10jqka.com.cn/20260911/c679799998.shtml)；[21 世纪经济报道](https://m.sfccn.com/2026/9-24/yMMDE0MDRfMjI1MjMyMw.html)
[^sse]: [上交所：发布程序化交易管理实施细则](https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20250403_10776805.shtml)
[^wscn]: [华尔街见闻：沪深交易所未经报告不得进行程序化交易](https://wallstreetcn.com/articles/3696980)
