# 持仓账本运维手册

线上：https://stock.wwz.cool （服务器 49.232.210.129，代码和数据在 `/opt/stock-ledger`）。异地备份在新服务器 42.194.244.252 的 `/var/backups/ledger`。

首页底部的“系统状态”（只有平台所有者能看到）显示下面各项最近一次的结果，超时或失败会标 ⚠。

## 部署

```bash
ledger/deploy/deploy.sh
```

脚本会依次做这些事，任何一步不对就停下：

1. `ledger/` 里有没提交的改动就不部署。
2. 对比服务器上的每个文件和上次部署的提交（服务器上的 `.deployed`）；有人在服务器上手动改过文件就停下，列出是哪些文件。
3. 备份服务器上正在运行的文件（`page-backups/deploy-时间`）和数据库。
4. 安装新文件；`app.py` 有改动才重启服务。
5. 检查服务能正常响应；不能就自动换回上一版，并在 `deploy.log` 记一笔 `rolled-back`。

部署记录在服务器的 `/opt/stock-ledger/deploy.log`。

## 备份

| 什么 | 什么时候 | 在哪里 | 保留 |
| --- | --- | --- | --- |
| 本地备份（SQLite 在线备份，运行中也能拿到一致的快照） | 每天 03:30 | `/opt/stock-ledger/data/backups` | 30 份 |
| 异地备份：发送前检查完整，收到后再检查一次 | 每天 03:45 | 新服务器 `/var/backups/ledger` | 60 份 |

- 发送用旧服务器上的专用密钥 `/root/.ssh/ledger_backup`，它在新服务器上只能执行“接收备份”这一条命令（`restrict,command=...`），不能登录、不能转发端口。
- 结果写在 `/opt/stock-ledger/data/offsite.json`，系统状态里显示。
- 脚本：[backup-push.sh](backup-push.sh)（发送，装在旧服务器的 `/usr/local/bin/ledger-backup-push`）、[backup-receive.sh](backup-receive.sh)（接收，装在新服务器的 `/usr/local/bin/ledger-backup-receive`）。定时任务在旧服务器的 `/etc/cron.d/stock-ledger`。

> 平台搬到新服务器以后，异地备份要改成发回旧服务器（或另一台机器），不能和数据放在同一台。

## 恢复演练（建议每季度一次）

```bash
ledger/deploy/restore-drill.sh
```

在新服务器上把最新的异地备份恢复成一个临时账本（用当前提交的代码），启动、检查能响应，并和线上的记录数逐类对比，然后删掉临时账本。结果写进 `data/drill.json`，系统状态里显示；超过 90 天会标 ⚠。

## 真的需要恢复时

1. 先停服务，保留现场：`systemctl stop stock-ledger`，把当前的 `data/ledger.db` 复制一份。
2. 选备份：本地的在 `data/backups/`，异地的在新服务器 `/var/backups/ledger/`（文件名是时间）。
3. 放回去：`install -o stockledger -g stockledger -m 644 备份文件 /opt/stock-ledger/data/ledger.db`，并删掉旁边旧的 `ledger.db-wal`、`ledger.db-shm`。
4. 启动并检查：`systemctl start stock-ledger`，打开网站核对持仓；备份之后录入的记录需要重新录。
