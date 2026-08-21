# -*- coding: utf-8 -*-
"""
局域网远程唤醒 (WakeOnLan) - 通过 Wake-on-LAN 魔术包远程唤醒局域网内电脑/设备

用法：
  1. 插件详情页配置设备列表（每行一个设备）：
       设备名称|MAC地址|广播地址(可选)|IP地址(可选,用于在线状态探测)
     示例：书房电脑|AA:BB:CC:DD:EE:FF|192.168.1.255|192.168.1.66
  2. 触发方式：
     - 插件详情页按钮（逐个/全部唤醒）
     - 聊天命令：/wol 全部唤醒、/wol_list 查看设备
     - HTTP 接口：GET /api/v1/plugin/WakeOnLan/wake?name=书房电脑&apikey=<MP_API_KEY>
                  GET /api/v1/plugin/WakeOnLan/wake_all?apikey=<MP_API_KEY>
     - 定时任务：配置 cron 到点自动全部唤醒（如工作日前自动开机）
注意：被唤醒设备需在 BIOS/网卡中开启 Wake-on-LAN（建议用网线连接）
"""
import re
import socket
import subprocess
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote as _q

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.core.event import eventmanager, Event
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import NotificationType
from app.schemas.types import EventType


class WakeOnLan(_PluginBase):
    """局域网远程唤醒"""

    plugin_name = "局域网远程唤醒 (WakeOnLan)"
    plugin_desc = "通过 Wake-on-LAN 魔术包远程唤醒局域网内电脑/设备；支持多设备、插件按钮、聊天命令、HTTP 接口与定时自动开机"
    plugin_version = "0.1.0"
    plugin_author = "wgl520ly"
    author_url = "https://github.com/wgl520ly"
    repo_url = "https://github.com/wgl520ly/moviepilot-plugins"
    plugin_icon = "https://raw.githubusercontent.com/wgl520ly/moviepilot-plugins/main/icons/WakeOnLan.png"
    plugin_order = 120
    auth_level = 1
    plugin_config_prefix = "wakeonlan_"

    _enabled = False
    _onlyonce = False
    _cron = ""
    _notify = True
    _port = 9
    _devices = ""
    _scheduler = None

    def init_plugin(self, config=None):
        self.stop_service()
        if config:
            self._enabled = bool(config.get("enabled", False))
            self._onlyonce = bool(config.get("onlyonce", False))
            self._cron = str(config.get("cron") or "").strip()
            self._notify = bool(config.get("notify", True))
            try:
                self._port = int(config.get("port") or 9)
            except Exception:
                self._port = 9
            self._devices = str(config.get("devices") or "").strip()

        if not self._enabled:
            logger.info("[WakeOnLan] 插件未启用")
            return

        devices = self.__parse_devices()
        if not devices:
            logger.warn("[WakeOnLan] 未配置有效设备（格式：名称|MAC|广播地址|IP）")
            return

        logger.info("[WakeOnLan] 插件已启用，%s 个设备，端口 %s，定时 %s"
                    % (len(devices), self._port, self._cron or "无"))

        if self._onlyonce:
            self._onlyonce = False
            self.__update_config()
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            from datetime import timedelta
            self._scheduler.add_job(self.wake_all, "date",
                                    run_date=datetime.now() + timedelta(seconds=3))
            self._scheduler.start()
            logger.info("[WakeOnLan] 已安排立即执行一次")
        elif self._cron:
            try:
                self._scheduler = BackgroundScheduler(timezone=settings.TZ)
                self._scheduler.add_job(self.wake_all, CronTrigger.from_crontab(self._cron))
                self._scheduler.start()
                logger.info("[WakeOnLan] 定时唤醒已启动：%s" % self._cron)
            except Exception as e:
                logger.error(f"[WakeOnLan] 定时任务配置失败: {e}")

    def get_state(self):
        return self._enabled

    def stop_service(self):
        try:
            if self._scheduler:
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
        except Exception as e:
            logger.error(f"[WakeOnLan] 停止服务失败: {e}")

    def __update_config(self):
        self.update_config({
            "enabled": self._enabled,
            "onlyonce": self._onlyonce,
            "cron": self._cron,
            "notify": self._notify,
            "port": self._port,
            "devices": self._devices,
        })

    # ---------- 设备解析 ----------
    def __parse_devices(self) -> List[dict]:
        """解析设备列表：名称|MAC|广播地址(可选)|IP(可选)"""
        result = []
        for line in self._devices.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")]
            name = parts[0]
            mac = parts[1] if len(parts) > 1 else ""
            broadcast = parts[2] if len(parts) > 2 and parts[2] else "255.255.255.255"
            ip = parts[3] if len(parts) > 3 and parts[3] else ""
            if not name or not self.__is_valid_mac(mac):
                continue
            result.append({
                "name": name,
                "mac": self.__normalize_mac(mac),
                "broadcast": broadcast,
                "ip": ip,
            })
        return result

    @staticmethod
    def __is_valid_mac(mac: str) -> bool:
        cleaned = re.sub(r"[:.\-\s]", "", mac or "").lower()
        return bool(re.fullmatch(r"[0-9a-f]{12}", cleaned))

    @staticmethod
    def __normalize_mac(mac: str) -> str:
        cleaned = re.sub(r"[:.\-\s]", "", mac or "").lower()
        return ":".join(cleaned[i:i + 2] for i in range(0, 12, 2))

    # ---------- WOL 发送 ----------
    def __send_wol(self, mac: str, broadcast: str, port: int) -> bool:
        """发送 Wake-on-LAN 魔术包（6*FF + 16*MAC）"""
        mac_bytes = bytes.fromhex(mac.replace(":", ""))
        packet = b"\xff" * 6 + mac_bytes * 16
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sent = 0
            for addr in [broadcast, "255.255.255.255"]:
                try:
                    sock.sendto(packet, (addr, port))
                    sent += 1
                except Exception as e:
                    logger.warn(f"[WakeOnLan] 发送到 {addr} 失败: {e}")
            sock.close()
            return sent > 0
        except Exception as e:
            logger.error(f"[WakeOnLan] WOL 发送异常: {e}")
            return False

    @staticmethod
    def __is_online(ip: str) -> Optional[bool]:
        """探测设备是否在线：先 ping，失败再尝试常见端口（445/3389/22/80/443）"""
        if not ip:
            return None
        ping_ok = None
        try:
            r = subprocess.run(["ping", "-c", "1", "-W", "1", ip],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
            ping_ok = r.returncode == 0
        except Exception:
            pass
        if ping_ok is True:
            return True
        for port in (445, 3389, 22, 80, 443):
            try:
                with socket.create_connection((ip, port), timeout=0.6):
                    return True
            except Exception:
                continue
        if ping_ok is False:
            return False
        return None

    # ---------- 唤醒动作 ----------
    def wake_device(self, name: str) -> dict:
        devices = self.__parse_devices()
        if not devices:
            return {"success": False, "msg": "未配置有效设备"}
        hit = [d for d in devices if d["name"] == name]
        if not hit:
            return {"success": False, "msg": "未找到设备: %s" % name}
        device = hit[0]
        ok = self.__send_wol(device["mac"], device["broadcast"], self._port)
        msg = ("✅ 已发送唤醒包\n" if ok else "⚠️ 发送失败\n") + \
              "设备: %s\nMAC: %s\n广播: %s:%s" % (device["name"], device["mac"], device["broadcast"], self._port)
        if self._notify:
            self.post_message(mtype=NotificationType.SiteMessage,
                              title="局域网唤醒",
                              text=msg)
        return {"success": ok, "msg": msg, "device": device}

    def wake_all(self) -> dict:
        devices = self.__parse_devices()
        if not devices:
            msg = "⚠️ 未配置有效设备"
            if self._notify:
                self.post_message(mtype=NotificationType.SiteMessage, title="局域网唤醒", text=msg)
            return {"success": False, "msg": msg, "results": []}
        lines = ["✅ 唤醒包已发送："]
        results = []
        for device in devices:
            ok = self.__send_wol(device["mac"], device["broadcast"], self._port)
            status = "✅" if ok else "⚠️"
            lines.append("%s %s (%s)" % (status, device["name"], device["mac"]))
            results.append({"name": device["name"], "mac": device["mac"], "success": ok})
        msg = "\n".join(lines)
        logger.info("[WakeOnLan] 唤醒结果:\n" + msg)
        if self._notify:
            self.post_message(mtype=NotificationType.SiteMessage,
                              title="局域网唤醒 (共 %s 台)" % len(devices),
                              text=msg)
        return {"success": True, "msg": msg, "results": results}

    # ---------- 命令 ----------
    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return [
            {
                "cmd": "/wol",
                "event": EventType.PluginAction,
                "desc": "唤醒局域网全部设备",
                "category": "工具",
                "data": {"action": "wol_all"}
            },
            {
                "cmd": "/wol_list",
                "event": EventType.PluginAction,
                "desc": "查看局域网唤醒设备列表",
                "category": "工具",
                "data": {"action": "wol_list"}
            },
        ]

    @eventmanager.register(EventType.PluginAction)
    def remote_action(self, event: Event):
        data = event.event_data or {}
        if data.get("action") in ["wol_all", "wol_list"]:
            if data.get("action") == "wol_all":
                self.wake_all()
            else:
                devices = self.__parse_devices()
                lines = ["📡 已配置设备："] if devices else ["📡 未配置设备，请先到插件页面配置"]
                for d in devices:
                    lines.append("· %s (%s) 广播 %s" % (d["name"], d["mac"], d["broadcast"]))
                self.post_message(mtype=NotificationType.SiteMessage,
                                  title="局域网唤醒",
                                  text="\n".join(lines))

    # ---------- API ----------
    def get_api(self) -> List[Dict[str, Any]]:
        return [
            {
                "path": "/wake",
                "endpoint": self.api_wake,
                "methods": ["GET"],
                "summary": "唤醒指定设备（name 参数）",
            },
            {
                "path": "/wake_all",
                "endpoint": self.api_wake_all,
                "methods": ["GET"],
                "summary": "唤醒全部设备",
            },
            {
                "path": "/devices",
                "endpoint": self.api_devices,
                "methods": ["GET"],
                "summary": "查看已配置设备列表",
            },
            {
                "path": "/device_remove",
                "endpoint": self.api_device_remove,
                "methods": ["GET"],
                "summary": "删除一个设备（name 参数）",
            },
            {
                "path": "/device_add",
                "endpoint": self.api_device_add,
                "methods": ["GET"],
                "summary": "添加一个设备（name, mac, broadcast, ip 参数）",
            },
        ]

    def api_wake(self, name: str = "") -> dict:
        if not name:
            return {"success": False, "msg": "缺少设备名称参数 name"}
        return self.wake_device(name)

    def api_wake_all(self) -> dict:
        return self.wake_all()

    def api_devices(self) -> dict:
        devices = self.__parse_devices()
        rows = []
        for d in devices:
            online = self.__is_online(d["ip"])
            status = "🟢 在线" if online is True else ("⚪ 离线" if online is False else "—")
            rows.append({
                "name": d["name"],
                "mac": d["mac"],
                "broadcast": d["broadcast"],
                "ip": d["ip"],
                "status": status,
            })
        return {"success": True, "data": rows}

    def api_device_remove(self, name: str = "") -> dict:
        if not name:
            return {"success": False, "msg": "缺少 name 参数"}
        lines = [ln for ln in self._devices.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        kept = [ln for ln in lines if ln.split("|", 1)[0].strip() != name]
        self._devices = "\n".join(kept)
        self.__update_config()
        return {"success": True, "msg": "删除设备：%s" % name, "devices": self._devices}

    def api_device_add(self, name: str = "", mac: str = "", broadcast: str = "", ip: str = "") -> dict:
        if not name or not self.__is_valid_mac(mac):
            return {"success": False, "msg": "参数错误：name 和合法的 mac 必填"}
        broadcast = broadcast or "255.255.255.255"
        line = f"{name}|{mac}|{broadcast}|{ip}"
        lines = [ln for ln in self._devices.splitlines() if ln.strip()]
        if any(ln.split("|", 1)[0].strip() == name for ln in lines):
            return {"success": False, "msg": "设备已存在：%s" % name}
        self._devices = "\n".join(lines + [line])
        self.__update_config()
        return {"success": True, "msg": "添加设备：%s" % name, "devices": self._devices}

    # ---------- 页面 ----------
    def get_page(self) -> List[dict]:
        devices = self.__parse_devices()
        rows = []
        for d in devices:
            online = self.__is_online(d["ip"])
            status = "🟢 在线" if online is True else ("⚪ 离线" if online is False else "—")
            rows.append({
                "name": d["name"],
                "mac": d["mac"],
                "broadcast": d["broadcast"],
                "ip": d["ip"] or "—",
                "status": status,
            })
        token = settings.API_TOKEN or ""
        params = "?apikey=%s" % token if token else ""
        add_form = [
            {
                "component": "VCard",
                "props": {"variant": "outlined", "class": "pa-3 mb-3"},
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [
                                {"component": "VTextField", "props": {"model": "dev_name", "label": "名称", "placeholder": "如 书房电脑"}}]},
                            {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [
                                {"component": "VTextField", "props": {"model": "dev_mac", "label": "MAC", "placeholder": "AA:BB:CC:DD:EE:FF"}}]},
                            {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [
                                {"component": "VTextField", "props": {"model": "dev_broadcast", "label": "广播地址", "placeholder": "255.255.255.255"}}]},
                            {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [
                                {"component": "VTextField", "props": {"model": "dev_ip", "label": "IP（在线探测）", "placeholder": "192.168.1.66"}}]},
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {"component": "VCol", "props": {"cols": 12}, "content": [
                                {
                                    "component": "VBtn",
                                    "props": {"color": "primary", "prepend-icon": "mdi-plus", "block": True},
                                    "text": "添加设备",
                                    "events": {"click": {
                                        "api": "plugin/WakeOnLan/device_add" + params,
                                        "method": "get",
                                        "params": {"name": "dev_name", "mac": "dev_mac", "broadcast": "dev_broadcast", "ip": "dev_ip"}
                                    }}
                                }
                            ]}
                        ],
                    },
                    {
                        "component": "VCol", "props": {"cols": 12}, "content": [
                            {"component": "VAlert", "props": {"type": "info", "variant": "tonal", "dense": True},
                             "content": [{"component": "text", "text": "填写后点击添加设备，自动保存进插件配置。唤醒前请开启电脑 BIOS 的 Wake-on-LAN。"}]}
                        ]
                    }
                ]
            }
        ]
        if not devices:
            return add_form + [
                {
                    "component": "VCard",
                    "props": {"style": "padding: 16px;"},
                    "content": [
                        {
                            "component": "VAlert",
                            "props": {"type": "info", "variant": "tonal"},
                            "content": [{"component": "text", "text": "尚未配置设备，请到插件设置中按「名称|MAC地址|广播地址|IP」每行一个填写。"}],
                        }
                    ],
                }
            ]
        wake_btns = [
            {
                "component": "VBtn",
                "props": {"color": "primary", "block": True, "variant": "tonal", "prepend-icon": "mdi-power"},
                "text": "唤醒全部",
                "events": {"click": {"api": "plugin/WakeOnLan/wake_all" + params, "method": "get"}},
            },
        ]
        per_device = []
        for d in devices:
            per_device.append(
                {
                    "component": "VBtn",
                    "props": {"color": "success", "variant": "tonal", "prepend-icon": "mdi-power",
                              "size": "small", "class": "ma-1"},
                    "text": "唤醒: %s" % d["name"],
                    "events": {"click": {"api": "plugin/WakeOnLan/wake?name=%s%s" % (d["name"], params), "method": "get"}},
                }
            )
        return add_form + [
            {
                "component": "VRow",
                "content": [
                    {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": wake_btns},
                    {
                        "component": "VCol", "props": {"cols": 12, "md": 6},
                        "content": [
                            {
                                "component": "VAlert",
                                "props": {"type": "success", "variant": "tonal", "dense": True},
                                "content": [{"component": "text", "text": "设备在线状态基于 IP ping 探测；唤醒需要设备开启 Wake-on-LAN。"}],
                            }
                        ],
                    },
                ],
            },
            {
                "component": "VRow",
                "content": [
                    {"component": "VCol", "props": {"cols": 12}, "content": per_device},
                ],
            },
            {
                "component": "VCard",
                "props": {"variant": "outlined"},
                "content": [
                    {
                        "component": "VTable",
                        "props": {"hover": True},
                        "content": [
                            {
                                "component": "thead",
                                "content": [
                                    {
                                        "component": "tr",
                                        "content": [
                                            {"component": "th", "text": "设备名称"},
                                            {"component": "th", "text": "MAC 地址"},
                                            {"component": "th", "text": "广播地址"},
                                            {"component": "th", "text": "IP 地址"},
                                            {"component": "th", "text": "状态"},
                                            {"component": "th", "text": "操作"},
                                        ],
                                    }
                                ],
                            },
                            {
                                "component": "tbody",
                                "content": [
                                    {
                                        "component": "tr",
                                        "content": [
                                            {"component": "td", "text": row["name"]},
                                            {"component": "td", "text": row["mac"]},
                                            {"component": "td", "text": row["broadcast"]},
                                            {"component": "td", "text": row["ip"]},
                                            {"component": "td", "text": row["status"]},
                                            {"component": "td", "content": [
                                                {"component": "VBtn", "props": {"color": "error", "variant": "tonal", "size": "small", "prepend-icon": "mdi-delete"},
                                                 "text": "删除",
                                                 "events": {"click": {"api": "plugin/WakeOnLan/device_remove?name=" + _q(row["name"]) + params, "method": "get"}}},
                                            ]},
                                        ],
                                    }
                                    for row in rows
                                ],
                            },
                        ],
                    }
                ],
            },
        ]

    # ---------- 表单 ----------
    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        # 兼容旧 MP：返回 (form_list, dict)；新版只读 form_list
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
                        {"component": "VTextField", "props": {"model": "cron", "label": "定时自动唤醒 (cron)", "placeholder": "如 30 8 * * 1-5 工作日 8:30"}}]},
                    {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [
                        {"component": "VTextField", "props": {"model": "port", "label": "WOL 端口", "placeholder": "9"}}]},
                    {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [
                        {"component": "VSwitch", "props": {"model": "notify", "label": "发送站内通知"}}]},
                    {"component": "VCol", "props": {"cols": 12, "md": 12}, "content": [
                        {
                            "component": "VTextField",
                            "props": {
                                "model": "devices",
                                "label": "设备列表",
                                "type": "textarea",
                                "rows": 6,
                                "placeholder": "每行一个：设备名称|MAC地址|广播地址(可选)|IP地址(可选,用于在线状态)\n示例：书房电脑|AA:BB:CC:DD:EE:FF|192.168.1.255|192.168.1.66",
                            },
                        }]},
                ],
            }]
        }], {}