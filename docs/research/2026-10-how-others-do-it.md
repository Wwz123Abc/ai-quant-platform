# 上线产品是怎么做的

调研日期：2026-10-21 起。[市场调研](2026-10-market-research.md)回答了“市面上有什么”，这一篇回答“它们是怎么做的”，按[目标说明](../goals.md)的三件事分开写，一次写一块：

1. [记账](#一记账) ✅
2. 按规则管理持仓（待写）
3. 买前分析（待写）

> 本文是产品和技术调研，不是投资建议。

## 一、记账

选了两个对象：**Ghostfolio**（开源，可以直接读代码，看它“怎么实现”）和 **Sharesight**（商业产品，帮助文档最完整，看它“怎么定义”）。

### Ghostfolio：读代码看到的做法

GitHub 9.5k stars，AGPL-3.0。技术栈：Angular 前端，NestJS 后端，PostgreSQL（Prisma），Redis 任务队列。

**1. 数据模型：一张交易表，六种类型**

交易（`Order`）只有一张表，用类型区分：买入、卖出、**分红**、**费用**、**利息**、负债[^gf-schema]。每笔记录日期、数量、单价、费用、币种、所属账户。

行情（`MarketData`）一张表，按“数据源 + 代码 + 日期”唯一，并且有一个字段 `isCarriedForward`：某天数据源没给价格、用前一天的价格补上时，这个字段标为“是”[^gf-schema][^gf-processor]。**每个价格都知道自己是真实收盘价还是补出来的。**

**2. 行情采集：定时、分优先级、失败自动重试**

- 每小时（随机某一分钟，避免所有用户同时请求）拉最新价格和最近 7 天的数据；每周日刷新一次股票的基本资料[^gf-cron]。
- 任务放进队列，分高、中、低优先级。
- 失败自动重试 6 次，间隔按指数增长：1、3、7、15、31 分钟后重试，共约 57 分钟[^gf-config]。
- 数据源可插拔，共 10 个（Yahoo、FMP、EODHD、Alpha Vantage、CoinGecko、手工录入等）[^gf-tree]。

**3. 风险体检：每条规则一个类，阈值可调**

9 大类规则：单一账户集中度、资产类别、币种集中度、发达/新兴市场、地区分布、应急资金、费用占比、可用资金等[^gf-tree]。每条规则的写法都一样[^gf-rule]：

- `evaluate(设置)`：用阈值做判断，返回“通过 / 不通过”和一句带数字的说明，例如“最大的账户占 72%，超过上限 50%”。
- `getConfiguration()`：声明阈值的取值范围和步长，用户可以在界面上调。
- 规则可以单独开关。

**4. 收益计算：声明了四种，实际实现两种**

代码里有 ROAI（平均投入回报）、ROI（总投入回报）、TWR（时间加权）、MWR（资金加权）四个计算器，但 **TWR 和 MWR 目前是空的**，调用会报“未实现”[^gf-calc]。真正在用的是 ROAI 和 ROI。

### Sharesight：读文档看到的做法

**1. 收益：资金加权，并拆成三部分**

- 用资金加权的方法（Modified Dietz 的一个变体）算收益率，既考虑投入金额，也考虑投入时间[^ss-return]。
- 按每笔资金实际投入的时间算“平均投资年限”，满一年才显示年化收益；不满一年显示区间收益，避免把短期涨跌放大[^ss-annual]。
- 总收益拆成**资本利得、分红、汇兑收益**三部分；现金不计入收益，但计入总资产[^ss-return]。

**2. 公司行动：自动处理**

- 自动处理拆股、合股、红股、特别分红：拆股时调整股数和每股价格，总市值不变[^ss-ca]。
- 需要个人决定的事件（比如配股权，要选认购还是卖出）不自动处理，提示用户手动记录[^ss-ca]。
- 它的博客举过一个例子：有用户的持仓拆股没被记上，结果看到的是亏 47.73%，实际是赚了近 5%[^ss-ca]。

**3. 导入：成交确认邮件**

每个组合有一个专属邮箱。把券商的成交确认邮件转发过去（或让券商直接抄送），5 分钟内自动记账；支持 100 多家券商。不支持的券商，可以通过 SnapTrade 连接[^ss-import]。

### 对照我们的持仓账本

| 方面 | Ghostfolio | Sharesight | 我们的账本 |
| --- | --- | --- | --- |
| 交易类型 | 买、卖、分红、费用、利息、负债 | 同类，另有公司行动 | **只有买入、卖出**；国债定存、现金单独记录 |
| 分红 | 作为交易记录 | 自动记录，单独算分红收益 | **没有记录，分红不计入收益** |
| 拆股、合股 | 依赖数据源 | 自动调整股数和单价 | **不处理**：持仓股票拆股后，账本会显示大幅亏损 |
| 收益算法 | ROAI、ROI | 资金加权，满一年才年化；拆成资本利得、分红、汇兑三部分 | 资金加权（XIRR），满 30 天显示、不满一年标注“仅供参考”；**没有拆分来源** |
| 行情质量 | 每个价格标注是否为补出来的；失败重试 | — | 显示价格日期和“盘中 / 收盘”；**没有交叉校验，没有重试** |
| 导入 | 文件导入、接口 | 成交确认邮件，5 分钟入账 | CSV 导入、截图识别 |
| 风险体检 | 9 类规则，阈值可调，可开关 | — | 量化分析里有单只上限、现金储备、目标仓位三条规则 |

### 可以借鉴的地方（对应目标）

| # | 借鉴什么 | 来自 | 对应目标 | 说明 |
| --- | --- | --- | --- | --- |
| 1 | **分红记录**：新增“分红”交易类型，计入收益 | 两者 | 记账准确 | 现在收益偏低；VOO、QQQM 都定期分红 |
| 2 | **拆股处理**：发生拆股时调整股数和成本单价，总成本不变 | Sharesight | 记账准确 | 量化分析已经能识别拆股（新浪因子，加上漏记检测），可以复用 |
| 3 | **收益拆成三部分**：资本利得、分红、汇兑 | Sharesight | 可解释 | 港股按港元成交，汇兑部分有意义 |
| 4 | **价格质量标记**：每个价格记来源，以及是否为沿用的旧价 | Ghostfolio | 数据正确 | 和待办第 1 项（行情交叉校验）合在一起做 |
| 5 | **失败重试、指数退避** | Ghostfolio | 稳定可靠 | 刷新行情和每日任务都用得上 |
| 6 | **规则统一写法**：结论 + 数字 + 阈值，阈值可调，可开关 | Ghostfolio | 可解释 | 和待办第 5 项（建议的可解释性）合在一起做 |
| 7 | **从成交确认导入** | Sharesight | 记账准确 | 做待办第 2 项（和汇丰对账）时，看汇丰能提供什么：成交确认邮件、月结单 |

[^gf-schema]: [Ghostfolio：prisma/schema.prisma](https://github.com/ghostfolio/ghostfolio/blob/main/prisma/schema.prisma)
[^gf-processor]: [Ghostfolio：data-gathering.processor.ts](https://github.com/ghostfolio/ghostfolio/blob/main/apps/api/src/services/queues/data-gathering/data-gathering.processor.ts)
[^gf-cron]: [Ghostfolio：cron.service.ts](https://github.com/ghostfolio/ghostfolio/blob/main/apps/api/src/services/cron/cron.service.ts)
[^gf-config]: [Ghostfolio：libs/common/src/lib/config.ts](https://github.com/ghostfolio/ghostfolio/blob/main/libs/common/src/lib/config.ts)
[^gf-tree]: [Ghostfolio：apps/api/src](https://github.com/ghostfolio/ghostfolio/tree/main/apps/api/src)（规则在 `models/rules/`，数据源在 `services/data-provider/`）
[^gf-rule]: [Ghostfolio：account-cluster-risk/current-investment.ts](https://github.com/ghostfolio/ghostfolio/blob/main/apps/api/src/models/rules/account-cluster-risk/current-investment.ts)
[^gf-calc]: [Ghostfolio：portfolio/calculator](https://github.com/ghostfolio/ghostfolio/tree/main/apps/api/src/app/portfolio/calculator)（代码读于 2026-10-21）
[^ss-return]: [Sharesight Help：Return calculation](https://help.sharesight.com/au/performance_calculation_method/)
[^ss-annual]: [Sharesight Help：Absolute and annualised return](https://help.sharesight.com/au/absolute-and-annualised-return/)
[^ss-ca]: [Sharesight Help：How Sharesight automatically handles corporate actions](https://help.sharesight.com/us/how-sharesight-automatically-handles-corporate-actions/)
[^ss-import]: [Sharesight：Import trades via your broker's trade confirmations](https://www.sharesight.com/us/partners/contract-notes/)
