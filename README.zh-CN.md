# 油电簿 Voltane

[English](README.md) | [中文](README.zh-CN.md)

> 一车两能，一本算清。自托管的油电混动用车账本：记录加油、充电和用车花费，算准混动车的油耗和电耗。

## 功能

- **混动能耗算得准**：把油箱和电池当作「库存」，同一段里程同时给出 L/100km 和 kWh/100km、每公里成本、等效油耗、电能占比。满到满、SOC 读数、油表读数统一计算。
- **自动校准电量**：根据充电记录学习「每 1% 电量对应多少度电」，不需要知道电池实际容量和充电损耗。
- **哨兵 / 静置耗电单独统计**：「状态快照」记录停车前后的电量，行驶能耗和含静置的综合能耗分开显示；异常段自动提示，可打标签或排除。
- **中途接入、记录不全也能用**：新车向导支持「已经开了一段时间」；历史汇总按合计补录；漏记自动识别、一键补录；结果按 A 精确 / B 估算 / C 粗略 / D 不足 分级回退，从不报错。
- **多人共享一辆车**：车主 / 共同记录 / 只看三种角色，邀请码或邀请链接加入，记录署名、动态、并发修改冲突提示、重复记录提醒、付款人统计。
- **记得快**：三填二自动算、加满自动算油量、峰谷电价自动算电费、常用地点、同上次、两步记充电、草稿恢复、离线保存联网同步。
- **统计**：能耗趋势、花费构成与月度走势、油价电价走势、里程、用车日历与月报、年度报告。
- **提醒**：保险、年检、保养、轮胎等，按日期和 / 或里程，哪个先到算哪个；完成后按实际日期和里程顺延。
- **数据**：CSV / JSON 导出、JSON 导入（可整批撤销）、回收站、每日自动备份、后台备份与恢复。
- **PWA**：可添加到主屏幕，长按图标直接记加油 / 充电。

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.11 · FastAPI · SQLAlchemy 2 · SQLite（WAL） |
| 前端 | 原生 ES Module，零构建；自绘 SVG 图表；PWA |
| 部署 | Docker（amd64 / arm64）· docker compose |
| CI | GitHub Actions → Docker Hub `fange173/voltane` |

## 快速开始

```bash
git clone https://github.com/fange173/voltane.git && cd voltane
# 修改 docker-compose.yml 里的 SECRET_KEY
docker compose up -d
```

浏览器打开 `http://<NAS-IP>:8086`，默认账号 `admin / admin123`，登录后请在「我的」里修改密码。

在外面加油、充电时需要能访问到 NAS：建议配合 Tailscale / ZeroTier，或 HTTPS 反向代理（反代后把 `COOKIE_SECURE`、`TRUST_PROXY_HEADERS` 设为 `true`）。PWA 和定位只在 HTTPS 下可用；连不上时记录会先存在手机上，联网后自动同步。

## 本地开发

```bash
python3.11 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
DATABASE_URL=sqlite:///./data/dev.db .venv/bin/python scripts/seed_demo.py   # 可选：生成演示数据
DATABASE_URL=sqlite:///./data/dev.db .venv/bin/uvicorn app.main:app --reload --port 8136
.venv/bin/pytest -q && node scripts/smoke-modules.mjs
```

## 备份

- 每天凌晨 3 点后自动备份到 `data/backups/`，保留 `BACKUP_KEEP` 份。
- 管理后台可以下载当前备份、从备份恢复（恢复前会自动再备份一次）。

## 许可

MIT
