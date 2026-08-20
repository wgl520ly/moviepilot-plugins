# -*- coding: utf-8 -*-
"""
DGB公益站签到 (NewApiCheckIn) - New-API 站点多账号每日签到插件

适用站点：https://freeapi.dgbmc.top （DGB 公益站，New API / One API 面板，base_url 可配置）
流程：登录 -> 拉取站点配额参数(/api/status) -> 查询余额(/api/user/self) -> 签到(/api/user/checkin) -> 再查余额
记录字段：时间 / 账号 / 状态(success|already|failed) / 本次获得(quota 与 ≈元) / 签到后余额(quota 与 ≈元) / 说明
余额说明：站点配额参数 quota_per_unit=500000，显示模式下余额 = quota / 500000（该站≈元）；原始 quota 值一并保存。
"""
import re
from datetime import datetime, timedelta
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


class NewApiCheckIn(_PluginBase):
    """DGB 公益站（New-API 站点）多账号签到"""

    # ===== 插件元信息 =====
    plugin_name = "DGB公益站签到"
    plugin_desc = "DGB 公益站（freeapi.dgbmc.top）多账号每日签到，记录签到获得的额度、当前余额与状态"
    plugin_version = "0.2.4"
    plugin_author = "wgl520ly"
    author_url = "https://github.com/wgl520ly"
    repo_url = "https://github.com/wgl520ly/moviepilot-plugins"

    plugin_order = 98
    auth_level = 1
    plugin_config_prefix = "newapicheckin_"

    # ===== 配置状态 =====
    _enabled: bool = False
    _onlyonce: bool = False
    _cron: str = "30 8 * * *"
    _notify: bool = True
    _notify_failed_only: bool = False
    _base_url: str = "https://freeapi.dgbmc.top"
    _accounts: str = ""
    _scheduler: Optional[BackgroundScheduler] = None

    def init_plugin(self, config: dict = None):
        self.stop_service()
        if config:
            self._enabled = bool(config.get("enabled", False))
            self._onlyonce = bool(config.get("onlyonce", False))
            self._cron = str(config.get("cron") or "30 8 * * *")
            self._notify = bool(config.get("notify", True))
            self._notify_failed_only = bool(config.get("notify_failed_only", False))
            self._base_url = str(config.get("base_url") or "https://freeapi.dgbmc.top").strip().rstrip("/")
            self._accounts = str(config.get("accounts") or "")

        if not self._enabled:
            logger.info("[NewApiCheckIn] 插件未启用")
            return

        logger.info(f"[NewApiCheckIn] 插件已启用，每日 {self._cron} 执行，站点 {self._base_url}")

        if self._onlyonce:
            self._onlyonce = False
            self.__update_config()
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            self._scheduler.add_job(
                self.run_all,
                "date",
                run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
            )
            self._scheduler.start()
            logger.info("[NewApiCheckIn] 3 秒后立即执行一次")

    def get_state(self) -> bool:
        return self._enabled

    def stop_service(self):
        try:
            if self._scheduler:
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
        except Exception as e:
            logger.error(f"[NewApiCheckIn] 停止服务失败: {e}")

    def __update_config(self):
        self.update_config({
            "enabled": self._enabled,
            "onlyonce": self._onlyonce,
            "cron": self._cron,
            "notify": self._notify,
            "notify_failed_only": self._notify_failed_only,
            "base_url": self._base_url,
            "accounts": self._accounts,
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
                                    "props": {"model": "cron", "label": "执行周期 (cron)", "placeholder": "30 8 * * *"}
                                }]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [{
                                    "component": "VTextField",
                                    "props": {"model": "base_url", "label": "站点地址", "placeholder": "https://freeapi.dgbmc.top"}
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
                                    "component": "VSwitch",
                                    "props": {"model": "notify_failed_only", "label": "仅失败时通知"}
                                }]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 12},
                                "content": [{
                                    "component": "VTextarea",
                                    "props": {
                                        "model": "accounts",
                                        "label": "账号列表（每行一个：用户名|密码）",
                                        "rows": 6,
                                        "placeholder": "wgl520ly|你的密码"
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
            "cron": "30 8 * * *",
            "notify": True,
            "notify_failed_only": False,
            "base_url": "https://freeapi.dgbmc.top",
            "accounts": ""
        }

    # ============================================================
    # 详情页（参考 GlaDOS 签到插件：摘要卡 + 历史表格）
    # ============================================================
    def get_page(self) -> Optional[List[dict]]:
        records = self.get_data("records") or []
        meta = self.get_data("site_meta") or {}
        qpu = int(meta.get("quota_per_unit") or 500000)

        if not records:
            return [{
                "component": "VAlert",
                "props": {"type": "info", "variant": "tonal",
                          "text": "暂无签到记录，请先在插件设置里配置账号并启用，或勾选「保存后立即执行一次」运行一轮", "class": "mb-2"}
            }]

        today = datetime.now().strftime("%Y-%m-%d")
        todays = [r for r in records if r.get("day") == today]

        ok = sum(1 for r in todays if r.get("status") == "success")
        already = sum(1 for r in todays if r.get("status") == "already")
        failed = sum(1 for r in todays if r.get("status") == "failed")
        today_added = sum(int(r.get("added") or 0) for r in todays)
        cards = [
            {
                "component": "VCard",
                "props": {"variant": "elevated", "elevation": 2, "rounded": "lg", "class": "mb-4"},
                "content": [
                    {"component": "VCardTitle", "props": {"class": "text-h6 font-weight-bold"},
                     "text": "📊 DGB公益站 · 今日签到概览"},
                    {
                        "component": "VCardText",
                        "content": [
                            {
                                "component": "VRow",
                                "content": [
                                    {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [
                                        {"component": "VChip", "props": {"size": "large", "variant": "tonal", "color": "success"},
                                         "text": f"✅ 成功 {ok}"}]},
                                    {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [
                                        {"component": "VChip", "props": {"size": "large", "variant": "tonal", "color": "grey"},
                                         "text": f"🔁 已签到 {already}"}]},
                                    {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [
                                        {"component": "VChip", "props": {"size": "large", "variant": "tonal", "color": "error"},
                                         "text": f"❌ 失败 {failed}"}]},
                                    {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [
                                        {"component": "VChip", "props": {"size": "large", "variant": "tonal", "color": "amber-darken-2"},
                                         "text": f"📈 今日获得 {self.__fmt_money(today_added, qpu, meta)}"}]},
                                ]
                            }
                        ]
                    }
                ]
            }
        ]

        latest_by_account: Dict[str, dict] = {}
        for r in records:
            acc = r.get("account", "")
            if acc not in latest_by_account:
                latest_by_account[acc] = r
        account_rows = []
        for acc, r in latest_by_account.items():
            status = r.get("status", "")
            color = {"success": "success", "already": "grey", "failed": "error"}.get(status, "grey")
            status_text = {"success": "✅ 成功", "already": "🔁 已签到", "failed": "❌ 失败"}.get(status, status)
            account_rows.append(
                {
                    "component": "VRow",
                    "props": {"class": "mt-2"},
                    "content": [
                        {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [
                            {"component": "VChip", "props": {"size": "default", "variant": "elevated",
                                                             "color": "primary"}, "text": f"👤 {acc}"}]},
                        {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [
                            {"component": "VChip", "props": {"size": "default", "variant": "tonal",
                                                             "color": color}, "text": status_text}]},
                        {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [
                            {"component": "VChip", "props": {"size": "default", "variant": "tonal",
                                                             "color": "amber-darken-2"},
                             "text": f"💰 余额 {self.__fmt_money(r.get('balance'), qpu, meta)}"}]},
                        {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [
                            {"component": "VChip", "props": {"size": "default", "variant": "tonal",
                                                             "color": "teal"},
                             "text": f"🕒 {str(r.get('date', ''))[5:16]}"}]},
                    ]
                }
            )
        cards.append({
            "component": "VCard",
            "props": {"variant": "elevated", "elevation": 2, "rounded": "lg", "class": "mb-4"},
            "content": [
                {"component": "VCardTitle", "props": {"class": "text-h6 font-weight-bold"},
                 "text": "👥 账号最近签到"},
                {"component": "VCardText", "content": account_rows}
            ]
        })

        rows = []
        for h in records[:50]:
            delta = int(h.get("added") or 0)
            delta_color = "success" if delta > 0 else ("grey" if delta == 0 else "error")
            delta_emoji = "📈" if delta > 0 else "➖"
            status = h.get("status", "")
            status_text = {"success": "✅ 成功", "already": "🔁 已签到", "failed": "❌ 失败"}.get(status, status)
            status_color = {"success": "success", "already": "grey", "failed": "error"}.get(status, "grey")
            rows.append({
                "component": "tr",
                "content": [
                    {"component": "td", "props": {"class": "text-caption"}, "text": h.get("date", "")},
                    {"component": "td", "props": {"class": "text-caption"}, "text": h.get("account", "")},
                    {"component": "td", "content": [
                        {"component": "VChip", "props": {"size": "small", "variant": "outlined", "color": status_color},
                         "text": status_text}]},
                    {"component": "td", "content": [
                        {"component": "VChip", "props": {"size": "small", "variant": "outlined", "color": delta_color},
                         "text": f"{delta_emoji} {self.__fmt_money(delta, qpu, meta)}" if delta > 0 else f"{delta_emoji} -"}]},
                    {"component": "td", "content": [
                        {"component": "VChip", "props": {"size": "small", "variant": "outlined", "color": "teal"},
                         "text": self.__fmt_money(h.get("balance"), qpu, meta)}]},
                    {"component": "td", "props": {"class": "text-caption"}, "text": h.get("message", "")},
                ]
            })
        cards.append({
            "component": "VCard",
            "props": {"variant": "elevated", "elevation": 2, "rounded": "lg", "class": "mb-4"},
            "content": [
                {"component": "VCardTitle", "props": {"class": "text-h6 font-weight-bold"},
                 "text": "📋 签到历史（近 50 条，共保留 200 条）"},
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
                                                {"component": "th", "props": {"class": "text-body-2"}, "text": "时间"},
                                                {"component": "th", "props": {"class": "text-body-2"}, "text": "账号"},
                                                {"component": "th", "props": {"class": "text-body-2"}, "text": "状态"},
                                                {"component": "th", "props": {"class": "text-body-2"}, "text": "本次获得"},
                                                {"component": "th", "props": {"class": "text-body-2"}, "text": "签到后余额"},
                                                {"component": "th", "props": {"class": "text-body-2"}, "text": "说明"},
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
                "id": "NewApiCheckInTimer",
                "name": "DGB公益站每日签到",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.run_all,
                "kwargs": {},
            }]
        return []

    def get_command(self) -> List[Dict[str, Any]]:
        return [{
            "cmd": "/checkin",
            "event": EventType.PluginAction,
            "desc": "DGB公益站立即签到",
            "category": "工具",
            "data": {"action": "checkin"}
        }]

    @eventmanager.register(EventType.PluginAction)
    def handle_command(self, event: Event = None):
        if not event:
            return
        if (event.event_data or {}).get("action") == "checkin":
            self.run_all()

    def get_api(self) -> List[Dict[str, Any]]:
        return [
            {
                "path": "/NewApiCheckIn/run",
                "endpoint": self.api_run,
                "methods": ["POST"],
                "summary": "立即执行签到",
                "description": "手动触发一次全部账号签到",
            },
            {
                "path": "/NewApiCheckIn/records",
                "endpoint": self.api_records,
                "methods": ["GET"],
                "summary": "获取签到记录",
                "description": "返回最近 50 条签到记录",
            },
        ]

    def api_run(self) -> dict:
        self.run_all()
        return {"success": True, "message": "签到任务已触发"}

    def api_records(self) -> list:
        return (self.get_data("records") or [])[:50]

    # ============================================================
    # 核心逻辑
    # ============================================================
    def run_all(self):
        accounts = self.__parse_accounts()
        if not accounts:
            logger.warn("[NewApiCheckIn] 未配置账号，跳过")
            return

        records = self.get_data("records") or []
        today = datetime.now().strftime("%Y-%m-%d")
        acc_names = {a["username"] for a in accounts}
        records = [r for r in records if not (r.get("day") == today and r.get("account") in acc_names)]

        results = []
        meta = {}
        for acc in accounts:
            rec, m = self.__checkin_one(acc)
            if m:
                meta = m
            rec["account"] = acc["username"]
            results.append(rec)
            records.insert(0, {
                "day": today,
                "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "account": acc["username"],
                "status": rec["status"],
                "added": rec.get("added_quota", 0),
                "balance": rec.get("quota_after", 0),
                "used": rec.get("used_quota", 0),
                "message": rec.get("message", ""),
            })
        records = records[:200]
        self.save_data("records", records)
        if meta:
            self.save_data("site_meta", meta)
        self.save_data("last_run", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        ok = sum(1 for r in results if r["status"] == "success")
        already = sum(1 for r in results if r["status"] == "already")
        failed = sum(1 for r in results if r["status"] == "failed")
        logger.info(f"[NewApiCheckIn] 签到完成：成功 {ok}，已签到 {already}，失败 {failed}")

        if self._notify:
            failed_list = [r for r in results if r["status"] == "failed"]
            if not self._notify_failed_only or failed_list:
                self.__send_notify(results)

    def __checkin_one(self, acc: dict) -> Tuple[dict, dict]:
        username = acc["username"]
        password = acc["password"]
        base = self._base_url
        meta = {}

        def req(method: str, path: str, token: str = None, json_body: dict = None):
            headers = {"Content-Type": "application/json", "User-Agent": "MoviePilot/DGBCheckIn"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            try:
                resp = requests.request(method, base + path, headers=headers, json=json_body, timeout=25)
                try:
                    return resp.status_code, resp.json()
                except Exception:
                    return resp.status_code, {"success": False, "message": f"HTTP {resp.status_code}: 响应不是 JSON"}
            except Exception as e:
                return None, {"success": False, "message": f"请求异常: {e}"}

        code, data = req("POST", "/api/user/login", json_body={"username": username, "password": password})
        if not isinstance(data, dict) or not data.get("success"):
            msg = data.get("message", "登录失败") if isinstance(data, dict) else "登录失败"
            return {"status": "failed", "added_quota": 0, "quota_after": 0, "used_quota": 0, "message": f"登录失败: {msg}"}, meta
        token = ((data.get("data") or {}).get("access_token") or "")
        if not token:
            return {"status": "failed", "added_quota": 0, "quota_after": 0, "used_quota": 0, "message": "登录成功但未返回 access_token"}, meta

        try:
            _, status_data = req("GET", "/api/status", token=token)
            if isinstance(status_data, dict) and status_data.get("success"):
                sd = status_data.get("data") or {}
                meta = {
                    "quota_per_unit": int(sd.get("quota_per_unit") or 500000),
                    "display_in_currency": bool(sd.get("display_in_currency")),
                    "quota_display_type": str(sd.get("quota_display_type") or "USD"),
                    "usd_exchange_rate": float(sd.get("usd_exchange_rate") or 1),
                    "custom_currency_symbol": str(sd.get("custom_currency_symbol") or "¤"),
                    "custom_currency_exchange_rate": float(sd.get("custom_currency_exchange_rate") or 1),
                }
        except Exception as e:
            logger.warn(f"[NewApiCheckIn] 获取站点配额参数失败: {e}")

        def fetch_self():
            _, d = req("GET", "/api/user/self", token=token)
            if isinstance(d, dict) and d.get("success"):
                dd = d.get("data") or {}
                return dd.get("quota"), dd.get("used_quota"), ""
            return None, None, str(d.get("message", "获取余额失败")) if isinstance(d, dict) else "获取余额失败"

        qpu = int(meta.get("quota_per_unit") or 500000)

        def fetch_today_checkin_quota():
            # 站点日志里补算当天签到获得的额度（type=4，内容形如「用户签到，获得额度 ＄31.335102 额度」）
            try:
                _, ld = req("GET", "/api/log/self?p=1&page_size=50", token=token)
                if isinstance(ld, dict) and ld.get("success"):
                    items = ((ld.get("data") or {}).get("items")) or []
                    tz = pytz.timezone(settings.TZ)
                    day_start = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
                    day_start_ts = int(day_start.timestamp())
                    total = 0
                    for it in items:
                        if it.get("type") == 4 and int(it.get("created_at") or 0) >= day_start_ts:
                            m = re.search(r"获得额度\s*[＄$¥]\s*([\d,]+(?:\.[\d]+)?)", str(it.get("content") or ""))
                            if m:
                                try:
                                    total += int(round(float(m.group(1).replace(",", "")) * qpu))
                                except ValueError:
                                    pass
                    if total:
                        return total
            except Exception as e:
                logger.warn(f"[NewApiCheckIn] 获取今日签到日志失败: {e}")
            return 0

        quota_before, used_before, _ = fetch_self()

        _, check_data = req("POST", "/api/user/checkin", token=token)
        if not isinstance(check_data, dict) or not check_data.get("success"):
            msg = str(check_data.get("message", "签到失败")) if isinstance(check_data, dict) else "签到失败"
            if "已签到" in msg:
                quota_after = quota_before
                used_after = used_before
                if quota_after is None:
                    quota_after, used_after, _ = fetch_self()
                added = fetch_today_checkin_quota()
                note = f"，今日已获得 {self.__fmt_money(added, qpu, meta)}" if added else ""
                return {"status": "already", "added_quota": added, "quota_after": quota_after or 0,
                        "used_quota": used_after or 0, "message": msg + note}, meta
            return {"status": "failed", "added_quota": 0, "quota_after": quota_before or 0,
                    "used_quota": used_before or 0, "message": msg}, meta

        quota_after, used_after, _ = fetch_self()
        msg = str(check_data.get("message") or "签到成功")
        added = 0
        if isinstance(quota_before, int) and isinstance(quota_after, int):
            added = max(0, quota_after - quota_before)
        m = re.search(r"获得\s*([＄$¥])?\s*([\d,]+(?:\.[\d]+)?)", msg)
        if m:
            try:
                val = float(m.group(2).replace(",", ""))
                added = int(round(val * qpu)) if m.group(1) else int(val)
            except ValueError:
                pass
        if not added:
            added = fetch_today_checkin_quota()
        return {"status": "success", "added_quota": added, "quota_after": quota_after or 0,
                "used_quota": used_after or 0, "message": msg}, meta

    # ============================================================
    # 工具方法
    # ============================================================
    def __parse_accounts(self) -> List[Dict[str, str]]:
        accounts = []
        for line in self._accounts.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "|" in line:
                username, password = line.split("|", 1)
            elif " " in line:
                username, password = line.split(" ", 1)
            else:
                logger.warn(f"[NewApiCheckIn] 账号行格式不正确，跳过: {line[:20]}")
                continue
            username = username.strip()
            password = password.strip()
            if username and password:
                accounts.append({"username": username, "password": password})
        return accounts

    def __send_notify(self, results: List[dict]):
        meta = self.get_data("site_meta") or {}
        qpu = int(meta.get("quota_per_unit") or 500000)
        tz = pytz.timezone(settings.TZ)
        today = datetime.now(tz).strftime("%Y-%m-%d")

        ok_count = sum(1 for r in results if r.get("status") in ("success", "already"))
        failed_list = [r for r in results if r.get("status") == "failed"]
        total_added = sum(int(r.get("added_quota") or 0) for r in results if r.get("status") != "failed")

        lines = []
        for r in results:
            status = r.get("status", "")
            acc = r.get("account") or "?"
            if status == "failed":
                lines.append(f"❌ {acc}")
                lines.append(f"　原因：{r.get('message') or '未知错误'}")
            else:
                state = "签到成功" if status == "success" else "今日已签到"
                added = int(r.get("added_quota") or 0)
                gain = f"📈 获得 +{self.__fmt_money(added, qpu, meta)}" if added > 0 else "➖ 获得 -"
                balance = self.__fmt_money(r.get("quota_after"), qpu, meta)
                lines.append(f"✅ {acc} · {state}")
                lines.append(f"　{gain}")
                lines.append(f"　💰 余额：{balance}")
            lines.append("──────────────")

        if lines:
            lines.pop()  # 去掉最后一个分隔线

        lines.append(f"━━━━━━━━━━━━━━━━")
        lines.append(f"🎯 今日合计获得：{self.__fmt_money(total_added, qpu, meta)}")
        if failed_list:
            lines.append(f"⚠️ 失败 {len(failed_list)} 个账号（{', '.join(r.get('account') or '?' for r in failed_list)}）")

        title = f"📊 DGB公益站签到 · {today}" if ok_count else "🔴 DGB公益站签到失败"
        self.post_message(
            mtype=NotificationType.SiteMessage,
            title=title,
            text="\n".join(lines),
        )

    @staticmethod
    def __fmt_quota(value) -> str:
        try:
            return f"{int(value):,}"
        except (TypeError, ValueError):
            return "-"

    @staticmethod
    def __fmt_money(value, quota_per_unit: int, meta: dict) -> str:
        try:
            qpu = int(quota_per_unit) or 500000
            num = int(value or 0) / qpu
        except (TypeError, ValueError):
            return "-"
        d_type = str((meta or {}).get("quota_display_type") or "USD")
        if d_type == "TOKENS":
            return f"{num:,.0f} Tokens"
        symbol = "$"
        if d_type == "CNY":
            symbol = "¥"
            try:
                num = num * float((meta or {}).get("usd_exchange_rate") or 1)
            except (TypeError, ValueError):
                pass
        elif d_type == "CUSTOM":
            symbol = str((meta or {}).get("custom_currency_symbol") or "¤")
            try:
                num = num * float((meta or {}).get("custom_currency_exchange_rate") or 1)
            except (TypeError, ValueError):
                pass
        return f"{symbol}{num:,.2f}"
