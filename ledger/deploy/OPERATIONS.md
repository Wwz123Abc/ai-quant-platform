# 持仓账本运维手册

线上：https://wwz.hk.cn （服务器 42.194.244.252，代码和数据在 `/opt/stock-ledger`；2026-11-04 从旧服务器搬来）。不再使用旧服务器。异地备份在你的电脑上：`ledger/deploy/backup-pull.sh` 从服务器拉取最新备份到 `Desktop/stock-projects/ledger-backups`（保留 60 份），电脑开着时运行才会更新。脚本默认连 ssh 别名 `wwz`（新服务器，ubuntu + sudo）。证书（腾讯云免费，放在 `/etc/nginx/ssl/`）2027-02-02 到期，到期前重新申请并替换。

首页底部的“系统状态”（只有平台所有者能看到）显示下面各项最近一次的结果，超时或失败会标 ⚠。

## 部署

```bash
ledger/deploy/deploy.sh
```

脚本会依次做这些事，任何一步不对就停下：

1. `ledger/` 里有没提交的改动就不部署。
2. 对比服务器上的每个文件和上次部署的提交（服务器上的 `.deployed`）；有人在服务器上手动改过文件就停下，列出是哪些文件。
3. 备份服务器上正在运行的文件（`page-backups/deploy-时间`）和数据库。
4. 安装新文件，重启服务（每次都重启，保证运行的就是刚装的代码）。
5. 检查服务已经重新启动、能正常响应；不能就自动换回上一版，并在 `deploy.log` 记一笔 `rolled-back`。

部署记录在服务器的 `/opt/stock-ledger/deploy.log`。

## 备份

| 什么 | 什么时候 | 在哪里 | 保留 |
| --- | --- | --- | --- |
| 本地备份（SQLite 在线备份，运行中也能拿到一致的快照） | 每天 03:30（服务器） | `/opt/stock-ledger/data/backups` | 30 份 |
| 异地备份：让服务器先做一份最新备份，再拉到你的电脑，检查完整后保存 | Windows 计划任务 `LedgerBackupPull`：每天 9:30 和每次登录时（电脑当时没开机，开机后补跑）；也可手动运行 `ledger/deploy/backup-pull.sh` | `Desktop/stock-projects/ledger-backups`（日志 `pull.log`） | 60 份 |

- 拉取的结果写在服务器 `/opt/stock-ledger/data/offsite.json`，系统状态里显示；太久没拉会标 ⚠。计划任务在“任务计划程序”里叫 `LedgerBackupPull`，可以改时间或删除。
- 旧的“服务器发到旧服务器”方式（[backup-push.sh](backup-push.sh)、[backup-receive.sh](backup-receive.sh)）已停用，脚本留作参考。

## 行情接口自检

每天 06:15（美股收盘后）把每个外部接口各调用一次，检查数据拿得到、格式对、日期是最新的，结果写进 `data/sources.json`，系统状态里显示。手动运行：

```bash
sudo -u stockledger python3 /opt/stock-ledger/app.py check-sources
```

| 接口 | 作用 | 要求 |
| --- | --- | --- |
| 腾讯实时报价、腾讯日 K 线 | 主用：现价、收盘价、K 线 | 必须正常 |
| 新浪日 K 线 | 核对收盘价；腾讯不通时顶上 | 必须正常 |
| 新浪拆股记录、美元兑港元、美元兑人民币、黄金期货 | 拆合股提示、汇率、看板 | 必须正常 |
| 东方财富日 K 线和报价 | 备用 | 不通只提示（2026-10 起东方财富拒绝旧服务器的地址） |

## 恢复演练（建议每季度一次）

```bash
ledger/deploy/restore-drill.sh
```

在异地备份所在的旧服务器上把最新的异地备份恢复成一个临时账本（用当前提交的代码），启动、检查能响应，并和线上的记录数逐类对比，然后删掉临时账本。结果写进 `data/drill.json`，系统状态里显示；超过 90 天会标 ⚠。

## 真的需要恢复时

1. 先停服务，保留现场：`systemctl stop stock-ledger`，把当前的 `data/ledger.db` 复制一份。
2. 选备份：本地的在 `data/backups/`，异地的在新服务器 `/var/backups/ledger/`（文件名是时间）。
3. 放回去：`install -o stockledger -g stockledger -m 644 备份文件 /opt/stock-ledger/data/ledger.db`，并删掉旁边旧的 `ledger.db-wal`、`ledger.db-shm`。
4. 启动并检查：`systemctl start stock-ledger`，打开网站核对持仓；备份之后录入的记录需要重新录。

## 分时线

首页“我的股票组合”卡片里的分时线用腾讯财经的分钟线（美股 UsMinute、港股 hkMinute）。账本接口 `GET /api/intraday?symbols=VOO,QQQM` 返回每只股票最近一场的分钟价（时间已转成秒级时间戳，附这一场的开收盘时间）；只返回你自己持有的股票，每只在服务器内存里缓存 20 秒，所以多个页面同时打开也不会多取。组合线在浏览器里算：每分钟把每只股票的股数乘以截至该分钟的最新价（还没成交的用上一收盘价）再相加。代码：`app.py` 的 `minute_series`，`static/calc.js` 的 `intradayPortfolio`。

## 价格自动更新

服务器在开市时每 30 秒自动取一次腾讯的实时报价（美股 9:30–16:00 美东，港股 9:30–12:00、13:00–16:00 香港），只更新持仓里正在交易的股票的现价，标“盘中”；页面每 15 秒读一次服务器，所以打开着的页面会自己变；首页最上面的“我的股票组合”卡片把所有持仓股票合成一个标的，显示合计现值和较上一收盘的涨跌。价格没变就不写库，页面不会白刷新。收盘后补取一次，把“盘中”改成“收盘”。收盘价、历史行情、汇率、价格核对仍然只在点“刷新数据”和每天的检查里更新。你手动填写的价格不会被盖掉（手动的日期更晚时保留）。关闭：给账本服务设环境变量 `LEDGER_AUTO_QUOTES=0`。代码：`app.py` 的 `refresh_quotes`、`quote_loop`。
