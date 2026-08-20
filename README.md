# MoviePilot 自研插件集（fnos）

MoviePilot V3 自研插件仓库，内含 3 个插件，可通过 GitHub 仓库直接安装。

| 插件 | 插件 ID | 功能 |
| --- | --- | --- |
| 📊 DGB公益站签到 | `NewApiCheckIn` | DGB 公益站（freeapi.dgbmc.top）多账号每日签到，记录签到获得的额度、当前余额 |
| 🐱 MMKG公共节点站签到 | `MmkgCheckIn` | MMKG 公共节点站（api.mmkg.cloud）多账号每日签到，记录签到获得的咪币额度、当前余额 |
| 📋 红圈工作报告查询 | `HecomWorkReport` | 每天 21:30 查询红圈（cloud.hecom.cn）当天工作报告日提交情况，统计已提交/未提交人员，并推送带温馨提醒的通知 |

## 安装方法

### 方式一：插件市场添加仓库（推荐）

1. 打开 MoviePilot → **插件市场** → 右上角 **插件仓库**
2. 添加仓库：`https://github.com/<你的用户名>/moviepilot-plugins`
3. 在插件市场中搜索插件名并安装

> 仓库结构完全符合 MoviePilot V3 插件市场规范（根目录 `package.json` + `plugins/<插件id小写>/__init__.py`），Market 直接通过 GitHub Contents API 读取安装。

### 方式二：手动放入 devplugins

```bash
cp -r plugins/<插件目录> /config/devplugins/plugins/
# 并把对应条目合并进 /config/devplugins/package.json
```

然后访问：
`http://<moviepilot>:<port>/api/v1/plugin/install/<插件ID>?repo_url=local://<插件ID>?path=/config/devplugins&force=true`

## 配置说明

- **账号密码**：各插件登录账号、密码均在插件设置中填写（明文存储于 MoviePilot 的 user.db，请自行评估安全风险）。
- **定时任务**：默认 cron 见各插件设置，如红圈默认为 `30 21 * * *`（每天 21:30）。
- **HecomWorkReport 特殊依赖**：依赖容器内 Playwright 浏览器。首次使用前需在 MoviePilot 容器内执行：
  ```bash
  docker exec <moviepilot容器名> /opt/venv/bin/python -m playwright install chromium-headless-shell
  ```
- **HecomWorkReport 团队名单**：团队成员名单、温馨提醒文案均可在插件设置中修改；发布版不内置任何真实名单。

## 工作原理解析

- **DGB / MMKG 签到**：通过站点 API 登录（如 `/api/user/login`）→ 签到（`/api/user/checkin`）→ 读取余额（`/api/user/self` 或对应站点接口）。
- **红圈工作报告**：Playwright 无头浏览器登录红圈 SPA（cloud.hecom.cn，登录态存 localStorage.auth）→ 直接调用
  `POST https://cloud1.hecom.cn/universe/paas/app/workReport/list/list`（请求头携带 accessToken/uid/empCode/entCode/clientTag=web）
  → 按当天日期筛选日报 → 与团队名单比对 → 生成统计 + 温馨提醒推送。

## 免责声明

本仓库插件仅用于个人自动化场景。使用第三方云服务站点时，请自行遵守其服务条款；本仓库不承担任何因使用插件产生的后果。
