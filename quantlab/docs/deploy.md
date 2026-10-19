# 部署到服务器：账本网站的 /lab/

QuantLab 和美股账本放在同一台服务器、同一个域名下：账本在 `/`，量化平台在 `/lab/`，用账本的登录。

```
浏览器 ──HTTPS──> nginx ──/──────> 账本 127.0.0.1:5090
                    │
                    └─/lab/──> 先问账本 GET /api/sso（是平台所有者吗？）
                               是 → 转发到 QuantLab 127.0.0.1:8010，带上 X-Ledger-User
                               否 → 页面跳回账本登录；接口返回 401
```

- **只有一个账号能用**：账本里用 `python3 app.py platform-owner 用户名` 指定，`platform-owner off` 关闭。
- **代理模式**（`QUANTLAB_AUTH_MODE=proxy`）：QuantLab 不再用访问令牌，而是相信 nginx 传来的用户头；所以它只能监听 127.0.0.1（监听其他地址会拒绝启动）。会改数据的请求还要带 `X-Requested-With: quantlab`，防止别的网站借你的登录状态发请求。
- **子路径**（`QUANTLAB_BASE_PATH=/lab`）：页面里写入 `<base href="/lab/">`，资源、接口和前端路由都跟着它走；同一份构建既能在根路径用，也能在 `/lab/` 下用。
- **国内行情**（`QUANTLAB_PRICE_SOURCE=cn`）：服务器访问不了 Yahoo，日线改用腾讯不复权 K 线 + 新浪复权因子，换算成和 Yahoo 一样的比例复权，并自动补上因子表漏记的拆股。

## 步骤

1. 本地构建：`cd web && npm run build`，然后 `pip wheel . --no-deps -w dist`；TradingAgents 也在本地打成 wheel（服务器连不上 GitHub）。
2. 服务器：创建 `quantlab` 系统用户和 `/opt/quantlab`，建虚拟环境，用腾讯云的 PyPI 镜像安装两个 wheel（`quantlab-*.whl[ml]`）。
3. 上传 `data/universe/` 下的两个成分股文件（服务器连不上纳斯达克和维基百科）。
4. 安装 [deploy/quantlab.service](../deploy/quantlab.service)，把 [deploy/nginx-lab.conf](../deploy/nginx-lab.conf) 加进账本网站的 server 块，`nginx -t` 后重新加载。
5. 账本的只读密钥直接写进 QuantLab 的 `.env`，不经过屏幕：账本运行 `app.py read-key 用户名 文件`，再把文件装到 `/opt/quantlab/.env`（属主 quantlab，权限 600）。
6. 其他密钥自己在服务器上输入（输入时不显示）：

```bash
cd /opt/quantlab && sudo -u quantlab /opt/quantlab/venv/bin/quantlab secrets set SEC_EDGAR_USER_AGENT
```

`quantlab secrets status` 只显示每个密钥是否已配置。改完 `sudo systemctl restart quantlab`。

## 已知限制

- TradingAgents 的行情来自 Yahoo（yfinance），服务器上会被限流（429），所以 AI 研报暂时只能在本地电脑上运行。
