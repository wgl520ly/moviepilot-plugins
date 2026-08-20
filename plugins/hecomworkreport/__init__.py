# -*- coding: utf-8 -*-
"""
红圈工作报告查询 (HecomWorkReport) - 红圈(cloud.hecom.cn)当天工作报告提交情况统计

流程：Playwright 无头浏览器登录 -> 从 localStorage 取 auth(accessToken/uid/empCode/entCode)
      -> 直接调用 API POST https://cloud1.hecom.cn/universe/paas/app/workReport/list/list
      -> 过滤 date==今天(UTC+8 零点时间戳) 的日报 -> 与团队成员名单比对
      -> 按指定格式推送站内通知（已提交X人+未提交Y人）
特点：页面菜单是 antd 图标栏+浮层，直接用 API 更稳更快（无需模拟菜单点击）；浏览器仅用于登录拿 token
"""
import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pytz
import requests
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.core.event import eventmanager, Event
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import NotificationType
from app.schemas.types import EventType

try:
    from playwright.sync_api import sync_playwright
except Exception:
    sync_playwright = None


class HecomWorkReport(_PluginBase):
    """红圈工作报告查询"""

    plugin_name = "红圈工作报告查询"
    plugin_desc = "每天21:30查询红圈（cloud.hecom.cn）当天工作报告日提交情况，统计已提交/未提交人员并推送通知"
    plugin_version = "0.1.1"
    plugin_author = "wgl520ly"
    author_url = "https://github.com/wgl520ly"
    repo_url = "https://github.com/wgl520ly/moviepilot-plugins"

    plugin_order = 96
    auth_level = 1
    plugin_config_prefix = "hecomworkreport_"

    _enabled: bool = False
    _onlyonce: bool = False
    _cron: str = "30 21 * * *"
    _notify: bool = True
    _phone: str = ""
    _password: str = ""
    _team: str = ""
    _reminder: str = ""
    _scheduler: Optional[BackgroundScheduler] = None

    SETTINGS_API_URL = "https://cloud1.hecom.cn/universe/paas/app/workReport/list/list"
    LOGIN_URL = "https://cloud.hecom.cn/login"
    DEFAULT_TEAM = ""  # 发布版不内置名单，请在插件设置中填写
    DEFAULT_REMINDER = "亲爱的小伙伴们，今天的工作报告还没有提交哦～工作再忙也别忘了花几分钟提交一下，谢谢大家的配合，辛苦啦！☕"

    def init_plugin(self, config: dict = None):
        self.stop_service()
        if config:
            self._enabled = bool(config.get("enabled", False))
            self._onlyonce = bool(config.get("onlyonce", False))
            self._cron = str(config.get("cron") or "30 21 * * *")
            self._notify = bool(config.get("notify", True))
            self._phone = str(config.get("phone") or "").strip()
            self._password = str(config.get("password") or "")
            self._team = str(config.get("team") or self.DEFAULT_TEAM).strip()
            self._reminder = str(config.get("reminder") or self.DEFAULT_REMINDER).strip()

        if not self._enabled:
            logger.info("[HecomWorkReport] 插件未启用")
            return

        if not self._phone or not self._password:
            logger.warn("[HecomWorkReport] 未配置手机号/密码，跳过")
            return

        logger.info(f"[HecomWorkReport] 插件已启用，每日 {self._cron} 执行")

        if self._onlyonce:
            self._onlyonce = False
            self.__update_config()
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            self._scheduler.add_job(self.run_all, "date",
                                    run_date=datetime.now(pytz.timezone(settings.TZ)))
            self._scheduler.start()
            logger.info("[HecomWorkReport] 已安排立即执行")

    def get_state(self) -> bool:
        return self._enabled

    def stop_service(self):
        try:
            if self._scheduler:
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
        except Exception as e:
            logger.error(f"[HecomWorkReport] 停止服务失败: {e}")

    def __update_config(self):
        self.update_config({
            "enabled": self._enabled,
            "onlyonce": self._onlyonce,
            "cron": self._cron,
            "notify": self._notify,
            "phone": self._phone,
            "password": self._password,
            "team": self._team,
            "reminder": self._reminder,
        })

    # ============================================================
    # 配置表单
    # ============================================================
    def get_form(self) -> Tuple[Optional[List[dict]], Dict[str, Any]]:
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [{
                                    "component": "VSwitch",
                                    "props": {"model": "enabled", "label": "启用插件"}
                                }]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [{
                                    "component": "VSwitch",
                                    "props": {"model": "onlyonce", "label": "保存后立即执行一次"}
                                }]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [{
                                    "component": "VTextField",
                                    "props": {"model": "cron", "label": "执行周期 (cron)", "placeholder": "30 21 * * *"}
                                }]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [{
                                    "component": "VSwitch",
                                    "props": {"model": "notify", "label": "发送站内通知"}
                                }]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [{
                                    "component": "VTextField",
                                    "props": {"model": "phone", "label": "红圈登录手机号", "placeholder": "请输入红圈登录手机号"}
                                }]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [{
                                    "component": "VTextField",
                                    "props": {"model": "password", "label": "红圈登录密码", "placeholder": "登录密码（明文保存于本机配置）"}
                                }]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 12},
                                "content": [{
                                    "component": "VTextarea",
                                    "props": {
                                        "model": "team",
                                        "label": "团队成员名单（用顿号/逗号/换行分隔）",
                                        "rows": 4,
                                        "placeholder": "姓名1、姓名2、姓名3…（顿号分隔）"
                                    }
                                }]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 12},
                                "content": [{
                                    "component": "VTextarea",
                                    "props": {
                                        "model": "reminder",
                                        "label": "温馨提醒文案（有未提交时随通知发出，可直接转发）",
                                        "rows": 3,
                                        "placeholder": "亲爱的小伙伴们，今天的工作报告还没有提交哦～工作再忙也别忘了花几分钟提交一下，谢谢大家的配合，辛苦啦！☕"
                                    }
                                }]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "onlyonce": False,
            "cron": "30 21 * * *",
            "notify": True,
            "phone": "",
            "password": "",
            "team": self.DEFAULT_TEAM,
            "reminder": self.DEFAULT_REMINDER,
        }

    # ============================================================
    # 详情页
    # ============================================================
    def get_page(self) -> Optional[List[dict]]:
        records = self.get_data("records") or []
        if not records:
            return [{
                "component": "VAlert",
                "props": {"type": "info", "variant": "tonal",
                          "text": "暂无查询记录。请先在插件设置里填入红圈手机号/密码并启用，或勾选「保存后立即执行一次」触发一轮查询。", "class": "mb-2"}
            }]

        cur = records[0]
        sub = cur.get("submitted") or []
        mis = cur.get("missing") or []
        cards = [
            {
                "component": "VCard",
                "props": {"variant": "elevated", "elevation": 2, "rounded": "lg", "class": "mb-4"},
                "content": [
                    {"component": "VCardTitle", "props": {"class": "text-h6 font-weight-bold"},
                     "text": f"📋 红圈工作报告 · {cur.get('day') or '今天'}"},
                    {
                        "component": "VCardText",
                        "content": [
                            {
                                "component": "VRow",
                                "content": [
                                    {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [
                                        {"component": "VChip", "props": {"size": "large", "variant": "tonal", "color": "success"},
                                         "text": f"✅ 已提交 {len(sub)} 人"}]},
                                    {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [
                                        {"component": "VChip", "props": {"size": "large", "variant": "tonal", "color": "error"},
                                         "text": f"❌ 未提交 {len(mis)} 人"}]},
                                    {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [
                                        {"component": "VChip", "props": {"size": "large", "variant": "tonal", "color": "primary"},
                                         "text": f"🕒 查询时间 {str(cur.get('date', ''))[5:16]}"}]},
                                ]
                            }
                        ]
                    }
                ]
            }
        ]

        # 今日总结卡片（统计 + 温馨提醒）
        sm = cur.get("summary") or {}
        sm_lines = []
        if sm.get("total"):
            cnt = sm.get("count", 0)
            sm_lines.append(f"· 已提交 {cnt} 人 / 共 {sm.get('total')} 人")
        sm_lines.append(f"· 未提交：{'、'.join(mis) if mis else '无，全部完成 🎉'}")
        for n in mis:
            last = (sm.get("last_submit") or {}).get(n)
            if last:
                sm_lines.append(f"· {n} 上次提交：{last}")
        if mis and (self._reminder or self.DEFAULT_REMINDER):
            sm_lines.append("💌 " + (self._reminder or self.DEFAULT_REMINDER))
        if not sm_lines:
            sm_lines.append("· 暂无汇总数据（再次执行后可生成）")
        cards.append({
            "component": "VCard",
            "props": {"variant": "tonal", "elevation": 0, "rounded": "lg", "class": "mb-4"},
            "content": [
                {"component": "VCardTitle", "props": {"class": "text-h6 font-weight-bold"}, "text": "📊 今日总结"},
                {"component": "VCardText", "props": {"style": "white-space:pre-line"},
                 "text": "\n".join(sm_lines)},
            ]
        })

        sub_lines = "\n".join([f"　✅ {x.get('name')}（{x.get('time')}）" for x in sub]) or "　（无）"
        mis_lines = "\n".join([f"　❌ {x}" for x in mis]) or "　（全部已提交 🎉）"
        cards.append({
            "component": "VCard",
            "props": {"variant": "elevated", "elevation": 2, "rounded": "lg", "class": "mb-4"},
            "content": [
                {"component": "VCardTitle", "props": {"class": "text-h6 font-weight-bold"}, "text": "今日明细"},
                {"component": "VCardText", "props": {}, "content": [
                    {"component": "VRow", "content": [
                        {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [
                            {"component": "div", "props": {"style": "white-space:pre-line"}, "text": sub_lines}]},
                        {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [
                            {"component": "div", "props": {"style": "white-space:pre-line"}, "text": mis_lines}]},
                    ]}
                ]},
            ]
        })

        rows = []
        for h in records[:20]:
            rows.append({
                "component": "tr",
                "content": [
                    {"component": "td", "props": {"class": "text-caption"}, "text": h.get("day", "")},
                    {"component": "td", "content": [
                        {"component": "VChip", "props": {"size": "small", "variant": "tonal", "color": "success"},
                         "text": f"✅ {len(h.get('submitted') or [])}"}]},
                    {"component": "td", "content": [
                        {"component": "VChip", "props": {"size": "small", "variant": "tonal", "color": "error"},
                         "text": f"❌ {len(h.get('missing') or [])}"}]},
                    {"component": "td", "props": {"class": "text-caption"},
                     "text": (", ".join(h.get("missing") or []))[:60]},
                ]
            })
        cards.append({
            "component": "VCard",
            "props": {"variant": "elevated", "elevation": 2, "rounded": "lg", "class": "mb-4"},
            "content": [
                {"component": "VCardTitle", "props": {"class": "text-h6 font-weight-bold"},
                 "text": "📜 历史查询（近 20 条）"},
                {
                    "component": "VCardText",
                    "content": [
                        {
                            "component": "VTable",
                            "props": {"hover": True, "density": "comfortable"},
                            "content": [
                                {
                                    "component": "thead",
                                    "content": [
                                        {
                                            "component": "tr",
                                            "content": [
                                                {"component": "th", "props": {"class": "text-body-2"}, "text": "日期"},
                                                {"component": "th", "props": {"class": "text-body-2"}, "text": "已提交"},
                                                {"component": "th", "props": {"class": "text-body-2"}, "text": "未提交"},
                                                {"component": "th", "props": {"class": "text-body-2"}, "text": "未提交名单"},
                                            ]
                                        }
                                    ]
                                },
                                {"component": "tbody", "content": rows}
                            ]
                        }
                    ]
                }
            ]
        })
        return cards

    # ============================================================
    # 定时任务 / 命令 / API
    # ============================================================
    def get_service(self) -> List[Dict[str, Any]]:
        if self._enabled and self._cron:
            return [{
                "id": "HecomWorkReportTimer",
                "name": "红圈工作报告每日查询",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.run_all,
                "kwargs": {},
            }]
        return []

    def get_command(self) -> List[Dict[str, Any]]:
        return [{
            "cmd": "/hecomwr",
            "event": EventType.PluginAction,
            "desc": "红圈工作报告立即查询",
            "category": "工具",
            "data": {"action": "check"}
        }]

    @eventmanager.register(EventType.PluginAction)
    def handle_command(self, event: Event = None):
        if not event:
            return
        if (event.event_data or {}).get("action") == "check":
            self.run_all()

    def get_api(self) -> List[Dict[str, Any]]:
        return [
            {
                "path": "/HecomWorkReport/run",
                "endpoint": self.api_run,
                "methods": ["POST"],
                "summary": "立即查询一次",
                "description": "登录红圈并查询当天工作报告提交情况",
            },
            {
                "path": "/HecomWorkReport/records",
                "endpoint": self.api_records,
                "methods": ["GET"],
                "summary": "获取查询记录",
                "description": "返回最近 50 条查询记录",
            },
        ]

    def api_run(self) -> dict:
        self.run_all()
        return {"success": True, "message": "查询任务已触发"}

    def api_records(self) -> list:
        return (self.get_data("records") or [])[:50]

    # ============================================================
    # 核心逻辑
    # ============================================================
    def run_all(self):
        if not self._phone or not self._password:
            logger.warn("[HecomWorkReport] 未配置手机号/密码")
            return

        team = self.__parse_team()
        if not team:
            logger.warn("[HecomWorkReport] 团队成员名单为空")
            return

        tz = pytz.timezone(settings.TZ if settings.TZ else "Asia/Shanghai")
        today = datetime.now(tz).strftime("%Y-%m-%d")
        today_ms = int(datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()) * 1000

        logger.info(f"[HecomWorkReport] 开始查询 {today} 红圈工作报告（{len(team)} 人名单）")

        try:
            data = self.__fetch_report_data(today_ms, team)
        except Exception as e:
            msg = f"查询失败: {e}"
            logger.error(f"[HecomWorkReport] {msg}")
            self.__save_record(today, [], team, failed=msg)
            if self._notify:
                self.post_message(
                    mtype=NotificationType.SiteMessage,
                    title=f"🔴 红圈工作报告查询失败 · {today}",
                    text=f"❌ {msg}",
                )
            return

        submitted = data["today"]
        missing = [n for n in team if n not in submitted]
        ordered_sub = [(n, submitted[n]) for n in team if n in submitted]

        total = len(team)
        done = len(ordered_sub)

        summary = {
            "count": done,
            "total": total,
            "last_submit": {n: data["last"].get(n) or "" for n in missing},
        }

        # ---- 推送正文：统计 + 名单 + 温馨提醒（可直接转发） ----
        lines = [f"📋 红圈工作报告 · {today}", ""]
        lines.append(f"✅ 已提交 {done} 人 · ❌ 未提交 {len(missing)} 人")
        lines.append("")
        lines.append(f"▎已提交（{done}）")
        for i in range(0, len(ordered_sub), 2):
            row = [f"· {n}（{t}）" for n, t in ordered_sub[i:i + 2]]
            lines.append(" ".join(row))
        if missing:
            lines.append("")
            lines.append(f"▎未提交（{len(missing)}）")
            for name in missing:
                last = data["last"].get(name)
                lines.append(f"⚠️ {name}" + (f"（上次提交：{last}）" if last else "（暂无提交记录）"))
            lines.append("")
            lines.append("💌 温馨提醒")
            lines.append(self._reminder or self.DEFAULT_REMINDER)
        else:
            lines.append("")
            lines.append("🎉 今日全员提交完成，大家辛苦啦！")
        text = "\n".join(lines)

        self.__save_record(today, ordered_sub, missing, summary=summary)

        if self._notify:
            if missing:
                title = f"📋 红圈工作报告 · {today}"
            elif done:
                title = f"🎉 红圈工作报告 · {today} · 全员提交"
            else:
                title = f"🔴 红圈工作报告 · {today}"
            self.post_message(
                mtype=NotificationType.SiteMessage,
                title=title,
                text=text,
            )
        logger.info(f"[HecomWorkReport] 查询完成：已提交 {done}，未提交 {len(missing)}（{rate}）")

    def __fetch_report_data(self, today_ms: int, team: List[str]) -> Dict[str, Any]:
        """抓取最近工作报告（拉 2 页），返回：
        today: {姓名: "HH:MM"}        当日已提交
        last:  {姓名: "MM-DD HH:MM"}  每人最近一次历史提交（不含今天）
        first / latest: 今日最早/最晚提交时间
        """
        if not sync_playwright:
            raise RuntimeError("容器内未安装 playwright")
        import json as _json

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True,
                                        args=["--no-sandbox", "--disable-dev-shm-usage"])
            try:
                context = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
                page = context.new_page()
                # ---- 登录 ----
                already = False
                try:
                    already = page.evaluate(
                        "() => { try { const a = localStorage.getItem('auth'); return !!(a && a.indexOf('accessToken') >= 0 && window.location.host.indexOf('cloud.hecom.cn') >= 0); } catch(e) { return false; } }")
                except Exception:
                    already = False

                if not already:
                    page.goto(self.LOGIN_URL, timeout=45000, wait_until="domcontentloaded")
                    page.wait_for_timeout(3000)
                    page.fill("#account", self._phone)
                    page.fill("#password", self._password)
                    page.wait_for_timeout(300)
                    page.evaluate("""() => {
                        const b = [...document.querySelectorAll('button')].find(x => {
                            const t = (x.innerText || '').replace(/\\s+/g, '').toLowerCase();
                            return t === 'login' || t === '登录';
                        });
                        if (b) b.click();
                    }""")
                    page.wait_for_function(
                        """() => { try { const a = localStorage.getItem('auth'); return !!(a && a.indexOf('accessToken') >= 0); } catch(e) { return false; } }""",
                        timeout=45000)
                    page.wait_for_timeout(2000)

                # ---- 调用工作报告列表 API（拉 2 页，补算“上次提交”）----
                resp_text = page.evaluate("""async (url) => {
                    const auth = JSON.parse(localStorage.getItem('auth'));
                    const headers = {
                        "Content-Type": "application/json",
                        "version": "0.0.4",
                        "accessToken": auth.accessToken,
                        "entCode": auth.entCode,
                        "uid": auth.uid,
                        "empCode": auth.empCode,
                        "clientTag": "web",
                        "app": "workReport",
                        "act": "list"
                    };
                    const baseBody = {
                        "metaName": "workReport",
                        "scope": 1,
                        "filter": {"conditions": [], "conj": "advance", "expr": ""},
                        "page": {"pageNo": 1, "pageSize": 50},
                        "sorts": [{"field": "updatedOn", "orderType": 0}]
                    };
                    const out = [];
                    for (const pageNo of [1, 2]) {
                        const body = Object.assign({}, baseBody);
                        body.page = {"pageNo": pageNo, "pageSize": 50};
                        try {
                            const r = await fetch(url, {method: "POST", headers: headers, body: JSON.stringify(body)});
                            const t = await r.text();
                            const j = JSON.parse(t);
                            out.push(...((j.data && j.data.records) || []));
                        } catch (e) {}
                    }
                    return JSON.stringify(out);
                }""", self.SETTINGS_API_URL)

                try:
                    records = _json.loads(resp_text)
                except Exception:
                    raise RuntimeError(f"列表接口响应解析失败: {resp_text[:200]}")
                if not records:
                    raise RuntimeError("列表接口未返回记录")
            finally:
                try:
                    browser.close()
                except Exception:
                    pass

        tz_cn = pytz.timezone("Asia/Shanghai")
        today: Dict[str, str] = {}
        today_ts: Dict[str, int] = {}
        last_seen: Dict[str, tuple] = {}  # name -> (最近提交日期ms, 该次提交时间ms)
        for rec in records:
            try:
                rec_date = int(rec.get("date") or 0)
                owner = (rec.get("owner") or {}).get("name")
                if not owner or owner not in team:
                    continue
                sub_ms = int(rec.get("submittedOn") or 0)
                if rec_date == today_ms:
                    hms = datetime.fromtimestamp(sub_ms / 1000, tz=tz_cn).strftime("%H:%M")
                    if owner not in today or sub_ms > today_ts.get(owner, 0):
                        today[owner] = hms
                        today_ts[owner] = sub_ms
                else:
                    # 取最近一次历史提交（不含今天）
                    prev = last_seen.get(owner)
                    if prev is None or rec_date > prev[0] or (rec_date == prev[0] and sub_ms > prev[1]):
                        last_seen[owner] = (rec_date, sub_ms)
            except Exception:
                continue

        last: Dict[str, str] = {}
        for name, (rec_date_ms, sub_ms) in last_seen.items():
            dt = datetime.fromtimestamp(sub_ms / 1000, tz=tz_cn)
            last[name] = dt.strftime("%m-%d %H:%M")

        first_hm = latest_hm = None
        if today_ts:
            times = sorted(today_ts.values())
            first_hm = datetime.fromtimestamp(times[0] / 1000, tz=tz_cn).strftime("%H:%M")
            latest_hm = datetime.fromtimestamp(times[-1] / 1000, tz=tz_cn).strftime("%H:%M")

        return {"today": today, "last": last, "first": first_hm, "latest": latest_hm}

    # ============================================================
    # 工具方法
    # ============================================================
    def __parse_team(self) -> List[str]:
        names = []
        for sep in ["\n", "、", ",", "，", ";", "；", " "]:
            if sep in self._team:
                for part in self._team.split(sep):
                    part = part.strip()
                    if part and part not in names:
                        names.append(part)
                break
        else:
            t = self._team.strip()
            if t:
                names = [t]
        return names

    def __save_record(self, day: str, submitted: List[tuple], missing: List[str], failed: str = "", summary: dict = None):
        records = self.get_data("records") or []
        records.insert(0, {
            "day": day,
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "submitted": [{"name": n, "time": t} for n, t in submitted],
            "missing": missing,
            "failed": failed,
            "summary": summary or {},
        })
        records = records[:200]
        self.save_data("records", records)
        self.save_data("last_run", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
