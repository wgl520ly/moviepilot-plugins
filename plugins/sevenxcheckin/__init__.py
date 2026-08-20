# -*- coding: utf-8 -*-
"""
7x.hk 每日签到 (SevenXCheckIn) - 7x.hk (New API 站点) 多账号每日签到 + 余额报告

7x 是 QuantumNous New API 系站点，但认证为 cookie + New-Api-User 双要素：
  1. POST /api/user/login 返回 data.id 作为用户 ID，同时下发 session cookie
  2. 后续接口需带 cookie + header "New-Api-User: <id>"
流程：登录 -> 记 id/cookie -> POST /api/user/checkin -> GET /api/user/self 取余额
"""
import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.core.event import eventmanager, Event
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import NotificationType
from app.schemas.types import EventType


class SevenXCheckIn(_PluginBase):
    """7x.hk 每日签到"""

    plugin_name = "7x.hk 每日签到"
    plugin_desc = "每天定时访问 7x.hk (New API 站点)，对配置账号逐一完成每日签到并汇总余额/获得额度推送通知"
    plugin_version = "0.1.0"
    plugin_author = "fnos"
    plugin_order = 95
    auth_level = 1
    plugin_config_prefix = "sevenxcheckin_"

    _enabled = False
    _onlyonce = False
    _cron = "0 9 * * *"
    _notify = True
    _notify_failed_only = False
    _base_url = "https://7x.hk"
    _accounts = ""
    _scheduler = None

    def init_plugin(self, config=None):
        self.stop_service()
        if config:
            self._enabled = bool(config.get("enabled", False))
            self._onlyonce = bool(config.get("onlyonce", False))
            self._cron = str(config.get("cron") or "0 9 * * *")
            self._notify = bool(config.get("notify", True))
            self._notify_failed_only = bool(config.get("notify_failed_only", False))
            self._base_url = str(config.get("base_url") or "https://7x.hk").rstrip("/")
            self._accounts = str(config.get("accounts") or "").strip()

        if not self._enabled:
            logger.info("[SevenXCheckIn] 插件未启用")
            return

        accounts = self.__parse_accounts()
        if not accounts:
            logger.warn("[SevenXCheckIn] 未配置有效账号")
            return

        logger.info(f"[SevenXCheckIn] 插件已启用，每日 {self._cron} 执行，{len(accounts)} 个账号，站点 {self._base_url}")

        if self._onlyonce:
            self._onlyonce = False
            self.__update_config()
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            from datetime import timedelta
            self._scheduler.add_job(self.run_all, "date",
                                    run_date=datetime.now() + timedelta(seconds=3))
            self._scheduler.start()
            logger.info("[SevenXCheckIn] 已安排立即执行")

    def get_state(self):
        return self._enabled

    def stop_service(self):
        try:
            if self._scheduler:
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
        except Exception as e:
            logger.error(f"[SevenXCheckIn] 停止服务失败: {e}")

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

    def get_form(self):
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [
                                {"component": "VSwitch", "props": {"model": "enabled", "label": "启用插件"}}]},
                            {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [
                                {"component": "VSwitch", "props": {"model": "onlyonce", "label": "保存后立即执行一次"}}]},
                            {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [
                                {"component": "VTextField", "props": {"model": "cron", "label": "执行周期 (cron)", "placeholder": "0 9 * * *"}}]},
                            {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [
                                {"component": "VSwitch", "props": {"model": "notify", "label": "发送站内通知"}}]},
                            {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [
                                {"component": "VSwitch", "props": {"model": "notify_failed_only", "label": "仅失败时通知"}}]},
                            {"component": "VCol", "props": {"cols": 12, "md": 12}, "content": [
                                {"component": "VTextField", "props": {"model": "base_url", "label": "站点 Base URL", "placeholder": "https://7x.hk"}}]},
                            {"component": "VCol", "props": {"cols": 12, "md": 12}, "content": [
                                {"component": "VTextarea", "props": {
                                    "model": "accounts",
                                    "label": "账号列表（每行：账号|密码）",
                                    "rows": 5,
                                    "placeholder": "user1|pass1\nuser2|pass2"}}]}
                        ]
                    }
                ]
            }
        ], {
            "enabled": False, "onlyonce": False, "cron": "0 9 * * *", "notify": True,
            "notify_failed_only": False, "base_url": "https://7x.hk", "accounts": "",
        }

    def get_page(self):
        records = self.get_data("records") or []
        if not records:
            return [{"component": "VAlert", "props": {"type": "info", "variant": "tonal",
                "text": "暂无签到记录。请先在插件设置里填入账号并启用，或勾选「保存后立即执行一次」触发一轮签到。", "class": "mb-2"}}]

        cur = records[0]
        accs = cur.get("accounts") or []
        ok = sum(1 for a in accs if a.get("status") in ("success", "already"))
        fail = sum(1 for a in accs if a.get("status") == "failed")

        cards = [{
            "component": "VCard",
            "props": {"variant": "elevated", "elevation": 2, "rounded": "lg", "class": "mb-4"},
            "content": [
                {"component": "VCardTitle", "props": {"class": "text-h6 font-weight-bold"},
                 "text": f"📊 7x.hk 签到 · {cur.get('day') or '今天'}"},
                {"component": "VCardText", "content": [
                    {"component": "VRow", "content": [
                        {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [
                            {"component": "VChip", "props": {"size": "large", "variant": "tonal", "color": "success"},
                             "text": f"✅ 成功/已签到 {ok}"}]},
                        {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [
                            {"component": "VChip", "props": {"size": "large", "variant": "tonal", "color": "error"},
                             "text": f"❌ 失败 {fail}"}]},
                        {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [
                            {"component": "VChip", "props": {"size": "large", "variant": "tonal", "color": "primary"},
                             "text": f"🕒 {str(cur.get('date', ''))[5:16]}"}]},
                    ]}
                ]}
            ]
        }]

        lines = []
        for a in accs:
            icon = "✅" if a.get("status") in ("success", "already") else "❌"
            tag = "已签到" if a.get("status") == "already" else ("成功" if a.get("status") == "success" else "失败")
            msg = a.get("message") or ""
            m = a.get("meta") or {}
            extra = ""
            if a.get("quota_after"):
                extra = f" · 💰 余额 {self.__fmt_money(a.get('quota_after') or 0, m)}"
            if a.get("status") in ("success", "already") and a.get("added_quota"):
                extra += f" · 📈 获得 {self.__fmt_money(a.get('added_quota') or 0, m)}"
            lines.append(f"　{icon} {a.get('username')} · {tag}（{msg}）{extra}")

        cards.append({"component": "VCard", "props": {"variant": "elevated", "elevation": 2, "rounded": "lg", "class": "mb-4"}, "content": [
            {"component": "VCardTitle", "props": {"class": "text-h6 font-weight-bold"}, "text": "今日明细"},
            {"component": "VCardText", "props": {"style": "white-space:pre-line"}, "text": "\n".join(lines)},
        ]})

        rows = []
        for h in records[:20]:
            haccs = h.get("accounts") or []
            hok = sum(1 for a in haccs if a.get("status") in ("success", "already"))
            hfail = sum(1 for a in haccs if a.get("status") == "failed")
            rows.append({"component": "tr", "content": [
                {"component": "td", "props": {"class": "text-caption"}, "text": h.get("day", "")},
                {"component": "td", "content": [{"component": "VChip", "props": {"size": "small", "variant": "tonal", "color": "success"}, "text": f"✅ {hok}"}]},
                {"component": "td", "content": [{"component": "VChip", "props": {"size": "small", "variant": "tonal", "color": "error"}, "text": f"❌ {hfail}"}]},
                {"component": "td", "props": {"class": "text-caption"},
                 "text": ", ".join(a.get("username", "") for a in haccs if a.get("status") == "failed")[:60]},
            ]})
        cards.append({"component": "VCard", "props": {"variant": "elevated", "elevation": 2, "rounded": "lg", "class": "mb-4"}, "content": [
            {"component": "VCardTitle", "props": {"class": "text-h6 font-weight-bold"}, "text": "📜 历史"},
            {"component": "VCardText", "content": [
                {"component": "VTable", "props": {"hover": True, "density": "comfortable"}, "content": [
                    {"component": "thead", "content": [{"component": "tr", "content": [
                        {"component": "th", "props": {"class": "text-body-2"}, "text": "日期"},
                        {"component": "th", "props": {"class": "text-body-2"}, "text": "成功"},
                        {"component": "th", "props": {"class": "text-body-2"}, "text": "失败"},
                        {"component": "th", "props": {"class": "text-body-2"}, "text": "失败账号"},
                    ]}]},
                    {"component": "tbody", "content": rows}
                ]}
            ]}
        ]})
        return cards

    def get_service(self):
        if self._enabled and self._cron:
            return [{
                "id": "SevenXCheckInTimer",
                "name": "7x.hk 每日签到",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.run_all,
                "kwargs": {},
            }]
        return []

    def get_command(self):
        return [{"cmd": "/sevenx", "event": EventType.PluginAction, "desc": "7x.hk 立即签到",
                 "category": "工具", "data": {"action": "check"}}]

    @eventmanager.register(EventType.PluginAction)
    def handle_command(self, event=None):
        if not event:
            return
        if (event.event_data or {}).get("action") == "check":
            self.run_all()

    def get_api(self):
        return [
            {"path": "/SevenXCheckIn/run", "endpoint": self.api_run, "methods": ["POST"],
             "summary": "立即签到", "description": "手动触发一次全部账号签到"},
            {"path": "/SevenXCheckIn/records", "endpoint": self.api_records, "methods": ["GET"],
             "summary": "签到记录", "description": "返回最近 50 条签到记录"},
        ]

    def api_run(self):
        self.run_all()
        return {"success": True, "message": "签到任务已触发"}

    def api_records(self):
        return (self.get_data("records") or [])[:50]

    def run_all(self):
        accounts = self.__parse_accounts()
        if not accounts:
            logger.warn("[SevenXCheckIn] 未配置有效账号")
            return

        today = datetime.now().strftime("%Y-%m-%d")
        logger.info(f"[SevenXCheckIn] 开始签到（{len(accounts)} 个账号）")

        results = []
        for acc in accounts:
            try:
                results.append(self.__checkin_one(acc))
            except Exception as e:
                logger.error(f"[SevenXCheckIn] {acc.get('username')} 异常: {e}")
                results.append({"username": acc["username"], "status": "failed",
                                "added_quota": 0, "quota_after": 0, "used_quota": 0,
                                "message": f"异常: {e}"})

        ok = sum(1 for r in results if r["status"] == "success")
        already = sum(1 for r in results if r["status"] == "already")
        failed = sum(1 for r in results if r["status"] == "failed")
        logger.info(f"[SevenXCheckIn] 签到完成：成功 {ok}，已签到 {already}，失败 {failed}")

        self.__save_record(today, results)

        if self._notify:
            failed_list = [r for r in results if r["status"] == "failed"]
            if not self._notify_failed_only or failed_list:
                self.__send_notify(results)

    def __checkin_one(self, acc):
        username = acc["username"]
        password = acc["password"]
        base = self._base_url
        sess = requests.Session()
        sess.headers.update({"Content-Type": "application/json", "User-Agent": "MoviePilot/SevenXCheckIn"})

        def req(method, path, user_id=None):
            headers = {}
            if user_id:
                headers["New-Api-User"] = str(user_id)
            try:
                resp = sess.request(method, base + path, headers=headers, timeout=25)
                try:
                    return resp.status_code, resp.json()
                except Exception:
                    return resp.status_code, {"success": False, "message": f"HTTP {resp.status_code}: 响应不是 JSON"}
            except Exception as e:
                return None, {"success": False, "message": f"请求异常: {e}"}

        try:
            resp = sess.post(base + "/api/user/login",
                             json={"username": username, "password": password}, timeout=25)
            data = resp.json()
        except Exception as e:
            return {"username": username, "status": "failed",
                    "added_quota": 0, "quota_after": 0, "used_quota": 0,
                    "message": f"登录失败: {e}"}

        if not data.get("success"):
            return {"username": username, "status": "failed",
                    "added_quota": 0, "quota_after": 0, "used_quota": 0,
                    "message": f"登录失败: {data.get('message', '未知错误')}"}

        user_id = (data.get("data") or {}).get("id")
        if not user_id:
            return {"username": username, "status": "failed",
                    "added_quota": 0, "quota_after": 0, "used_quota": 0,
                    "message": "登录成功但未返回用户 ID"}

        meta = {"quota_per_unit": 500000, "display_in_currency": False,
                "quota_display_type": "USD", "usd_exchange_rate": 1.0,
                "custom_currency_symbol": "¤", "custom_currency_exchange_rate": 1.0}
        try:
            _, status_data = req("GET", "/api/status", user_id=user_id)
            if isinstance(status_data, dict) and status_data.get("success"):
                sd = (status_data.get("data") or {}).get("data") or {}
                meta = {
                    "quota_per_unit": int(sd.get("quota_per_unit") or 500000),
                    "display_in_currency": bool(sd.get("display_in_currency")),
                    "quota_display_type": str(sd.get("quota_display_type") or "USD"),
                    "usd_exchange_rate": float(sd.get("usd_exchange_rate") or 1),
                    "custom_currency_symbol": str(sd.get("custom_currency_symbol") or "¤"),
                    "custom_currency_exchange_rate": float(sd.get("custom_currency_exchange_rate") or 1),
                }
        except Exception as e:
            logger.warn(f"[SevenXCheckIn] 获取站点配额参数失败: {e}")

        q_before, used_before, _ = self.__fetch_self(req, user_id)

        try:
            resp = sess.post(base + "/api/user/checkin", headers={"New-Api-User": str(user_id)}, timeout=25)
            chk = resp.json()
        except Exception as e:
            return {"username": username, "status": "failed",
                    "added_quota": 0, "quota_after": q_before or 0, "used_quota": used_before or 0,
                    "message": f"签到请求异常: {e}"}

        chk_msg = (chk.get("message") or "").strip()

        q_after, used_after, _ = self.__fetch_self(req, user_id)
        if q_before is None:
            q_before = q_after or 0
        if q_after is None:
            q_after = q_before
        added = (q_after or 0) - (q_before or 0)

        # New API 系站点：今日已签到时常返回 success=false，仅凭 success 判定会误判为失败
        if "已签到" in chk_msg:
            return {"username": username, "status": "already",
                    "added_quota": 0, "quota_after": q_after or 0,
                    "used_quota": used_after or 0, "message": chk_msg or "今日已签到",
                    "meta": meta}
        if chk.get("success"):
            return {"username": username, "status": "success",
                    "added_quota": max(added, 0), "quota_after": q_after or 0,
                    "used_quota": used_after or 0, "message": chk_msg or "签到成功",
                    "meta": meta}
        return {"username": username, "status": "failed",
                "added_quota": 0, "quota_after": q_after or 0,
                "used_quota": used_after or 0,
                "message": chk_msg or "签到失败"}

    def __fetch_self(self, req, user_id):
        _, d = req("GET", "/api/user/self", user_id=user_id)
        if isinstance(d, dict) and d.get("success"):
            dd = d.get("data") or {}
            return dd.get("quota"), dd.get("used_quota"), ""
        msg = d.get("message", "获取余额失败") if isinstance(d, dict) else "获取余额失败"
        return None, None, msg

    def __parse_accounts(self):
        out = []
        for raw in (self._accounts or "").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "|" in line:
                u, _, p = line.partition("|")
                u, p = u.strip(), p.strip()
                if u and p:
                    out.append({"username": u, "password": p})
        return out

    def __save_record(self, day, results):
        records = self.get_data("records") or []
        records.insert(0, {"day": day, "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "accounts": results})
        records = records[:200]
        self.save_data("records", records)
        self.save_data("last_run", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def __send_notify(self, results):
        meta = ((results[0] or {}).get("meta") or {}) if results else {}
        meta = meta or {"quota_per_unit": 500000, "display_in_currency": False,
                       "quota_display_type": "USD", "usd_exchange_rate": 1.0,
                       "custom_currency_symbol": "¤", "custom_currency_exchange_rate": 1.0}

        lines = [f"📊 7x.hk 每日签到 · {datetime.now().strftime('%Y-%m-%d')}", ""]
        total_added = 0
        for r in results:
            tag = {"success": "✅ 签到成功", "already": "✅ 今日已签到", "failed": "❌ 失败"}.get(r["status"], r["status"])
            lines.append(f"{tag} {r['username']}")
            money_added = self.__fmt_money(r.get("added_quota") or 0, meta)
            money_balance = self.__fmt_money(r.get("quota_after") or 0, meta)
            if r["status"] in ("success", "already"):
                lines.append(f"　📈 本次获得：{money_added}")
                if r.get("quota_after"):
                    lines.append(f"　💰 当前余额：{money_balance}")
            else:
                lines.append(f"　⚠️ {r.get('message') or '失败原因未知'}")
                if r.get("quota_after"):
                    lines.append(f"　💰 当前余额：{money_balance}")
            lines.append("")
            if r["status"] in ("success", "already"):
                total_added += int(r.get("added_quota") or 0)

        lines.append("━" * 14)
        lines.append(f"🎯 今日合计获得：{self.__fmt_money(total_added, meta)}")

        self.post_message(
            mtype=NotificationType.SiteMessage,
            title=f"📊 7x.hk 每日签到 · {datetime.now().strftime('%Y-%m-%d')}",
            text="\n".join(lines),
        )

    def __fmt_money(self, quota_value, meta):
        qpu = int(meta.get("quota_per_unit") or 500000) or 500000
        if meta.get("display_in_currency"):
            symbol = "$" if meta.get("quota_display_type", "USD") == "USD" else meta.get("custom_currency_symbol", "¤")
            rate = float(meta.get("usd_exchange_rate") or 1)
            amount = (quota_value / qpu) * rate
            if symbol == "$":
                return "$" + f"{amount:,.2f}"
            return f"{symbol}{amount:,.2f}"
        return "$" + f"{quota_value / qpu:,.2f}"
