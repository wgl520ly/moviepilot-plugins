"""
晴辰云签到 (QtCoolCheckin) — MoviePilot V3 插件
自动签到 gpt.qt.cool，支持多账号，Playwright + ddddocr 解决滑动验证码
"""
import asyncio
import base64
import json
import logging
import re
import time
import traceback
from typing import Any, Dict, List, Tuple

from app.core.config import settings
from app.core.event import eventmanager
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import NotificationType

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None
    logger.warning("[QtCoolCheckin] playwright 未安装，签到功能不可用")

try:
    import ddddocr
except ImportError:
    ddddocr = None
    logger.warning("[QtCoolCheckin] ddddocr 未安装，验证码识别不可用")


class QtCoolCheckin(_PluginBase):
    # 插件基础信息
    plugin_name = "QtCoolCheckin"
    plugin_name_cn = "晴辰云签到"
    plugin_desc = "自动签到 gpt.qt.cool（晴辰云），Playwright + ddddocr 解决滑动验证码"
    plugin_version = "0.1.0"
    plugin_author = "wgl520ly"
    plugin_homepage = "https://github.com/wgl520ly/moviepilot-plugins"
    plugin_label = "工具"
    plugin_icon = "https://raw.githubusercontent.com/wgl520ly/moviepilot-plugins/main/icons/QtCoolCheckin.png"
    plugin_category = "签到"
    plugin_description = "自动签到晴辰云（gpt.qt.cool），支持多账号、滑动验证码自动识别、定时签到、站内通知"
    plugin_version_history = {
        "0.1.0": "首发：Playwright + ddddocr 自动签到晴辰云，多账号，滑动验证码自动识别"
    }

    # 插件配置
    _enabled: bool = False
    _cron: str = ""
    _keys: str = ""
    _notify: bool = True
    _onlyonce: bool = False
    _timeout: int = 60
    _scheduler = None

    def init_plugin(self, config: dict = None):
        if config:
            self._enabled = config.get("enabled", False)
            self._cron = config.get("cron", "0 8 * * *")
            self._keys = config.get("keys", "")
            self._notify = config.get("notify", True)
            self._onlyonce = bool(config.get("onlyonce", False))
            self._timeout = config.get("timeout", 60)

        # 注册定时任务
        self.stop_service()
        if self._enabled and self._cron:
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            try:
                self._scheduler.add_job(
                    self.do_checkin_all,
                    CronTrigger.from_crontab(self._cron),
                    id="qtcool_checkin",
                    replace_existing=True
                )
                if not self._scheduler.running:
                    self._scheduler.start()
                logger.info(f"[QtCoolCheckin] 定时签到已注册: {self._cron}")
            except Exception as e:
                logger.error(f"[QtCoolCheckin] 注册定时任务失败: {e}")
        if self._onlyonce:
            self._onlyonce = False
            logger.info("[QtCoolCheckin] 执行一次性签到...")
            try:
                self.do_checkin_all()
            except Exception as e:
                logger.error(f"[QtCoolCheckin] 一次性签到失败: {e}")

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
        return {
            "enabled": self._enabled,
            "cron": self._cron,
            "keys_count": len([k for k in self._keys.splitlines() if k.strip()]),
            "notify": self._notify,
            "onlyonce": self._onlyonce,
        }

    def get_page(self) -> List[dict]:
        token = settings.API_TOKEN or ""
        params = "?apikey=%s" % token if token else ""
        keys = [k.strip() for k in self._keys.splitlines() if k.strip()]
        return [
            {
                "component": "VCard",
                "props": {"variant": "outlined", "class": "pa-3 mb-3"},
                "content": [
                    {"component": "VAlert", "props": {"type": "info", "variant": "tonal"},
                     "content": [{"component": "text", "text": "Playwright + ddddocr 自动签到晴辰云（gpt.qt.cool），滑动验证码自动识别。"}]},
                    {"component": "VRow", "content": [
                        {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [
                            {"component": "VBtn", "props": {"color": "primary", "block": True, "variant": "tonal", "prepend-icon": "mdi-refresh"},
                             "text": "立即签到全部",
                             "events": {"click": {"api": "plugin/QtCoolCheckin/checkin" + params, "method": "get"}}}]},
                        {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [
                            {"component": "VBtn", "props": {"color": "info", "block": True, "variant": "tonal", "prepend-icon": "mdi-information"},
                             "text": "查看状态",
                             "events": {"click": {"api": "plugin/QtCoolCheckin/status" + params, "method": "get"}}}]},
                    ]},
                    {"component": "VCol", "props": {"cols": 12}, "content": [
                        {"component": "VAlert", "props": {"type": "success", "variant": "tonal", "dense": True},
                         "content": [{"component": "text", "text": "已配置 %d 个密钥 | 定时: %s | 通知: %s" % (len(keys), self._cron or "未设置", "开启" if self._notify else "关闭")}]},
                    ]},
                ],
            },
        ]

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
                        {"component": "VTextField", "props": {"model": "timeout", "label": "超时(秒)", "type": "number", "placeholder": "60"}}]},
                    {"component": "VCol", "props": {"cols": 12}, "content": [
                        {"component": "VTextarea", "props": {
                            "model": "keys",
                            "label": "API Key 列表（每行一个 sk-... 密钥）",
                            "placeholder": "sk-xxxxxxxxxxxxxxxxxxxxxxxx\nsk-yyyyyyyyyyyyyyyyyyyyyyyy",
                            "rows": 5,
                            "persistent-placeholder": True
                        }}]},
                ],
            }],
        }], {}

    def get_command(self) -> List[dict]:
        return [
            {"cmd": "/qtcheckin", "event": "system", "desc": "手动签到晴辰云（全部账号）", "category": "签到"},
            {"cmd": "/qtcheckin_status", "event": "system", "desc": "查看晴辰云签到状态", "category": "签到"},
        ]

    def get_api(self) -> List[dict]:
        return [
            {"path": "/checkin", "endpoint": self.api_checkin, "methods": ["GET"], "summary": "手动签到全部账号"},
            {"path": "/checkin_one", "endpoint": self.api_checkin_one, "methods": ["GET"], "summary": "签到指定账号（?key=sk-xxx）"},
            {"path": "/status", "endpoint": self.api_status, "methods": ["GET"], "summary": "查看签到状态"},
        ]

    # ==================== API ====================
    def api_checkin(self) -> dict:
        results = self.do_checkin_all()
        return {"success": True, "data": results}

    def api_checkin_one(self, key: str = "") -> dict:
        if not key:
            return {"success": False, "msg": "缺少 key 参数"}
        result = self.do_checkin_one(key)
        return {"success": result.get("success", False), "data": result}

    def api_status(self) -> dict:
        keys = [k.strip() for k in self._keys.splitlines() if k.strip()]
        return {
            "success": True,
            "data": {
                "enabled": self._enabled,
                "cron": self._cron,
                "keys_count": len(keys),
                "notify": self._notify,
            }
        }

    # ==================== 签到核心 ====================
    def do_checkin_all(self) -> list:
        """签到全部账号"""
        keys = [k.strip() for k in self._keys.splitlines() if k.strip()]
        if not keys:
            logger.warning("[QtCoolCheckin] 未配置任何 API Key")
            return [{"success": False, "msg": "未配置 API Key"}]

        results = []
        for key in keys:
            masked = key[:8] + "..." + key[-4:] if len(key) > 12 else key[:8] + "..."
            try:
                result = self.do_checkin_one(key)
                result["key_masked"] = masked
                results.append(result)
                logger.info(f"[QtCoolCheckin] {masked} 签到结果: {result.get('msg', '')}")
            except Exception as e:
                logger.error(f"[QtCoolCheckin] {masked} 签到异常: {e}")
                results.append({"success": False, "key_masked": masked, "msg": str(e)})

        # 发送通知
        if self._notify and results:
            success_count = sum(1 for r in results if r.get("success"))
            fail_count = len(results) - success_count
            lines = ["[晴辰云签到结果]\n"]
            for r in results:
                icon = "✅" if r.get("success") else "❌"
                lines.append(f"{icon} {r.get('key_masked', '?')}: {r.get('msg', '未知')}")
            lines.append(f"\n成功: {success_count} / 失败: {fail_count}")
            self.post_message(
                mtype=NotificationType.SiteMessage,
                title="晴辰云签到",
                text="\n".join(lines)
            )

        return results

    def do_checkin_one(self, key: str) -> dict:
        """用 Playwright 对单个账号执行签到"""
        if not async_playwright:
            return {"success": False, "msg": "playwright 未安装"}

        try:
            loop = asyncio.new_event_loop()
            result = loop.run_until_complete(self._async_checkin(key))
            loop.close()
            return result
        except Exception as e:
            logger.error(f"[QtCoolCheckin] 签到异常: {traceback.format_exc()}")
            return {"success": False, "msg": f"异常: {str(e)[:100]}"}


    async def _async_checkin(self, key: str) -> dict:
        """异步签到流程 — 纯 API 方式，不操作 UI"""
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage',
                    '--disable-web-security', '--dns-servers=8.8.8.8,8.8.4.4',
                ]
            )
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                locale='zh-CN'
            )
            page = await context.new_page()
            try:
                # 1. 打开页面获取 session cookie
                logger.info("[QtCoolCheckin] 打开签到页面...")
                await page.goto('https://gpt.qt.cool/checkin', wait_until='networkidle', timeout=30000)
                await page.wait_for_timeout(2000)

                # 2. 登录 — 直接调 API
                logger.info("[QtCoolCheckin] 登录...")
                login_result = await page.evaluate('''
                    async (key) => {
                        const r = await fetch('/portal/login', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({key})
                        });
                        return await r.json();
                    }
                ''', key)

                if login_result.get('code') != 0:
                    msg = login_result.get('message', '登录失败')
                    return {"success": False, "msg": f"登录失败: {msg}"}
                logger.info("[QtCoolCheckin] 登录成功")

                # 3. 检查今日签到状态
                status = await page.evaluate('''
                    async () => {
                        const r = await fetch('/portal/checkin/status');
                        return await r.json();
                    }
                ''')
                status_data = (status.get('data') or {})
                if status_data.get('checkedInToday'):
                    logger.info("[QtCoolCheckin] 今日已签到")
                    return {"success": True, "msg": "今日已签到"}

                # 4. 获取滑块验证码
                logger.info("[QtCoolCheckin] 获取滑块验证码...")
                captcha_resp = await page.evaluate('''
                    async () => {
                        const r = await fetch('/auth/captcha?mode=slider', {cache: 'no-store'});
                        return await r.json();
                    }
                ''')

                if captcha_resp.get('code') != 0:
                    return {"success": False, "msg": f"获取验证码失败: {captcha_resp.get('message', '')}"}

                captcha_data = captcha_resp.get('data', {})
                slider_id = captcha_data.get('id', '')
                bg_base64 = captcha_data.get('bg', '')
                piece_base64 = captcha_data.get('piece', '')
                piece_size = captcha_data.get('pieceSize', 52)
                target_y = captcha_data.get('y', 0)
                img_width = captcha_data.get('width', 320)

                if not bg_base64 or not piece_base64:
                    return {"success": False, "msg": "验证码图片为空"}

                # 5. 识别缺口位置
                slider_x = self._detect_gap(bg_base64, piece_base64, img_width, captcha_data.get('height', 180), target_y)
                if slider_x is None:
                    return {"success": False, "msg": "OCR 无法识别缺口位置"}

                logger.info(f"[QtCoolCheckin] 检测到缺口 X={slider_x}px (图片宽度={img_width}px)")

                # 6. 生成模拟拖动轨迹
                slider_track = self._gen_slider_track(slider_x)

                # 7. 提交签到
                logger.info(f"[QtCoolCheckin] 提交签到: sliderId={slider_id}, sliderX={slider_x}")
                checkin_result = await page.evaluate('''
                    async (body) => {
                        const r = await fetch('/portal/checkin', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify(body)
                        });
                        return await r.json();
                    }
                ''', {
                    "sliderId": slider_id,
                    "sliderX": slider_x,
                    "sliderTrack": slider_track,
                })

                logger.info(f"[QtCoolCheckin] 签到 API 响应: {json.dumps(checkin_result, ensure_ascii=False)[:300]}")

                if checkin_result.get('code') == 0:
                    data = checkin_result.get('data', {})
                    msg = data.get('message', '签到成功')
                    streak = data.get('streak', '')
                    milestone = data.get('isMilestone', False)
                    extra = f" 连续{streak}天" if streak else ""
                    if milestone:
                        extra += " ★ 里程碑"
                    return {"success": True, "msg": f"{msg}{extra}".strip()}
                else:
                    msg = checkin_result.get('message', '签到失败')
                    return {"success": False, "msg": f"签到失败: {msg}"}

            finally:
                await browser.close()

    def _detect_gap(self, bg_base64: str, piece_base64: str, img_width: int = 320, img_height: int = 180, target_y: int = 0) -> int:
        """识别背景图中的缺口 X 坐标：edge-based 优先，ddddocr 兜底"""
        import io as _io
        bg_data = bg_base64.split(',', 1)[-1] if ',' in bg_base64 else bg_base64
        piece_data = piece_base64.split(',', 1)[-1] if ',' in piece_base64 else piece_base64
        bg_bytes = base64.b64decode(bg_data)
        piece_bytes = base64.b64decode(piece_data)

        # === 方法 1：边缘匹配（最可靠） ===
        try:
            from PIL import Image
            import numpy as np

            bg_img = Image.open(_io.BytesIO(bg_bytes))
            piece_img = Image.open(_io.BytesIO(piece_bytes))

            bg_gray = np.array(bg_img.convert('L')).astype(float)
            piece_gray = np.array(piece_img.convert('L')).astype(float)
            alpha = np.array(piece_img.split()[-1]).astype(float) / 255.0

            # 计算 bg 二阶梯度（边缘检测）
            bg_edges = np.gradient(np.gradient(bg_gray, axis=1), axis=1)
            # 计算 piece 的边缘
            piece_edges_p = np.gradient(np.gradient(piece_gray, axis=1), axis=1)

            pw = piece_img.size[0]
            ph = piece_img.size[1]
            best_x, best_score = 0, -float('inf')

            for x in range(0, img_width - pw):
                y1 = min(target_y, img_height - ph)
                bg_region = bg_edges[y1:y1 + ph, x:x + pw]
                score = 0
                count = 0
                for pr in range(ph):
                    for pc in range(pw):
                        if alpha[pr, pc] > 0.5:
                            if abs(piece_edges_p[pr, pc]) > 30:
                                score += abs(bg_region[pr, pc])
                                count += 1
                if count > 0:
                    avg = score / count
                    if avg > best_score:
                        best_score = avg
                        best_x = x

            logger.info(f"[QtCoolCheckin] edge-detect: X={best_x}, score={best_score:.1f}")
            return max(0, best_x)
        except Exception as e:
            logger.warning(f"[QtCoolCheckin] edge-detect 失败: {e}")

        # === 方法 2：ddddocr slide_match 兜底 ===
        if ddddocr:
            try:
                ocr = ddddocr.DdddOcr(show_ad=False)
                result = ocr.slide_match(piece_bytes, bg_bytes)
                if result and 'target' in result:
                    x = int(result['target'][0])
                    logger.info(f"[QtCoolCheckin] ddddocr fallback: X={x}")
                    return max(0, x)
                elif result and 'target_x' in result:
                    return max(0, int(result['target_x']))
            except Exception as e:
                logger.warning(f"[QtCoolCheckin] ddddocr fallback 失败: {e}")

        return None

    def _gen_slider_track(self, target_x: int) -> str:
        """生成模拟人类拖动轨迹字符串"""
        import random
        points = []
        t = 0
        x = 0
        while x < target_x and t < 200:
            # 先快后慢
            speed = max(1, int((target_x - x) * 0.15))
            dx = min(speed, target_x - x)
            x += dx
            t += random.randint(8, 25)
            y_offset = random.randint(-2, 2)
            points.append(f"{t}:{x}:{400 + y_offset}")
        # 最后微调
        if x != target_x:
            t += random.randint(5, 15)
            points.append(f"{t}:{target_x}:{400 + random.randint(-1, 1)}")
        return ";".join(points[-80:])
