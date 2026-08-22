"""
哈基米API站签到 (GemaiCheckIn) — MoviePilot V3 插件
自动签到 api.gemai.cc，多账号，纯 HTTP（无浏览器依赖）
"""
import time
import traceback
from typing import Any, Dict, List, Tuple

import requests

from app.core.config import settings
from app.plugins import _PluginBase
from app.schemas import NotificationType

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger


class GemaiCheckIn(_PluginBase):
    # 插件信息
    plugin_name = "哈基米API站签到"
    plugin_name_en = "GemaiCheckIn"
    plugin_desc = "自动签到 api.gemai.cc（哈基米API站），多账号支持"
    plugin_version = "0.1.1"
    plugin_author = "wgl520ly"
    plugin_homepage = "https://github.com/wgl520ly/moviepilot-plugins"
    plugin_icon = "https://raw.githubusercontent.com/wgl520ly/moviepilot-plugins/main/icons/GemaiCheckIn.png"
    plugin_category = "签到"
    plugin_order = 8
    plugin_labels = ["签到", "自动"]

    # 配置项
    _enabled: bool = False
    _onlyonce: bool = False
    _cron: str = "0 8 * * *"
    _notify: bool = True
    _accounts: str = ""
    _timeout: int = 30
    _scheduler = None

    # 缓存的状态数据
    _cached_data: dict = {}

    def init_plugin(self, config: dict = None):
        if config:
            self._enabled = config.get("enabled", False)
            self._onlyonce = config.get("onlyonce", False)
            self._cron = config.get("cron", "0 8 * * *")
            self._notify = config.get("notify", True)
            self._accounts = config.get("accounts", "")
            self._timeout = config.get("timeout", 30)

        self.stop_service()
        if self._enabled and self._cron:
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            try:
                self._scheduler.add_job(
                    self.do_checkin_all,
                    CronTrigger.from_crontab(self._cron),
                    id="gemai_checkin",
                    replace_existing=True,
                )
                if not self._scheduler.running:
                    self._scheduler.start()
            except Exception as e:
                from app.log import logger
                logger.error(f"[GemaiCheckIn] 注册定时任务失败: {e}")

        if self._onlyonce:
            self._onlyonce = False
            # 持久化 onlyonce=false 防止热重载重复触发
            self.update_config({
                "enabled": self._enabled,
                "cron": self._cron,
                "notify": self._notify,
                "accounts": self._accounts,
                "onlyonce": False,
                "timeout": self._timeout,
            })
            try:
                self.do_checkin_all()
            except Exception:
                pass

    def stop_service(self):
        if self._scheduler is not None:
            try:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown(wait=False)
            except Exception:
                pass
            self._scheduler = None

    def get_state(self) -> dict:
        accounts = self._parse_accounts()
        return {
            "enabled": self._enabled,
            "cron": self._cron,
            "notify": self._notify,
            "accounts_count": len(accounts),
            **self._cached_data,
        }

    # ==================== 页面 ====================
    def get_page(self) -> List[dict]:
        token = settings.API_TOKEN or ""
        params = "?apikey=%s" % token if token else ""
        accounts = self._parse_accounts()
        cached = self._cached_data

        balance = cached.get("quota", 0)
        gift = cached.get("gift_quota", 0)
        total_quota = cached.get("total_quota", 0)
        today_str = cached.get("checkin_date", "无")
        awarded = cached.get("quota_awarded", 0)

        def fmt_yuan(v):
            """额度转 ¥ 显示 (500K额度=¥1)"""
            yuan = v / 500000.0
            if yuan >= 100:
                return "¥%.0f" % yuan
            elif yuan >= 1:
                return "¥%.2f" % yuan
            elif yuan >= 0.01:
                return "¥%.3f" % yuan
            return "¥%.4f" % yuan

        def fmt_quota(v):
            """额度原始值显示"""
            if v >= 1_000_000:
                return "%.1fM" % (v / 1_000_000)
            elif v >= 1000:
                return "%.1fK" % (v / 1000)
            return str(v)

        page = [
            # === 账户信息卡片 ===
            {
                "component": "VCard",
                "props": {"variant": "outlined", "class": "pa-3 mb-3"},
                "content": [
                    {"component": "VCardTitle", "props": {"class": "text-subtitle-1 font-weight-bold pb-0"},
                     "content": [
                         {"component": "Icon", "props": {"icon": "mdi-wallet", "size": "18", "class": "mr-1"}},
                         {"component": "text", "text": "账户概览"},
                     ]},
                    {"component": "VCardText", "props": {"class": "pt-2"}, "content": [
                        {"component": "VRow", "props": {"dense": True}, "content": [
                            {"component": "VCol", "props": {"cols": 6, "sm": 3}, "content": [
                                {"component": "div", "props": {"class": "text-center"}, "content": [
                                    {"component": "div", "props": {"class": "text-h5 font-weight-bold text-primary"},
                                     "content": [{"component": "text", "text": fmt_quota(balance)}]},
                                    {"component": "div", "props": {"class": "text-caption text-medium-emphasis"},
                                     "content": [{"component": "text", "text": "余额 %s" % fmt_yuan(balance)}]},
                                ]},
                            ]},
                            {"component": "VCol", "props": {"cols": 6, "sm": 3}, "content": [
                                {"component": "div", "props": {"class": "text-center"}, "content": [
                                    {"component": "div", "props": {"class": "text-h5 font-weight-bold text-success"},
                                     "content": [{"component": "text", "text": fmt_quota(gift)}]},
                                    {"component": "div", "props": {"class": "text-caption text-medium-emphasis"},
                                     "content": [{"component": "text", "text": "赠送 %s" % fmt_yuan(gift)}]},
                                ]},
                            ]},
                            {"component": "VCol", "props": {"cols": 6, "sm": 3}, "content": [
                                {"component": "div", "props": {"class": "text-center"}, "content": [
                                    {"component": "div", "props": {"class": "text-h5 font-weight-bold text-info"},
                                     "content": [{"component": "text", "text": fmt_quota(total_quota)}]},
                                    {"component": "div", "props": {"class": "text-caption text-medium-emphasis"},
                                     "content": [{"component": "text", "text": "累计 %s" % fmt_yuan(total_quota)}]},
                                ]},
                            ]},
                            {"component": "VCol", "props": {"cols": 6, "sm": 3}, "content": [
                                {"component": "div", "props": {"class": "text-center"}, "content": [
                                    {"component": "div", "props": {"class": "text-h5 font-weight-bold text-warning"},
                                     "content": [{"component": "text", "text": fmt_quota(awarded)}]},
                                    {"component": "div", "props": {"class": "text-caption text-medium-emphasis"},
                                     "content": [{"component": "text", "text": "签到 %s" % fmt_yuan(awarded)}]},
                                ]},
                            ]},
                        ]},
                    ]},
                ],
            },
            # === 签到信息卡片 ===
            {
                "component": "VCard",
                "props": {"variant": "outlined", "class": "pa-3 mb-3"},
                "content": [
                    {"component": "VCardTitle", "props": {"class": "text-subtitle-1 font-weight-bold pb-0"},
                     "content": [
                         {"component": "Icon", "props": {"icon": "mdi-calendar-check", "size": "18", "class": "mr-1"}},
                         {"component": "text", "text": "签到信息"},
                     ]},
                    {"component": "VCardText", "content": [
                        {"component": "VRow", "content": [
                            {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [
                                {"component": "VAlert", "props": {"type": "success", "variant": "tonal", "density": "compact"},
                                 "content": [
                                     {"component": "Icon", "props": {"icon": "mdi-check-circle", "size": "20", "class": "mr-2"}},
                                     {"component": "text", "text": "签到日期: %s | 奖励: %s" % (today_str, fmt_yuan(awarded))},
                                 ]},
                            ]},
                            {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [
                                {"component": "VAlert", "props": {"type": "info", "variant": "tonal", "density": "compact"},
                                 "content": [
                                     {"component": "Icon", "props": {"icon": "mdi-cash", "size": "20", "class": "mr-2"}},
                                     {"component": "text", "text": "余额: %s | 赠送: %s | 账号: %d" % (fmt_yuan(balance), fmt_yuan(gift), len(accounts))},
                                 ]},
                            ]},
                            {"component": "VCol", "props": {"cols": 12}, "content": [
                                {"component": "VAlert", "props": {"type": "surface", "variant": "outlined", "density": "compact"},
                                 "content": [
                                     {"component": "Icon", "props": {"icon": "mdi-information-outline", "size": "16", "class": "mr-2"}},
                                     {"component": "text", "text": "定时: %s | 通知: %s" % (self._cron or "未设置", "开启" if self._notify else "关闭")},
                                 ]},
                            ]},
                        ]},
                    ]},
                ],
            },
            # === 操作按钮 ===
            {
                "component": "VCard",
                "props": {"variant": "outlined", "class": "pa-3"},
                "content": [
                    {"component": "VRow", "content": [
                        {"component": "VCol", "props": {"cols": 12, "sm": 6}, "content": [
                            {"component": "VBtn", "props": {"color": "success", "block": True, "variant": "flat", "prepend-icon": "mdi-check-bold", "size": "large"},
                             "text": "立即签到全部",
                             "events": {"click": {"api": "plugin/GemaiCheckIn/checkin" + params, "method": "get"}}},
                        ]},
                        {"component": "VCol", "props": {"cols": 12, "sm": 6}, "content": [
                            {"component": "VBtn", "props": {"color": "info", "block": True, "variant": "tonal", "prepend-icon": "mdi-sync"},
                             "text": "刷新状态",
                             "events": {"click": {"api": "plugin/GemaiCheckIn/status" + params, "method": "get"}}},
                        ]},
                    ]},
                ],
            },
        ]
        return page

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [{
            "component": "VForm",
            "content": [{
                "component": "VRow",
                "content": [
                    {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [
                        {"component": "VSwitch", "props": {"model": "enabled", "label": "启用插件"}}]},
                    {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [
                        {"component": "VSwitch", "props": {"model": "onlyonce", "label": "保存后立即执行一次"}}]},
                    {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [
                        {"component": "VSwitch", "props": {"model": "notify", "label": "签到结果通知"}}]},
                    {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [
                        {"component": "VTextField", "props": {"model": "cron", "label": "定时签到 (cron)", "placeholder": "0 8 * * *"}}]},
                    {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [
                        {"component": "VTextField", "props": {"model": "timeout", "label": "超时(秒)", "type": "number", "placeholder": "30"}}]},
                    {"component": "VCol", "props": {"cols": 12}, "content": [
                        {"component": "VTextarea", "props": {
                            "model": "accounts",
                            "label": "账号列表（每行一个 用户名|密码）",
                            "placeholder": "wgl520ly|wgl19880917\nwgl520ly1|wgl19880917",
                            "rows": 5,
                            "persistent-placeholder": True,
                        }}]},
                ],
            }],
        }], {}

    def get_command(self) -> List[dict]:
        return [
            {"cmd": "/gemai", "event": "system", "desc": "手动签到哈基米API站（全部账号）", "category": "签到"},
        ]

    def get_api(self) -> List[dict]:
        return [
            {"path": "/checkin", "endpoint": self.api_checkin, "methods": ["GET"], "summary": "手动签到全部账号"},
            {"path": "/status", "endpoint": self.api_status, "methods": ["GET"], "summary": "查看签到状态"},
        ]

    # ==================== API ====================
    def api_checkin(self) -> dict:
        results = self.do_checkin_all()
        return {"success": True, "data": results}

    def api_status(self) -> dict:
        accounts = self._parse_accounts()
        if accounts:
            try:
                info = self._get_account_info(accounts[0])
                if info:
                    self._cached_data = info
            except Exception:
                pass
        return {"success": True, "data": {"accounts_count": len(accounts), **self._cached_data}}

    # ==================== 签到核心 ====================
    def _parse_accounts(self) -> list:
        """解析账号列表，格式：用户名|密码"""
        accounts = []
        for line in self._accounts.splitlines():
            line = line.strip()
            if not line or "|" not in line:
                continue
            parts = line.split("|", 1)
            username = parts[0].strip()
            password = parts[1].strip()
            if username and password:
                accounts.append({"username": username, "password": password})
        return accounts

    def _login(self, username: str, password: str) -> str:
        """登录获取 session cookie"""
        try:
            resp = requests.post(
                "https://api.gemai.cc/api/user/login",
                json={"username": username, "password": password},
                timeout=self._timeout,
            )
            data = resp.json()
            if data.get("success"):
                # 获取 session cookie
                cookie_jar = resp.cookies
                session = cookie_jar.get("session", "")
                user_id = data.get("data", {}).get("id", "")
                return session, user_id
            return "", ""
        except Exception as e:
            from app.log import logger
            logger.error(f"[GemaiCheckIn] 登录失败 {username}: {e}")
            return "", ""

    def _checkin(self, session: str, user_id) -> dict:
        """执行签到"""
        try:
            resp = requests.post(
                "https://api.gemai.cc/api/user/checkin",
                cookies={"session": session},
                headers={"New-Api-User": str(user_id)},
                timeout=self._timeout,
            )
            return resp.json()
        except Exception as e:
            return {"success": False, "message": str(e)}

    def _get_account_info(self, account: dict) -> dict:
        """获取账户信息"""
        session, user_id = self._login(account["username"], account["password"])
        if not session:
            return {}
        try:
            resp = requests.get(
                "https://api.gemai.cc/api/user/self",
                cookies={"session": session},
                headers={"New-Api-User": str(user_id)},
                timeout=self._timeout,
            )
            data = resp.json()
            if data.get("success"):
                d = data.get("data", {})
                return {
                    "username": d.get("username", ""),
                    "quota": d.get("quota", 0),
                    "gift_quota": d.get("gift_quota", 0),
                    "total_quota": d.get("total_quota", 0),
                    "used_quota": d.get("used_quota", 0),
                    "email": d.get("email", ""),
                }
        except Exception:
            pass
        return {}

    def do_checkin_all(self) -> list:
        """签到全部账号"""
        from app.log import logger
        accounts = self._parse_accounts()
        if not accounts:
            logger.warning("[GemaiCheckIn] 未配置任何账号")
            return [{"success": False, "msg": "未配置账号"}]

        results = []
        for account in accounts:
            username = account["username"]
            masked = username[:3] + "***" + username[-2:] if len(username) > 5 else username
            try:
                # 登录
                session, user_id = self._login(username, account["password"])
                if not session:
                    results.append({"success": False, "username": masked, "msg": "登录失败"})
                    continue

                # 签到
                checkin_resp = self._checkin(session, user_id)
                if checkin_resp.get("success"):
                    cd = checkin_resp.get("data", {})
                    awarded = cd.get("quota_awarded", 0)
                    date = cd.get("checkin_date", "")
                    msg = "签到成功 +%s" % fmt_yuan(awarded)

                    # 获取账户信息
                    info = self._get_account_info(account)
                    balance = info.get("quota", 0)
                    gift = info.get("gift_quota", 0)

                    results.append({
                        "success": True,
                        "username": masked,
                        "msg": msg,
                        "date": date,
                        "awarded": awarded,
                        "balance": balance,
                        "gift_quota": gift,
                    })

                    # 缓存最后一个账号的数据
                    self._cached_data = {
                        "quota": balance,
                        "gift_quota": gift,
                        "total_quota": info.get("total_quota", 0),
                        "quota_awarded": awarded,
                        "checkin_date": date,
                    }

                    logger.info(f"[GemaiCheckIn] {masked} {msg}, 余额: {fmt_yuan(balance)}")
                else:
                    err_msg = checkin_resp.get("message", "签到失败")
                    # "今日已签到"视为成功
                    if "已签到" in err_msg or "already" in err_msg.lower():
                        info = self._get_account_info(account)
                        balance = info.get("quota", 0)
                        gift = info.get("gift_quota", 0)
                        results.append({
                            "success": True,
                            "username": masked,
                            "msg": "今日已签到",
                            "balance": balance,
                            "gift_quota": gift,
                        })
                        self._cached_data = {
                            "quota": balance,
                            "gift_quota": gift,
                            "total_quota": info.get("total_quota", 0),
                            "quota_awarded": 0,
                            "checkin_date": checkin_resp.get("data", {}).get("checkin_date", time.strftime("%Y-%m-%d")),
                        }
                        logger.info(f"[GemaiCheckIn] {masked} 今日已签到, 余额: {balance}")
                    else:
                        results.append({"success": False, "username": masked, "msg": err_msg})
                        logger.warning(f"[GemaiCheckIn] {masked} {err_msg}")

                # 账号间间隔
                if len(accounts) > 1:
                    time.sleep(2)

            except Exception as e:
                logger.error(f"[GemaiCheckIn] {masked} 异常: {e}")
                results.append({"success": False, "username": masked, "msg": str(e)[:100]})

        # 发送通知
        if self._notify and results:
            success_count = sum(1 for r in results if r.get("success"))
            fail_count = len(results) - success_count
            lines = ["[哈基米API站签到结果]\n"]
            for r in results:
                icon = "✅" if r.get("success") else "❌"
                extra = ""
                if r.get("success"):
                    balance = r.get('balance', 0)
                    extra = " | 余额: %s" % fmt_yuan(balance)
                lines.append(f"{icon} {r.get('username', '?')}: {r.get('msg', '未知')}{extra}")
            lines.append(f"\n成功: {success_count} / 失败: {fail_count}")
            self.post_message(
                mtype=NotificationType.SiteMessage,
                title="哈基米API站签到",
                text="\n".join(lines),
            )

        return results
