# -*- coding: utf-8 -*-
"""bilibilidl Android UI entry point.
Uses the repository's bili_api.py, login_manager.py and downloader.py interfaces.
Network work is performed off the Kivy UI thread; failures are surfaced in the UI.
"""
import os
import io
import re
import json
import time
import threading
import traceback
import urllib.parse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import requests
import qrcode
from kivy.app import App
from kivy.clock import Clock, mainthread
from kivy.metrics import dp
from kivy.utils import platform
from kivy.core.text import LabelBase
from kivy.core.image import Image as CoreImage
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.properties import NumericProperty
from kivy.uix.image import Image, AsyncImage
from kivy.uix.widget import Widget
from kivy.graphics import Color, RoundedRectangle, Ellipse, Line, Rectangle
from kivy.config import Config

# Force a light application theme: prevent Kivy/native controls inheriting dark defaults.
Config.set('graphics', 'clearcolor', '0.965, 0.961, 0.969, 1')

from bili_api import new_session, get_video_info, get_play_info, get_nav
from login_manager import qr_generate, qr_poll, validate_login
from downloader import download_video, preload_merger, DownloadError, safe_name

BASE_DIR = Path(__file__).resolve().parent
APP_NAME = 'bilibilidl'
FONT_REG = BASE_DIR / 'NotoSansCJKsc-Regular.ttf'
FONT_BOLD = BASE_DIR / 'NotoSansCJKsc-Bold.ttf'

PINK = (0.91, 0.20, 0.43, 1)
PALE = (1.0, 0.95, 0.97, 1)
WHITE = (1, 1, 1, 1)
INK = (0.10, 0.12, 0.17, 1)
GRAY = (0.42, 0.45, 0.51, 1)
BG = (0.965, 0.961, 0.969, 1)
BORDER = (0.89, 0.88, 0.91, 1)
RED = (0.78, 0.16, 0.22, 1)
GREEN = (0.12, 0.55, 0.32, 1)

FONT = 'Roboto'
FONT_BOLD_NAME = 'Roboto'

for _path, _name, _bold in (
    (FONT_REG, 'BiliCJK', False),
    (FONT_BOLD, 'BiliCJKBold', True)
):
    try:
        if _path.is_file():
            LabelBase.register(name=_name, fn_regular=str(_path))
            if _bold:
                FONT_BOLD_NAME = _name
            else:
                FONT = _name
    except Exception:
        pass


def safe_text(value, limit=300):
    try:
        return str(value if value is not None else '')[:limit]
    except Exception:
        return ''


def _verified_file(path):
    """只有文件真实存在且非空时才允许记录为成功。"""
    p = Path(str(path)).expanduser()
    if not p.is_file():
        raise RuntimeError('下载器返回的路径对应文件不存在：' + str(p))
    if p.stat().st_size <= 0:
        raise RuntimeError('文件大小为 0：' + str(p))
    return p


def _export_to_public_downloads(path):
    """导出到公共 Download 目录；失败时明确保留私有目录文件。"""
    source = _verified_file(path)
    if platform != 'android':
        return str(source)
    try:
        from jnius import autoclass
        PythonActivity = autoclass('org.kivy.android.PythonActivity')
        try:
            MediaMerger = autoclass('org.bilibilidl.MediaMerger')
        except Exception as exc:
            raise RuntimeError(
                '当前 APK 未包含 org.bilibilidl.MediaMerger，'
                '无法导出到公共 Download 目录'
            ) from exc
        result = str(MediaMerger.exportToDownloads(
            PythonActivity.mActivity, str(source.resolve()), source.name
        ))
        if result.startswith('content://'):
            return result
        raise RuntimeError(result or '导出接口没有返回成功地址')
    except Exception as exc:
        raise RuntimeError(
            '下载文件已生成，但公共目录导出失败。原文件仍保存在应用私有目录：'
            + str(source) + '。原因：' + safe_text(exc, 180)
        )


def fmt_duration(value):
    try:
        n = max(0, int(float(value or 0)))
        return '%02d:%02d:%02d' % (
            n // 3600, (n % 3600) // 60, n % 60
        )
    except Exception:
        return '00:00'


def ui_label(text='', size=14, color=INK, height=None,
             bold=True, halign='left'):
    lab = Label(
        text=safe_text(text),
        font_name=FONT_BOLD_NAME,
        bold=True,
        font_size=dp(size),
        color=color,
        halign=halign,
        valign='middle'
    )
    if height is not None:
        lab.size_hint_y = None
        lab.height = dp(height)
    lab.bind(
        size=lambda obj, val: setattr(
            obj, 'text_size', (max(1, val[0]), max(1, val[1]))
        )
    )
    return lab


class Card(BoxLayout):
    def __init__(self, bg=WHITE, radius=dp(18), **kwargs):
        super().__init__(**kwargs)
        self.orientation = kwargs.get('orientation', 'vertical')
        self.padding = kwargs.get('padding', dp(16))
        self.spacing = kwargs.get('spacing', dp(10))
        self._card_radius = radius
        with self.canvas.before:
            Color(0.36, 0.22, 0.29, 0.055)
            self._shadow = RoundedRectangle(
                pos=(self.x, self.y - dp(2)), size=self.size,
                radius=[radius + dp(1)]
            )
            Color(*bg)
            self._rect = RoundedRectangle(
                pos=self.pos, size=self.size, radius=[radius]
            )
        with self.canvas.after:
            Color(0.87, 0.84, 0.87, 0.72)
            self._edge = Line(
                rounded_rectangle=(self.x, self.y, self.width, self.height, radius),
                width=0.65
            )
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        self._shadow.pos = (self.x, self.y - dp(2))
        self._shadow.size = self.size
        self._rect.pos = self.pos
        self._rect.size = self.size
        self._edge.rounded_rectangle = (self.x, self.y, self.width, self.height, self._card_radius)


class PinkButton(Button):
    def __init__(self, bg=PINK, fg=WHITE, **kwargs):
        super().__init__(**kwargs)
        self.background_normal = ''
        self.background_down = ''
        self.background_color = (0, 0, 0, 0)
        self.color = fg
        self.font_name = FONT_BOLD_NAME
        self.font_size = dp(14)
        self.bold = True
        with self.canvas.before:
            Color(*bg)
            self._rect = RoundedRectangle(
                pos=self.pos, size=self.size, radius=[dp(18)]
            )
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        self._rect.pos = self.pos
        self._rect.size = self.size


class PinkInput(TextInput):
    """High-contrast input with a visible outline and reliable touch focus."""
    def __init__(self, **kwargs):
        kwargs.setdefault('multiline', False)
        kwargs.setdefault('write_tab', False)
        super().__init__(**kwargs)
        self.background_normal = ''
        self.background_active = ''
        self.background_color = (0, 0, 0, 0)
        self.foreground_color = INK
        self.hint_text_color = (0.48, 0.49, 0.54, 1)
        self.cursor_color = PINK
        self.selection_color = (0.98, 0.68, 0.78, 0.75)
        self.font_name = FONT_BOLD_NAME
        self.bold = True
        self.font_size = dp(15)
        self.padding = [dp(14), dp(12), dp(14), dp(10)]
        self.size_hint_y = None
        self.height = dp(50)
        with self.canvas.before:
            Color(1, 1, 1, 1)
            self._input_bg = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(14)])
            self._focus_color = Color(*BORDER)
            self._input_edge = Line(rounded_rectangle=(self.x, self.y, self.width, self.height, dp(14)), width=1.15)
        self.bind(pos=self._sync_input, size=self._sync_input, focus=self._sync_focus)
        self._sync_focus()

    def _sync_input(self, *_):
        self._input_bg.pos = self.pos
        self._input_bg.size = self.size
        self._input_edge.rounded_rectangle = (self.x, self.y, self.width, self.height, dp(14))

    def _sync_focus(self, *_):
        self._focus_color.rgba = PINK if self.focus else BORDER
        self._input_edge.width = 1.7 if self.focus else 1.15

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            self.focus = True
        return super().on_touch_down(touch)


class PinkProgressBar(Widget):
    """Thick rounded pink progress bar; never uses Kivy's default blue style."""
    value = NumericProperty(0)
    max = NumericProperty(100)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.size_hint_y = None
        self.height = dp(18)
        with self.canvas:
            Color(1.0, 0.82, 0.88, 1)
            self._track = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(9)])
            Color(*PINK)
            self._fill = RoundedRectangle(pos=self.pos, size=(0, self.height), radius=[dp(9)])
        self.bind(pos=self._sync, size=self._sync, value=self._sync, max=self._sync)
        self._sync()

    def _sync(self, *_):
        self._track.pos = self.pos
        self._track.size = self.size
        ratio = 0 if self.max <= 0 else max(0.0, min(1.0, float(self.value) / float(self.max)))
        self._fill.pos = self.pos
        self._fill.size = (self.width * ratio, self.height)


class SafeAvatar(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(
            orientation='vertical',
            size_hint=(None, None),
            size=(dp(40), dp(40)),
            **kwargs
        )
        with self.canvas.before:
            Color(*PALE)
            self._circle = Ellipse(pos=self.pos, size=self.size)
        self.bind(pos=self._sync, size=self._sync)
        self.img = AsyncImage(allow_stretch=True, keep_ratio=True)
        self.add_widget(self.img)

    def _sync(self, *_):
        self._circle.pos = self.pos
        self._circle.size = self.size

    @mainthread
    def set_url(self, url):
        url = safe_text(url, 2048).strip()
        if url.startswith('//'):
            url = 'https:' + url
        if not url.startswith(('https://', 'http://')):
            url = ''
        try:
            self.img.source = url
        except Exception:
            self.img.source = ''


class ChoicePopup(Popup):
    def __init__(self, title, options, selected, callback, **kwargs):
        super().__init__(
            title=title,
            size_hint=(.88, .78),
            auto_dismiss=True,
            **kwargs
        )
        self.background = ''
        self.background_color = WHITE
        self.title_color = INK
        self.separator_color = PALE
        self.title_font = FONT_BOLD_NAME
        self.callback = callback
        root = BoxLayout(
            orientation='vertical',
            padding=dp(12),
            spacing=dp(8)
        )
        sc = ScrollView(do_scroll_x=False)
        items = BoxLayout(
            orientation='vertical',
            size_hint_y=None,
            spacing=dp(7)
        )
        items.bind(minimum_height=items.setter('height'))
        for i, item in enumerate(options):
            b = PinkButton(
                text=safe_text(item, 180),
                bg=PINK if i == selected else PALE,
                fg=WHITE if i == selected else INK,
                size_hint_y=None,
                height=dp(46)
            )
            b.bind(on_release=lambda _b, idx=i: self._choose(idx))
            items.add_widget(b)
        sc.add_widget(items)
        root.add_widget(sc)
        close = PinkButton(
            text='关闭',
            bg=(.72, .72, .75, 1),
            size_hint_y=None,
            height=dp(44)
        )
        close.bind(on_release=lambda *_: self.dismiss())
        root.add_widget(close)
        self.content = root

    def _choose(self, idx):
        try:
            self.callback(idx)
        finally:
            self.dismiss()


class QRMatrixWidget(Widget):
    """Render QR modules directly with Kivy canvas; does not require Pillow."""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.matrix = None
        self._rects = []
        self.bind(pos=self._redraw, size=self._redraw)

    def set_matrix(self, matrix):
        self.matrix = [list(row) for row in matrix]
        self._redraw()

    def _redraw(self, *_):
        self.canvas.clear()
        self._rects = []
        if not self.matrix:
            return
        rows = len(self.matrix)
        cols = max((len(row) for row in self.matrix), default=0)
        if not rows or not cols:
            return
        side = min(self.width, self.height)
        cell = side / max(rows, cols)
        ox = self.x + (self.width - cell * cols) / 2
        oy = self.y + (self.height - cell * rows) / 2
        with self.canvas:
            Color(1, 1, 1, 1)
            Rectangle(pos=self.pos, size=self.size)
            Color(0, 0, 0, 1)
            for r, row in enumerate(self.matrix):
                for c, dark in enumerate(row):
                    if dark:
                        self._rects.append(Rectangle(
                            pos=(ox + c * cell, oy + (rows - 1 - r) * cell),
                            size=(cell + 0.15, cell + 0.15)
                        ))


class LoginPopup(Popup):
    def __init__(self, app, **kwargs):
        super().__init__(
            title='B站授权登录',
            size_hint=(.9, .82),
            auto_dismiss=True,
            **kwargs
        )
        self.background = ''
        self.background_color = WHITE
        self.title_color = INK
        self.separator_color = PALE
        self.title_font = FONT_BOLD_NAME
        self.app_ref = app
        self.qr_key = None
        self.stop_event = threading.Event()
        root = BoxLayout(
            orientation='vertical',
            padding=dp(14),
            spacing=dp(10)
        )
        self.status = ui_label(
            '可生成二维码后使用 B 站 App 扫码登录',
            13, GRAY, 44, halign='center'
        )
        qr_shell = BoxLayout(orientation='vertical', size_hint_y=None, height=dp(280), padding=dp(8))
        with qr_shell.canvas.before:
            Color(1, 1, 1, 1)
            qr_shell._qr_bg = RoundedRectangle(pos=qr_shell.pos, size=qr_shell.size, radius=[dp(14)])
        qr_shell.bind(pos=lambda obj, *_: setattr(obj._qr_bg, 'pos', obj.pos), size=lambda obj, *_: setattr(obj._qr_bg, 'size', obj.size))
        self.qr_image = QRMatrixWidget(size_hint=(1, 1))
        qr_shell.add_widget(self.qr_image)
        direct = PinkButton(
            text='打开 B 站登录（优先 App）',
            size_hint_y=None,
            height=dp(48)
        )
        direct.bind(on_release=self.open_bili_login)
        make = PinkButton(
            text='使用扫码登录',
            size_hint_y=None,
            height=dp(48)
        )
        make.bind(on_release=self.generate)
        close = PinkButton(
            text='关闭',
            bg=(.72, .72, .75, 1),
            size_hint_y=None,
            height=dp(44)
        )
        close.bind(on_release=lambda *_: self.dismiss())
        root.add_widget(self.status)
        instruction = ui_label('直接登录：尝试将 B 站登录确认链接交给 B 站 App；若未进入确认页，请使用扫码登录。\n扫码登录：使用 B 站 App 扫码并确认授权。', 12, INK, 58, halign='center')
        root.add_widget(instruction)
        root.add_widget(direct)
        root.add_widget(qr_shell)
        root.add_widget(make)
        root.add_widget(close)
        self.content = root
        self.bind(on_dismiss=lambda *_: self.stop_event.set())
        self.bind(on_open=lambda *_: Clock.schedule_once(lambda _dt: self.generate(), 0.15))

    def open_bili_login(self, *_):
        """生成真实登录二维码，并尝试让 Android/B 站 App 处理其登录链接。

        不启动 B 站首页，也不自动退回浏览器。B 站 App 是否接管链接由系统
        的 App 链接配置决定；若未接管，用户仍可在本弹窗中扫码登录。
        """
        self.generate(open_in_app=True)

    def generate(self, *_args, open_in_app=False):
        self.stop_event.set()
        self.stop_event = threading.Event()
        self.status.text = '正在获取二维码…'
        stop = self.stop_event

        def work():
            try:
                session = new_session()
                url, key = qr_generate(session)
                qr = qrcode.QRCode(
                    box_size=10,
                    border=4,
                    error_correction=qrcode.constants.ERROR_CORRECT_M,
                )
                qr.add_data(url)
                qr.make(fit=True)
                matrix = qr.get_matrix()

                @mainthread
                def show():
                    if stop.is_set():
                        return
                    try:
                        # Draw modules using Kivy canvas: QR display no longer depends on Pillow.
                        self.qr_key = key
                        self.qr_image.set_matrix(matrix)
                        self.qr_image.opacity = 1
                        self.status.text = '请用 B 站 App 扫码确认登录'
                    except Exception as image_exc:
                        self.status.text = '二维码显示失败：' + safe_text(image_exc, 100)

                show()

                if open_in_app:
                    @mainthread
                    def launch_login_link():
                        if stop.is_set():
                            return
                        try:
                            if platform != 'android':
                                self.status.text = '当前设备不支持直接唤起 B 站 App，请使用二维码登录。'
                                return
                            from jnius import autoclass
                            PythonActivity = autoclass('org.kivy.android.PythonActivity')
                            Intent = autoclass('android.content.Intent')
                            Uri = autoclass('android.net.Uri')
                            activity = PythonActivity.mActivity
                            intent = Intent(Intent.ACTION_VIEW, Uri.parse(url))
                            activity.startActivity(intent)
                            self.status.text = (
                                '已尝试打开 B 站登录确认链接；如果没有出现确认页，'
                                '请返回本应用使用二维码登录。'
                            )
                        except Exception as exc:
                            self.status.text = (
                                '未能打开 B 站确认页面，请使用下方二维码登录。'
                            )

                    Clock.schedule_once(lambda _dt: launch_login_link(), 0.1)

                while not stop.is_set():
                    code, msg = qr_poll(session, key)
                    if code == 0:
                        cookie, profile = validate_login(session)

                        @mainthread
                        def success():
                            if not stop.is_set():
                                self.app_ref.save_cookie_local(cookie, profile)
                                self.status.text = '登录成功'
                                Clock.schedule_once(
                                    lambda _dt: self.dismiss(), 1.0
                                )
                        success()
                        return
                    if code == 86038:
                        @mainthread
                        def expired():
                            if not stop.is_set():
                                self.status.text = '二维码已过期，请重新生成'
                        expired()
                        return
                    stop.wait(1.8)
            except Exception as exc:
                msg = safe_text(exc, 180)

                @mainthread
                def fail():
                    if not stop.is_set():
                        self.status.text = '登录失败：' + msg
                fail()

        threading.Thread(
            target=work, daemon=True, name='bili-login'
        ).start()


class Toast(Popup):
    """Small transient-style message used for ordinary feedback."""
    def __init__(self, message, **kwargs):
        super().__init__(title='', separator_height=0, size_hint=(.88, None),
                         height=dp(72), auto_dismiss=True, **kwargs)
        self.background = ''
        self.background_color = (0, 0, 0, 0)
        self.content = Card(bg=PALE, radius=dp(18), padding=[dp(14), dp(8), dp(14), dp(8)])
        self.content.add_widget(ui_label(safe_text(message, 160), 13, INK, 46, halign='center'))
        Clock.schedule_once(lambda _dt: self.dismiss(), 1.8)


_active_toast = None
_toast_lock = threading.RLock()


def show_toast(message):
    """Toast 采用单实例策略，连续提示时替换旧提示，避免弹窗堆叠。"""
    def _show(_dt=0):
        global _active_toast
        try:
            with _toast_lock:
                if _active_toast is not None:
                    try:
                        _active_toast.dismiss()
                    except Exception:
                        pass
                _active_toast = Toast(message)
                _active_toast.open()
        except Exception:
            pass
    try:
        Clock.schedule_once(_show, 0)
    except Exception:
        pass


def apply_screen_background(screen, color=BG):
    """Paint every page explicitly so Android never shows the default black canvas."""
    with screen.canvas.before:
        Color(*color)
        screen._page_background = RoundedRectangle(pos=screen.pos, size=screen.size, radius=[0])
    def sync(*_):
        screen._page_background.pos = screen.pos
        screen._page_background.size = screen.size
    screen.bind(pos=sync, size=sync)


def _fetch_cover_bytes(url, session=None, cache_dir=None, timeout=(6, 12)):
    """Fetch Bilibili CDN covers with browser-like headers, bounded retries and disk cache."""
    url = safe_text(url, 2048).strip()
    if url.startswith('//'):
        url = 'https:' + url
    if not url.startswith(('https://', 'http://')):
        return None
    cache_path = None
    try:
        if cache_dir:
            cache_root = Path(cache_dir)
            cache_root.mkdir(parents=True, exist_ok=True)
            cache_path = cache_root / (hashlib.sha256(url.encode('utf-8')).hexdigest() + '.img')
            if cache_path.is_file() and 0 < cache_path.stat().st_size <= 20 * 1024 * 1024:
                return cache_path.read_bytes()
    except Exception:
        cache_path = None

    headers = {
        'Referer': 'https://www.bilibili.com/',
        'Origin': 'https://www.bilibili.com',
        'User-Agent': ('Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 '
                       '(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36'),
        'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.7',
    }
    requester = session if session is not None else requests
    for attempt in range(3):
        try:
            response = requester.get(url, timeout=timeout, headers=headers)
            if response.status_code == 403:
                # One clean-session retry can help when the API session has stale headers.
                if attempt == 0 and session is not None:
                    requester = requests
                    continue
                return None
            response.raise_for_status()
            raw = response.content
            if not raw or len(raw) > 20 * 1024 * 1024:
                return None
            # Reject HTML error pages that sometimes arrive with HTTP 200.
            prefix = raw[:256].lstrip().lower()
            if prefix.startswith(b'<!doctype html') or prefix.startswith(b'<html'):
                return None
            if cache_path is not None:
                try:
                    cache_path.write_bytes(raw)
                except Exception:
                    pass
            return raw
        except Exception:
            if attempt >= 2:
                break
            time.sleep(0.25 * (attempt + 1))
    return None


def _image_texture_from_bytes(raw, max_size=(900, 600)):
    """Decode a still image; use Pillow when present, otherwise Kivy's providers."""
    try:
        from PIL import Image as PILImage
        with PILImage.open(io.BytesIO(raw)) as source:
            source.seek(0)
            im = source.convert('RGB')
            im.thumbnail(max_size)
        buf = io.BytesIO()
        im.save(buf, format='JPEG', quality=86)
        buf.seek(0)
        return CoreImage(buf, ext='jpg').texture
    except ImportError:
        # Kivy's built-in providers can decode common PNG/JPEG formats without Pillow.
        return CoreImage(io.BytesIO(raw)).texture


def _set_image_from_bytes(widget, raw, max_size=(900, 600)):
    """Display animated GIF/WebP when Pillow is installed; gracefully show stills otherwise."""
    from kivy.clock import Clock
    previous = getattr(widget, '_cover_anim_event', None)
    if previous is not None:
        try:
            previous.cancel()
        except Exception:
            pass
        widget._cover_anim_event = None

    try:
        from PIL import Image as PILImage
    except ImportError:
        # No Pillow: don't fail the card. Let Kivy decode static formats directly.
        widget.texture = CoreImage(io.BytesIO(raw)).texture
        return 1

    frames, durations = [], []
    with PILImage.open(io.BytesIO(raw)) as source:
        total = int(getattr(source, 'n_frames', 1) or 1)
        indices = list(range(min(total, 36)))
        if total > 36:
            indices = [round(i * (total - 1) / 35) for i in range(36)]
        for idx in indices:
            try:
                source.seek(idx)
                frame = source.convert('RGBA')
                frame.thumbnail(max_size)
                # Encode PNG to preserve alpha rather than flattening every frame to JPEG.
                buf = io.BytesIO()
                frame.save(buf, format='PNG', optimize=True)
                buf.seek(0)
                frames.append(CoreImage(buf, ext='png').texture)
                durations.append(max(0.08, min(1.0, float(source.info.get('duration', 100)) / 1000.0)))
            except Exception:
                continue

    if not frames:
        # Last-chance direct decode; this covers providers that support the source format.
        widget.texture = CoreImage(io.BytesIO(raw)).texture
        return 1
    widget.texture = frames[0]
    if len(frames) > 1:
        state = {'index': 0, 'elapsed': 0.0}
        def advance(dt):
            if not getattr(widget, 'parent', None):
                return True
            state['elapsed'] += dt
            if state['elapsed'] >= durations[state['index']]:
                state['elapsed'] = 0.0
                state['index'] = (state['index'] + 1) % len(frames)
                widget.texture = frames[state['index']]
        widget._cover_anim_event = Clock.schedule_interval(advance, 1.0 / 30.0)
    return len(frames)


def _open_path_with_android(path, is_directory=False):
    """Open exported media through a shareable content URI, or open the public app folder."""
    target = Path(str(path)).expanduser()
    if platform != 'android':
        try:
            import webbrowser
            if target.exists() and not is_directory:
                return False
            return False
        except Exception:
            return False
    try:
        from jnius import autoclass
        Intent = autoclass('android.content.Intent')
        Uri = autoclass('android.net.Uri')
        PythonActivity = autoclass('org.kivy.android.PythonActivity')
        activity = PythonActivity.mActivity
        if is_directory:
            # Open the shared Download/bilibilidl folder in Android's document UI.
            uri = Uri.parse('content://com.android.externalstorage.documents/root/primary%3ADownload%2Fbilibilidl')
            intent = Intent(Intent.ACTION_VIEW)
            intent.setDataAndType(uri, 'vnd.android.document/directory')
            if intent.resolveActivity(activity.getPackageManager()) is None:
                # Fallback: open the general Downloads screen.
                intent = Intent(Intent.ACTION_VIEW)
                intent.setDataAndType(Uri.parse('content://com.android.externalstorage.documents/root/primary%3ADownload'), 'vnd.android.document/directory')
            if intent.resolveActivity(activity.getPackageManager()) is None:
                return False
            activity.startActivity(intent)
            return True

        if not target.is_file():
            return False
        # MediaMerger uses MediaStore/Downloads and returns a public content:// URI.
        # App-private file:// URIs cannot be opened by other apps on modern Android.
        try:
            MediaMerger = autoclass('org.bilibilidl.MediaMerger')
            uri_string = str(MediaMerger.exportToDownloads(activity, str(target.resolve()), target.name))
            if not uri_string.startswith('content://'):
                return False
            uri = Uri.parse(uri_string)
        except Exception:
            return False
        intent = Intent(Intent.ACTION_VIEW)
        intent.setDataAndType(uri, _mime_for_file(target))
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        if intent.resolveActivity(activity.getPackageManager()) is None:
            # Let Android show the chooser for installed compatible viewers.
            intent = Intent(Intent.ACTION_SEND)
            intent.setType(_mime_for_file(target))
            intent.putExtra(Intent.EXTRA_STREAM, uri)
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            intent = Intent.createChooser(intent, '查看文件')
        activity.startActivity(intent)
        return True
    except Exception as exc:
        print('[bilibilidl] open path failed:', safe_text(exc, 180))
        return False


def _mime_for_file(path):
    ext = Path(str(path)).suffix.lower()
    return {
        '.mp4': 'video/mp4', '.mkv': 'video/x-matroska', '.webm': 'video/webm',
        '.mp3': 'audio/mpeg', '.m4a': 'audio/mp4', '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp',
        '.gif': 'image/gif', '.xml': 'application/xml', '.srt': 'text/plain',
        '.ass': 'text/plain', '.txt': 'text/plain'
    }.get(ext, '*/*')


class DownloadedScreen(Screen):
    def __init__(self, app, **kwargs):
        super().__init__(**kwargs)
        self.app_ref = app
        apply_screen_background(self)
        self.files_box = BoxLayout(orientation='vertical', spacing=dp(10), size_hint_y=None)
        self.files_box.bind(minimum_height=self.files_box.setter('height'))
        root = BoxLayout(orientation='vertical', padding=[dp(17), dp(16), dp(17), dp(10)], spacing=dp(12))
        root.add_widget(ui_label('已下载', 25, INK, 38, bold=True))
        root.add_widget(ui_label('保存在本机的文件 · 不会因返回页面而清除', 12, GRAY, 25))
        refresh = PinkButton(text='刷新文件列表  ↻', bg=PALE, fg=PINK, size_hint_x=1, size_hint_y=None, height=dp(44))
        refresh.bind(on_release=lambda *_: self.refresh_files())
        root.add_widget(refresh)
        sc = ScrollView(do_scroll_x=False, bar_width=dp(3))
        sc.add_widget(self.files_box)
        root.add_widget(sc)
        self.add_widget(root)

    def refresh_files(self):
        self.files_box.clear_widgets()
        folder = Path(self.app_ref.user_data_dir) / 'downloads'
        try:
            ignored_suffixes = ('.tmp', '.part', '.partial', '.download', '.crdownload')
            candidates = []
            if folder.exists():
                for item in folder.iterdir():
                    try:
                        if (not item.is_file() or item.name.startswith('.')
                                or item.name.lower().endswith(ignored_suffixes)):
                            continue
                        stat = item.stat()
                        if stat.st_size > 0:
                            candidates.append((stat.st_mtime, item, stat.st_size))
                    except (OSError, PermissionError):
                        continue
            candidates.sort(key=lambda entry: entry[0], reverse=True)
            raw_files = [(item, size, mtime) for mtime, item, size in candidates[:500]]
            video_exts = {'.mp4', '.mkv', '.webm', '.mov', '.avi', '.flv', '.ts'}
            image_exts = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
            video_stems = {item.stem.lower() for item, _, _ in raw_files if item.suffix.lower() in video_exts}
            def associated_cover(item):
                if item.suffix.lower() not in image_exts:
                    return False
                stem = item.stem.lower()
                base = re.sub(r'([_-]cover|[.]cover|_封面)$', '', stem, flags=re.I)
                base = re.sub(r'([_-]p\d+)$', '', base, flags=re.I)
                if stem.endswith(('_cover', '-cover', '.cover', '_封面')):
                    return True
                return base in video_stems or stem in video_stems
            files = [(item, size, mtime) for item, size, mtime in raw_files if not associated_cover(item)]
        except Exception:
            files = []

        if not files:
            empty = Card(bg=WHITE, radius=dp(22), padding=[dp(18), dp(22), dp(18), dp(22)], spacing=dp(8), size_hint_y=None, height=dp(150))
            empty.add_widget(ui_label('这里还空空的', 18, INK, 32, halign='center'))
            empty.add_widget(ui_label('解析并下载后，文件会显示在这里。', 12, GRAY, 44, halign='center'))
            self.files_box.add_widget(empty)
            return

        for f, size, mtime in files:
            size_text = ('%.1f MB' % (size / 1048576)) if size >= 1048576 else ('%.0f KB' % (size / 1024))
            # Keep every card a fixed, generous height and use a horizontal layout to prevent overlap.
            card = Card(bg=WHITE, radius=dp(17), padding=[dp(12), dp(11), dp(12), dp(11)], spacing=dp(10),
                        orientation='horizontal', size_hint_y=None, height=dp(132))
            thumb = Image(size_hint=(None, 1), width=dp(104), allow_stretch=True, keep_ratio=True)
            # Match the sidecar cover saved when the video download completes.
            stem = f.stem
            image_exts = ('.jpg', '.jpeg', '.png', '.webp', '.gif')
            base_stem = re.sub(r'([_-]P\d+)$', '', stem, flags=re.I)
            cover_candidates = []
            for candidate_stem in (stem, base_stem):
                cover_candidates.extend(
                    f.with_name(candidate_stem + suffix + ext)
                    for suffix in ('_cover', '-cover', '.cover', '_封面')
                    for ext in image_exts
                )
                cover_candidates.extend(f.with_name(candidate_stem + ext) for ext in image_exts)
            cover_path = next((candidate for candidate in cover_candidates
                               if candidate.is_file() and candidate != f), None)
            if cover_path is None:
                # Some older downloads only have a BVID-keyed cache entry.
                try:
                    cache_dir = Path(self.app_ref.user_data_dir) / 'cover_cache'
                    if cache_dir.exists():
                        prefix = re.match(r'(BV[0-9A-Za-z]+)', stem)
                        prefix = prefix.group(1) if prefix else ''
                        cached = sorted(
                            (x for x in cache_dir.iterdir()
                             if x.is_file() and x.suffix.lower() in image_exts
                             and (not prefix or x.stem.startswith(prefix))),
                            key=lambda x: x.stat().st_mtime, reverse=True
                        )
                        cover_path = cached[0] if cached else None
                except Exception:
                    cover_path = None
            if cover_path:
                try:
                    # Local sidecar/cache images may be WebP or GIF, which Android's
                    # Kivy image provider may not decode consistently. Normalize via Pillow.
                    _set_image_from_bytes(thumb, cover_path.read_bytes(), (360, 360))
                except Exception:
                    thumb.source = str(cover_path)
                    thumb.reload()
            # Thumbnail is display-only; action buttons own all file-open interactions.
            card.add_widget(thumb)

            info = BoxLayout(orientation='vertical', spacing=dp(3), size_hint_x=1)
            info.add_widget(ui_label(f.name, 12, INK, 38, bold=True))
            info.add_widget(ui_label(size_text + '  ·  ' + time.strftime('%Y-%m-%d %H:%M', time.localtime(mtime)), 10, GRAY, 20))
            actions = BoxLayout(orientation='horizontal', spacing=dp(7), size_hint_y=None, height=dp(36))
            folder_btn = PinkButton(text='打开目录', bg=PALE, fg=PINK, size_hint_x=1, size_hint_y=None, height=dp(36), font_size=dp(12))
            folder_btn.bind(on_release=lambda _b, path=str(f.parent): self._open_location(path, True))
            file_btn = PinkButton(text='查看文件', bg=PINK, fg=WHITE, size_hint_x=1, size_hint_y=None, height=dp(36), font_size=dp(12))
            file_btn.bind(on_release=lambda _b, path=str(f): self._open_location(path, False))
            actions.add_widget(folder_btn)
            actions.add_widget(file_btn)
            info.add_widget(actions)
            card.add_widget(info)
            self.files_box.add_widget(card)

    def _open_location(self, path, is_directory=False):
        if not _open_path_with_android(path, is_directory=is_directory):
            # No dark path popup: report the action failure using the app's normal toast.
            try:
                self.app_ref.show_toast('无法打开目录，请检查文件管理器或文件访问权限。' if is_directory else '无法打开文件，请检查文件类型或文件访问权限。')
            except Exception:
                pass


class ProfileScreen(Screen):
    """Profile page with one scroll container; no floating header over content."""
    def __init__(self, app, **kwargs):
        super().__init__(**kwargs)
        self.app_ref = app
        apply_screen_background(self)
        self._page = BoxLayout(orientation='vertical', spacing=0)
        self._scroll = ScrollView(
            do_scroll_x=False,
            do_scroll_y=True,
            bar_width=dp(3),
            scroll_type=['content', 'bars'],
            bar_color=PINK,
            bar_inactive_color=(0.95, 0.72, 0.81, 0.45),
        )
        self.body = BoxLayout(
            orientation='vertical',
            size_hint_y=None,
            spacing=dp(12),
            padding=[dp(17), dp(16), dp(17), dp(22)],
        )
        self.body.bind(minimum_height=self.body.setter('height'))
        self._scroll.add_widget(self.body)
        self._page.add_widget(self._scroll)
        self.add_widget(self._page)
        self.refresh_profile()

    def refresh_profile(self):
        # Rebuild all page content inside the same ScrollView. Keeping the title in
        # the scrollable body prevents fixed headers/overlays from covering cards.
        old_scroll_y = getattr(self, '_scroll', None).scroll_y if getattr(self, '_scroll', None) else 1
        self.body.clear_widgets()
        self.body.add_widget(ui_label('我的', 25, INK, 38, bold=True))
        self.body.add_widget(ui_label('账号信息与本地下载概览', 12, GRAY, 25))
        profile = self.app_ref.profile_data if isinstance(self.app_ref.profile_data, dict) else {}
        name = safe_text(profile.get('uname') or '尚未登录', 40)
        uid = profile.get('mid') or profile.get('uid')

        account = Card(
            bg=WHITE, radius=dp(22), padding=[dp(17), dp(16), dp(17), dp(16)],
            spacing=dp(6), size_hint_y=None, height=dp(230),
        )
        avatar = Image(size_hint=(None, None), size=(dp(62), dp(62)),
                       allow_stretch=True, keep_ratio=True, pos_hint={'center_x': .5})
        account.add_widget(avatar)
        face_url = safe_text(profile.get('face') or '', 2048)
        if face_url.startswith('//'):
            face_url = 'https:' + face_url
        if face_url.startswith(('https://', 'http://')):
            def fetch_avatar(url=face_url, target=avatar):
                raw_avatar = _fetch_cover_bytes(url, cache_dir=Path(self.app_ref.user_data_dir) / 'cover_cache')
                if raw_avatar:
                    @mainthread
                    def apply_avatar():
                        try:
                            _set_image_from_bytes(target, raw_avatar, (220, 220))
                        except Exception:
                            target.texture = None
                    apply_avatar()
            threading.Thread(target=fetch_avatar, daemon=True, name='bili-avatar').start()
        else:
            account.add_widget(ui_label('登录后显示 B 站头像', 11, GRAY, 20, halign='center'))
        account.add_widget(ui_label(name, 19, INK, 32, bold=True, halign='center'))
        account.add_widget(ui_label(('UID ' + str(uid)) if uid else '登录 B 站以查看授权账号信息', 12, GRAY, 25, halign='center'))
        login = PinkButton(
            text='B站授权登录' if not self.app_ref.get_cookie() else '账号登录状态',
            bg=PALE, fg=PINK, size_hint_y=None, height=dp(44),
        )
        login.bind(on_release=lambda *_: LoginPopup(self.app_ref).open())
        account.add_widget(login)
        self.body.add_widget(account)

        folder = Path(self.app_ref.user_data_dir) / 'downloads'
        try:
            files = [x for x in folder.iterdir() if x.is_file() and x.stat().st_size > 0
                     and not x.name.startswith('.')
                     and not x.name.lower().endswith(('.tmp', '.part', '.partial', '.download', '.crdownload'))] if folder.exists() else []
            total = sum(x.stat().st_size for x in files)
        except Exception:
            files, total = [], 0
        size = ('%.2f GB' % (total / 1073741824)) if total >= 1073741824 else ('%.1f MB' % (total / 1048576))
        stats = Card(
            bg=WHITE, radius=dp(22), padding=[dp(15), dp(15), dp(15), dp(15)],
            spacing=dp(8), size_hint_y=None, height=dp(150),
        )
        stats.add_widget(ui_label('本地下载统计', 16, INK, 28, bold=True))
        row = BoxLayout(size_hint_y=None, height=dp(82), spacing=dp(8))
        for value, label in [(str(len(files)), '文件数量'), (size, '占用空间')]:
            tile = Card(bg=PALE, radius=dp(15), padding=[dp(8), dp(7), dp(8), dp(7)], spacing=dp(2), size_hint_y=1)
            tile.add_widget(ui_label(value, 17, PINK, 28, bold=True, halign='center'))
            tile.add_widget(ui_label(label, 11, GRAY, 22, halign='center'))
            row.add_widget(tile)
        stats.add_widget(row)
        self.body.add_widget(stats)

        settings = Card(
            bg=WHITE, radius=dp(22), padding=[dp(15), dp(14), dp(15), dp(14)],
            spacing=dp(7), size_hint_y=None, height=dp(150),
        )
        settings.add_widget(ui_label('应用信息', 16, INK, 28, bold=True))
        settings.add_widget(ui_label('bilibilidl · 本地下载工具', 13, GRAY, 24))
        settings.add_widget(ui_label('文件默认保存在应用本地目录；导出文件位于 Download/bilibilidl。', 12, GRAY, 45))
        repo_btn = PinkButton(text='项目 GitHub 仓库  ↗', bg=PALE, fg=PINK,
                              size_hint_y=None, height=dp(42))
        repo_btn.bind(on_release=lambda *_: self._open_repository())
        settings.add_widget(repo_btn)
        settings.height = dp(198)
        self.body.add_widget(settings)
        self.body.height = self.body.minimum_height
        self._scroll.scroll_y = min(1, max(0, old_scroll_y))


    def _open_repository(self):
        url = 'https://github.com/sxsbsxs/bilibilidl'
        try:
            if platform == 'android':
                from jnius import autoclass
                PythonActivity = autoclass('org.kivy.android.PythonActivity')
                Intent = autoclass('android.content.Intent')
                Uri = autoclass('android.net.Uri')
                intent = Intent(Intent.ACTION_VIEW, Uri.parse(url))
                PythonActivity.mActivity.startActivity(intent)
            else:
                import webbrowser
                webbrowser.open(url)
        except Exception:
            try:
                self.app_ref.show_toast('无法打开仓库，请检查浏览器是否可用。')
            except Exception:
                pass


class HomeScreen(Screen):
    MODES = [
        '视频 + 音频（MP4）',
        '封面图片（保留 GIF/WebP）',
        '弹幕 XML',
        '字幕 SRT'
    ]

    def __init__(self, app, **kwargs):
        super().__init__(**kwargs)
        self.app_ref = app
        self.info = None
        self.pages = []
        self.qualities = []
        self.qualities_by_page = {}
        self.quality_index = 0
        self.mode_index = 0
        self.selected_page = 0
        self.all_pages = False
        self.cover_bytes = None
        self.cover_url = ''
        self.busy = False
        self.cancel_event = threading.Event()
        self._parse_generation = 0
        self.auto_play_after_parse = False

        with self.canvas.before:
            Color(*BG)
            self._screen_bg = RoundedRectangle(pos=self.pos, size=self.size, radius=[0])
        self.bind(pos=self._sync_screen_bg, size=self._sync_screen_bg)

        outer = BoxLayout(orientation='vertical', spacing=dp(0), padding=[dp(17), dp(7), dp(17), dp(8)])
        header = BoxLayout(size_hint_y=None, height=dp(64), spacing=dp(10), padding=[dp(2), dp(1), dp(2), dp(3)])
        brand = BoxLayout(orientation='vertical', spacing=dp(0))
        brand.add_widget(ui_label('bilibilidl', 24, INK, 33, bold=True))
        brand.add_widget(ui_label('BILIBILI VIDEO TOOL  /  LOCAL', 9, GRAY, 20, bold=True))
        header.add_widget(brand)
        header.add_widget(Widget())
        self.account_btn = PinkButton(text='登录 B 站  ↗', bg=WHITE, fg=PINK, size_hint_x=None, width=dp(116), height=dp(42))
        self.account_btn.bind(on_release=self.open_login)
        header.add_widget(self.account_btn)
        outer.add_widget(header)

        sc = ScrollView(do_scroll_x=False, bar_width=dp(3), scroll_type=['bars', 'content'])
        content = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(12), padding=[dp(0), dp(5), dp(0), dp(24)])
        content.bind(minimum_height=content.setter('height'))

        # Hero / paste link card
        hero = Card(bg=WHITE, radius=dp(23), padding=[dp(18), dp(16), dp(18), dp(17)], spacing=dp(9), size_hint_y=None)
        hero.bind(minimum_height=hero.setter('height'))
        hero.add_widget(ui_label('VIDEO  /  DOWNLOADER', 10, PINK, 20, bold=True))
        hero.add_widget(ui_label('把喜欢的视频，\n认真保存下来。', 23, INK, 62, bold=True))
        hero.add_widget(ui_label('支持 BV 号、AV 号、视频链接与 b23.tv 短链', 12, GRAY, 24))
        input_shell = BoxLayout(size_hint_y=None, height=dp(58), padding=[dp(1), dp(1), dp(1), dp(1)])
        self.link_input = PinkInput(hint_text='点此输入或粘贴视频链接、BV 号、AV 号…', multiline=False, size_hint=(1, 1), height=dp(56))
        input_shell.add_widget(self.link_input)
        hero.add_widget(input_shell)
        self.parse_btn = PinkButton(text='解析视频  →', bg=PINK, fg=WHITE, size_hint_y=None, height=dp(50))
        self.parse_btn.bind(on_release=self.parse)
        hero.add_widget(self.parse_btn)
        self.auto_play_btn = PinkButton(
            text='解析后自动播放：关闭', bg=PALE, fg=INK,
            size_hint_y=None, height=dp(42)
        )
        self.auto_play_btn.bind(on_release=self.toggle_auto_play)
        hero.add_widget(self.auto_play_btn)
        content.add_widget(hero)

        # Status card
        status_card = Card(bg=WHITE, radius=dp(19), padding=[dp(16), dp(12), dp(16), dp(12)], spacing=dp(4), size_hint_y=None)
        status_card.bind(minimum_height=status_card.setter('height'))
        status_card.add_widget(ui_label('解析状态', 13, GRAY, 22, bold=True))
        self.result_label = ui_label('先粘贴视频链接并解析，封面、清晰度和分 P 会显示在下方。', 13, INK, 44)
        status_card.add_widget(self.result_label)
        content.add_widget(status_card)

        # Video info card with thumbnail and title
        video_card = Card(bg=WHITE, radius=dp(19), padding=[dp(14), dp(14), dp(14), dp(14)], spacing=dp(9), size_hint_y=None)
        video_card.bind(minimum_height=video_card.setter('height'))
        video_card.add_widget(ui_label('视频信息', 16, INK, 26, bold=True))
        # The preview area is also the anchor for the in-page WebView player.
        self.cover_preview = Image(size_hint_y=None, height=dp(202), allow_stretch=True, keep_ratio=True)
        video_card.add_widget(self.cover_preview)
        self.player_hint = ui_label('解析后可在此区域直接播放视频', 11, GRAY, 22)
        video_card.add_widget(self.player_hint)
        self._player_expanded = False
        self.title_label = ui_label('解析后显示视频标题', 18, INK, 54, bold=True)
        video_card.add_widget(self.title_label)
        self.owner_label = ui_label('UP 主 · 播放信息 · 视频时长', 12, GRAY, 28)
        video_card.add_widget(self.owner_label)
        content.add_widget(video_card)
        self.play_btn = PinkButton(text='在线播放（B站播放器）  ▶', bg=PALE, fg=PINK,
                                   size_hint_y=None, height=dp(48), disabled=True)
        self.play_btn.bind(on_release=self.open_online_player)
        content.add_widget(self.play_btn)

        # Download options
        options = Card(bg=WHITE, radius=dp(19), padding=[dp(16), dp(14), dp(16), dp(16)], spacing=dp(10), size_hint_y=None)
        options.bind(minimum_height=options.setter('height'))
        options.add_widget(ui_label('下载设置', 16, INK, 26, bold=True))
        options.add_widget(ui_label('清晰度', 12, GRAY, 20, bold=True))
        self.quality_btn = PinkButton(text='请先解析视频', bg=PALE, fg=INK, size_hint_y=None, height=dp(48))
        self.quality_btn.bind(on_release=self.choose_quality)
        options.add_widget(self.quality_btn)
        options.add_widget(ui_label('保存内容', 12, GRAY, 20, bold=True))
        self.mode_btn = PinkButton(text=self.MODES[0], bg=PALE, fg=INK, size_hint_y=None, height=dp(48))
        self.mode_btn.bind(on_release=self.choose_mode)
        options.add_widget(self.mode_btn)
        content.add_widget(options)

        # Parts selector
        parts_card = Card(bg=WHITE, radius=dp(19), padding=[dp(16), dp(14), dp(16), dp(14)], spacing=dp(8), size_hint_y=None)
        parts_card.bind(minimum_height=parts_card.setter('height'))
        part_row = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(8))
        part_row.add_widget(ui_label('分P列表', 16, INK, 34, bold=True))
        part_row.add_widget(Widget())
        self.all_btn = PinkButton(text='批量下载：关闭', bg=PALE, fg=PINK, size_hint_x=None, width=dp(142), height=dp(38))
        self.all_btn.bind(on_release=self.toggle_all)
        part_row.add_widget(self.all_btn)
        parts_card.add_widget(part_row)
        parts_card.add_widget(ui_label('点选单个分 P；开启批量模式即可依次下载全部分 P。', 12, GRAY, 30))
        self.part_list = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(7))
        self.part_list.bind(minimum_height=self.part_list.setter('height'))
        parts_card.add_widget(self.part_list)
        content.add_widget(parts_card)

        # Task panel
        task_card = Card(bg=WHITE, radius=dp(19), padding=[dp(16), dp(14), dp(16), dp(16)], spacing=dp(9), size_hint_y=None)
        task_card.bind(minimum_height=task_card.setter('height'))
        task_card.add_widget(ui_label('下载任务', 16, INK, 28, bold=True))
        task_info = BoxLayout(orientation='horizontal', size_hint_y=None, height=dp(94), spacing=dp(11))
        self.task_cover = Image(size_hint=(None, 1), width=dp(112), allow_stretch=True, keep_ratio=True)
        task_info.add_widget(self.task_cover)
        task_meta = BoxLayout(orientation='vertical', spacing=dp(4))
        self.task_video_title = ui_label('尚未选择视频', 14, INK, 52, bold=True)
        self.task_video_owner = ui_label('解析视频后显示封面与 UP 主信息', 11, GRAY, 34, bold=True)
        task_meta.add_widget(self.task_video_title)
        task_meta.add_widget(self.task_video_owner)
        task_info.add_widget(task_meta)
        task_card.add_widget(task_info)
        self.download_btn = PinkButton(text='开始下载  →', size_hint_y=None, height=dp(54))
        self.download_btn.bind(on_release=self.start_download)
        task_card.add_widget(self.download_btn)
        self.progress = PinkProgressBar(max=100, value=0, size_hint_y=None, height=dp(18))
        task_card.add_widget(self.progress)
        self.task_label = ui_label('准备就绪 · 下载完成后保存到 Download/bilibilidl', 12, GRAY, 42)
        task_card.add_widget(self.task_label)
        self.cancel_btn = PinkButton(text='取消当前任务', bg=(.91, .92, .94, 1), fg=INK, size_hint_y=None, height=dp(44), disabled=True)
        self.cancel_btn.bind(on_release=self.cancel_download)
        task_card.add_widget(self.cancel_btn)
        self.history_btn = PinkButton(text='查看已下载文件  ↗', bg=PALE, fg=PINK, size_hint_y=None, height=dp(46))
        self.history_btn.bind(on_release=self.show_download_location)
        task_card.add_widget(self.history_btn)
        content.add_widget(task_card)

        content.add_widget(ui_label('bilibilidl  /  为每一次收藏留一份本地备份', 11, GRAY, 34, halign='center'))
        sc.add_widget(content)
        outer.add_widget(sc)
        self.add_widget(outer)
        self.refresh_parts()

    def _sync_screen_bg(self, *_):
        self._screen_bg.pos = self.pos
        self._screen_bg.size = self.size

    def open_login(self, *_):
        LoginPopup(self.app_ref).open()

    @mainthread
    def refresh_account(self, profile):
        if isinstance(profile, dict) and profile.get('uname'):
            name = safe_text(profile.get('uname'), 24)
            uid = profile.get('mid') or profile.get('uid')
            self.account_btn.text = (
                name + (('\nUID ' + str(uid)) if uid else '')
            )[:42]
        else:
            self.account_btn.text = 'B站授权登录'

    def parse(self, *_):
        if self.busy:
            self.result_label.text = '当前任务正在运行，请等待或取消后再解析。'
            return
        text = self.link_input.text.strip()
        if not text:
            self.result_label.text = '请先输入 BV / AV / B站视频链接。'
            return
        self._parse_generation += 1
        generation = self._parse_generation
        self.parse_btn.disabled = True
        self.result_label.text = '正在解析视频…'
        self.close_online_player()
        self.cover_preview.texture = None
        self.task_cover.texture = None

        def worker():
            try:
                cookie = self.app_ref.get_cookie() or ''
                session = new_session(cookie)
                info = get_video_info(session, text)
                pages = info.get('pages') or []
                if not pages:
                    pages = [{
                        'page': 1,
                        'cid': info.get('cid'),
                        'part': info.get('title') or 'P1',
                        'duration': info.get('duration', 0)
                    }]
                # Validate persisted Cookie before using it for quality lookup.
                # A stale profile must not be mistaken for a live login.
                nav = None
                if cookie:
                    try:
                        nav = get_nav(session)
                    except Exception:
                        nav = None
                    if not nav:
                        cookie = ''
                        session = new_session('')
                        @mainthread
                        def clear_expired_login():
                            self.app_ref.cookie_data = ''
                            self.app_ref.profile_data = {}
                            try:
                                if self.app_ref.cookie_file.exists():
                                    self.app_ref.cookie_file.unlink()
                            except Exception:
                                pass
                            self.refresh_account({})
                        clear_expired_login()
                def parse_quality_list(play_data, has_cookie):
                    qns = play_data.get('accept_quality') or []
                    descs = play_data.get('accept_description') or []
                    result = {}
                    for i, raw_qn in enumerate(qns):
                        try:
                            qn = int(raw_qn)
                        except (TypeError, ValueError):
                            continue
                        if qn <= 0:
                            continue
                        # Display only the description explicitly returned by the API.
                        # Do not synthesize labels such as 1080P/720P/360P.
                        desc = safe_text(descs[i] if i < len(descs) else '', 50).strip()
                        if not desc or desc.isdigit():
                            continue
                        result[qn] = desc
                    return sorted(result.items(), key=lambda item: item[0], reverse=True)

                # Each P has its own CID and may expose a different quality list.
                qualities_by_page = {}
                for page_index, page in enumerate(pages):
                    cid = page.get('cid')
                    if not cid:
                        continue
                    page_info = dict(info)
                    page_info['cid'] = cid
                    try:
                        page_play = get_play_info(session, page_info, cookie)
                        page_data = page_play.get('data') or {}
                        qualities_by_page[page_index] = parse_quality_list(page_data, bool(cookie))
                    except Exception:
                        qualities_by_page[page_index] = []
                qlist = qualities_by_page.get(0) or []
                if not qlist:
                    raise RuntimeError('B站没有返回当前分P可用清晰度，请检查视频状态或重新登录。')
                cover_url = safe_text(info.get('pic'), 2048)
                if cover_url.startswith('//'):
                    cover_url = 'https:' + cover_url
                cover = _fetch_cover_bytes(
                    cover_url,
                    session=session,
                    cache_dir=Path(self.app_ref.user_data_dir) / 'cover_cache',
                )

                @mainthread
                def done():
                    if generation != self._parse_generation:
                        return
                    self.info = info
                    self.pages = pages
                    self.qualities_by_page = qualities_by_page
                    self.qualities = qlist
                    self.quality_index = 0
                    self.selected_page = 0
                    self.all_pages = False
                    self.cover_url = cover_url
                    self.cover_bytes = cover
                    self.title_label.text = safe_text(
                        info.get('title'), 180
                    )
                    self.task_video_title.text = safe_text(info.get('title') or '未获取到标题', 100)
                    owner = info.get('owner') or {}
                    owner_name = (
                        owner.get('name')
                        if isinstance(owner, dict) else owner
                    )
                    self.owner_label.text = (
                        'UP主：' + safe_text(owner_name, 80)
                        + '    时长：'
                        + fmt_duration(info.get('duration'))
                    )
                    self.task_video_owner.text = 'UP主：' + safe_text(owner_name, 70) + ' · ' + fmt_duration(info.get('duration'))
                    self.quality_btn.disabled = not bool(qlist)
                    self.play_btn.disabled = not bool(info.get('bvid') and pages)
                    self.quality_btn.text = (
                        ('清晰度：' + qlist[0][1]
                         + (' (%s)' % qlist[0][0] if qlist[0][0] else ''))
                        if qlist else '暂无可用清晰度'
                    )
                    self.cover_preview.source = ''
                    self.task_cover.source = ''
                    if cover:
                        try:
                            self._set_cover_preview(cover)
                            _set_image_from_bytes(self.task_cover, cover, (600, 420))
                        except Exception:
                            self.cover_preview.texture = None
                            self.task_cover.texture = None
                    elif cover_url:
                        # Retry through the same header-aware, cached fetch path; never block playback if CDN rejects it.
                        try:
                            raw_cover = _fetch_cover_bytes(
                                cover_url,
                                cache_dir=Path(self.app_ref.user_data_dir) / 'cover_cache',
                            )
                            if raw_cover:
                                self.cover_bytes = raw_cover
                                self._set_cover_preview(raw_cover)
                                _set_image_from_bytes(self.task_cover, raw_cover, (600, 420))
                            else:
                                self.result_label.text = '解析成功，但封面 CDN 暂时拒绝或网络不可用；可继续下载。'
                        except Exception:
                            self.result_label.text = '解析成功，但封面暂时加载失败；可继续下载。'
                    self.result_label.text = (
                        '解析成功：%d 个分P。可选择单P或全部分P。'
                        % len(pages)
                    )
                    self.all_btn.text = '全部分P：关闭'
                    self.refresh_parts()
                    self.parse_btn.disabled = False
                    if self.auto_play_after_parse:
                        # Let the parsed metadata and page controls settle before opening WebView.
                        Clock.schedule_once(lambda _dt: self.open_online_player(), 0.35)
                done()
            except Exception as exc:
                message = safe_text(exc, 220)

                @mainthread
                def failed():
                    if generation == self._parse_generation:
                        self.result_label.text = '解析失败：' + message
                        self.parse_btn.disabled = False
                failed()
        threading.Thread(
            target=worker, daemon=True, name='bili-parse'
        ).start()

    def toggle_auto_play(self, *_):
        self.auto_play_after_parse = not self.auto_play_after_parse
        self.auto_play_btn.text = (
            '解析后自动播放：开启' if self.auto_play_after_parse
            else '解析后自动播放：关闭'
        )
        self.auto_play_btn.bg = PINK if self.auto_play_after_parse else PALE
        self.auto_play_btn.fg = WHITE if self.auto_play_after_parse else INK

    def open_online_player(self, *_):
        """Show the official Bilibili player inline over the preview area; expand on demand."""
        info = self.info or {}
        pages = self.pages or []
        if not info.get('bvid') or not pages:
            self.result_label.text = '请先解析视频，再使用在线播放。'
            return
        idx = max(0, min(int(self.selected_page or 0), len(pages) - 1))
        page = pages[idx]
        cid = page.get('cid') or info.get('cid')
        if not cid:
            self.result_label.text = '当前分P缺少 CID，暂时无法打开播放器。'
            return
        url = 'https://player.bilibili.com/player.html?' + urllib.parse.urlencode({
            'bvid': str(info['bvid']), 'cid': str(cid), 'danmaku': '1',
            'autoplay': '0'
        })
        if platform != 'android':
            self.result_label.text = '应用内 WebView 播放器仅在 Android APK 中可用。'
            return
        try:
            from jnius import autoclass, PythonJavaClass, java_method
            from kivy.core.window import Window
            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            WebView = autoclass('android.webkit.WebView')
            WebViewClient = autoclass('android.webkit.WebViewClient')
            WebChromeClient = autoclass('android.webkit.WebChromeClient')
            CookieManager = autoclass('android.webkit.CookieManager')
            FrameLayout = autoclass('android.widget.FrameLayout')
            NativeButton = autoclass('android.widget.Button')
            Gravity = autoclass('android.view.Gravity')
            ViewGroup = autoclass('android.view.ViewGroup')
            View = autoclass('android.view.View')
            LayoutParams = autoclass('android.view.ViewGroup$LayoutParams')
            FrameParams = autoclass('android.widget.FrameLayout$LayoutParams')
            Color = autoclass('android.graphics.Color')
            activity = PythonActivity.mActivity
            self.close_online_player()
            web = WebView(activity)
            settings = web.getSettings()
            settings.setJavaScriptEnabled(True)
            settings.setDomStorageEnabled(True)
            settings.setMediaPlaybackRequiresUserGesture(False)
            settings.setSupportZoom(False)
            settings.setLoadWithOverviewMode(True)
            settings.setUseWideViewPort(True)
            web.setBackgroundColor(Color.BLACK)
            web.setWebChromeClient(WebChromeClient())

            # Keep player navigation inside this WebView. Only allow Bilibili's
            # player/site and media/CDN hosts; block unrelated external URLs.
            screen_ref = self
            class InlinePlayerWebViewClient(PythonJavaClass):
                __javabase__ = 'android.webkit.WebViewClient'
                __javacontext__ = 'app'

                def _allow_url(self, raw_url):
                    try:
                        parsed = urllib.parse.urlparse(str(raw_url or ''))
                        scheme = (parsed.scheme or '').lower()
                        host = (parsed.hostname or '').lower()
                        if scheme not in ('https', 'http') or not host:
                            return False
                        allowed_hosts = (
                            'bilibili.com', 'biligame.com', 'hdslb.com',
                            'bilivideo.com', 'bilivideo.cn', 'bilivideo.net',
                            'biliapi.net', 'biliapi.com', 'biliimg.com',
                            'bilivideo.tv'
                        )
                        return any(host == domain or host.endswith('.' + domain)
                                   for domain in allowed_hosts)
                    except Exception:
                        return False

                @java_method('(Landroid/webkit/WebView;Ljava/lang/String;)Z')
                def shouldOverrideUrlLoading(self, view, url_value):
                    # False means let WebView load the URL; True blocks it.
                    return not self._allow_url(url_value)

            self._inline_player_webview_client = InlinePlayerWebViewClient()
            web.setWebViewClient(self._inline_player_webview_client)
            cookie = self.app_ref.get_cookie() or ''
            cm = CookieManager.getInstance()
            cm.setAcceptCookie(True)
            if cookie:
                for pair in cookie.split(';'):
                    pair = pair.strip()
                    if '=' in pair:
                        cm.setCookie('https://www.bilibili.com/', pair)
                        cm.setCookie('https://player.bilibili.com/', pair)
            try:
                cm.flush()
            except Exception:
                pass

            root = activity.findViewById(autoclass('android.R$id').content)
            overlay = FrameLayout(activity)
            overlay.setBackgroundColor(Color.TRANSPARENT)
            # Keep the overlay container full-sized, but the WebView itself is placed
            # exactly over the Kivy preview widget so it does not cover labels/buttons.
            overlay_params = LayoutParams(-1, -1)
            root.addView(overlay, overlay_params)
            self._online_webview = web
            self._online_overlay = overlay
            self._online_root = root
            self._player_native = {
                'FrameParams': FrameParams, 'Gravity': Gravity,
                'NativeButton': NativeButton, 'Color': Color,
                'Window': Window, 'ViewGroup': ViewGroup,
            }
            self._player_fullscreen = False
            self._player_web_params = None
            overlay.addView(web)

            screen_ref = self
            class ToggleListener(PythonJavaClass):
                __javainterfaces__ = ['android/view/View$OnClickListener']
                __javacontext__ = 'app'
                @java_method('(Landroid/view/View;)V')
                def onClick(self, view):
                    Clock.schedule_once(lambda _dt: screen_ref.toggle_player_size(), 0)
            class CloseListener(PythonJavaClass):
                __javainterfaces__ = ['android/view/View$OnClickListener']
                __javacontext__ = 'app'
                @java_method('(Landroid/view/View;)V')
                def onClick(self, view):
                    Clock.schedule_once(lambda _dt: screen_ref.close_online_player(), 0)
            expand_btn = NativeButton(activity)
            expand_btn.setText('放大 ⛶')
            expand_btn.setTextColor(Color.WHITE)
            expand_btn.setBackgroundColor(Color.argb(225, 210, 45, 95))
            close_btn = NativeButton(activity)
            close_btn.setText('关闭 ×')
            close_btn.setTextColor(Color.WHITE)
            close_btn.setBackgroundColor(Color.argb(225, 70, 70, 70))
            ep = FrameParams(-2, -2)
            ep.gravity = Gravity.TOP | Gravity.RIGHT
            ep.topMargin = int(dp(4) * activity.getResources().getDisplayMetrics().density)
            cp = FrameParams(-2, -2)
            cp.gravity = Gravity.TOP | Gravity.LEFT
            cp.topMargin = int(dp(4) * activity.getResources().getDisplayMetrics().density)
            overlay.addView(expand_btn, ep)
            overlay.addView(close_btn, cp)
            self._online_expand_listener = ToggleListener()
            self._online_close_listener = CloseListener()
            expand_btn.setOnClickListener(self._online_expand_listener)
            close_btn.setOnClickListener(self._online_close_listener)
            self._player_expand_button = expand_btn
            self._player_close_button = close_btn
            self._position_inline_player()
            web.loadUrl(url)
            self.player_hint.text = '视频播放器已嵌入此处；点击“放大”进入全屏。'
            self.result_label.text = '播放器已显示在视频预览区域，不会覆盖标题和下载设置。'
        except Exception as exc:
            self.result_label.text = '应用内播放器启动失败：' + safe_text(exc, 160)

    def _position_inline_player(self):
        """Position the native WebView over the preview widget using Android pixels."""
        if not getattr(self, '_online_webview', None) or getattr(self, '_player_fullscreen', False):
            return
        try:
            from kivy.core.window import Window
            from kivy.metrics import Metrics
            FrameParams = self._player_native['FrameParams']
            density = float(Metrics.density or 1.0)
            x, y = self.cover_preview.to_window(0, 0)
            width = max(dp(220), self.cover_preview.width)
            height = max(dp(150), self.cover_preview.height)
            params = FrameParams(int(width * density), int(height * density))
            params.leftMargin = int(x * density)
            params.topMargin = int((Window.height - y - height) * density)
            self._online_webview.setLayoutParams(params)
            self._player_web_params = params
        except Exception:
            pass

    def toggle_player_size(self, *_):
        if not getattr(self, '_online_webview', None):
            return
        try:
            FrameParams = self._player_native['FrameParams']
            Gravity = self._player_native['Gravity']
            density = float(self._player_native['Window'].width / max(1, self._player_native['Window'].width))
            # Android LayoutParams use physical pixels. Kivy dp is converted using device density.
            from kivy.metrics import Metrics
            density = float(Metrics.density or 1.0)
            self._player_fullscreen = not self._player_fullscreen
            if self._player_fullscreen:
                params = FrameParams(-1, -1)
                self._player_expand_button.setText('缩小 ⤢')
                self.player_hint.text = '全屏播放中；点击“缩小”返回解析页面。'
            else:
                x, y = self.cover_preview.to_window(0, 0)
                params = FrameParams(int(self.cover_preview.width * density), int(self.cover_preview.height * density))
                params.leftMargin = int(x * density)
                params.topMargin = int((self._player_native['Window'].height - y - self.cover_preview.height) * density)
                self._player_expand_button.setText('放大 ⛶')
                self.player_hint.text = '视频播放器已嵌入此处；点击“放大”进入全屏。'
            self._online_webview.setLayoutParams(params)
        except Exception as exc:
            self.result_label.text = '切换播放器大小失败：' + safe_text(exc, 120)

    def close_online_player(self, *_):
        """Remove the native WebView overlay safely."""
        try:
            overlay = getattr(self, '_online_overlay', None)
            root = getattr(self, '_online_root', None)
            web = getattr(self, '_online_webview', None)
            if web is not None:
                try:
                    from jnius import autoclass
                    Looper = autoclass('android.os.Looper')
                    main_looper = Looper.getMainLooper()
                    current_looper = Looper.myLooper()
                    # WebView must only be manipulated on Android's main thread.
                    # A missing Looper/MessageQueue during shutdown is treated as
                    # a signal to skip native cleanup rather than dereference it.
                    if main_looper is not None and current_looper is not None and current_looper.equals(main_looper):
                        web.stopLoading()
                        web.loadUrl('about:blank')
                        web.destroy()
                except Exception:
                    pass
            if overlay is not None and root is not None:
                try:
                    from jnius import autoclass
                    Looper = autoclass('android.os.Looper')
                    main_looper = Looper.getMainLooper()
                    current_looper = Looper.myLooper()
                    if main_looper is not None and current_looper is not None and current_looper.equals(main_looper):
                        root.removeView(overlay)
                except Exception:
                    pass
        except Exception:
            pass
        self._online_webview = None
        self._online_overlay = None
        self._online_root = None
        self._online_close_listener = None
        self._inline_player_webview_client = None
        self._online_expand_listener = None
        self._player_expand_button = None
        self._player_close_button = None
        self._player_native = None
        self._player_fullscreen = False

    @mainthread
    def _set_cover_preview(self, raw):
        try:
            _set_image_from_bytes(self.cover_preview, raw, (900, 600))
        except Exception as exc:
            self.cover_preview.texture = None
            print('[bilibilidl] cover decode failed:', safe_text(exc, 160))

    def choose_quality(self, *_):
        if not self.qualities:
            try:
                self.app_ref.show_toast('接口没有返回可用清晰度，请重新解析或检查登录状态。')
            except Exception:
                pass
            return
        options = [
            '%s%s' % (desc, (' (%s)' % qn) if qn else '')
            for qn, desc in self.qualities
        ]

        def selected(idx):
            self.quality_index = idx
            qn, desc = self.qualities[idx]
            self.quality_btn.text = (
                '清晰度：' + desc + (' (%s)' % qn if qn else '')
            )
        ChoicePopup(
            '选择清晰度', options, self.quality_index, selected
        ).open()

    def choose_mode(self, *_):
        def selected(idx):
            self.mode_index = idx
            self.mode_btn.text = self.MODES[idx]
        ChoicePopup(
            '选择下载类型', self.MODES, self.mode_index, selected
        ).open()

    def toggle_all(self, *_):
        self.all_pages = not self.all_pages
        self.all_btn.text = (
            '全部分P：开启' if self.all_pages else '全部分P：关闭'
        )
        self.refresh_parts()

    @mainthread
    def refresh_parts(self):
        self.part_list.clear_widgets()
        if not self.pages:
            self.part_list.add_widget(
                ui_label('解析后显示分P列表。', 12, GRAY, 34)
            )
            return
        for idx, page in enumerate(self.pages):
            chosen = self.all_pages or idx == self.selected_page
            title = safe_text(
                page.get('part') or ('P%d' % (idx + 1)), 100
            )
            b = PinkButton(
                text='%s  P%d · %s' % (
                    '✓' if chosen else '○', idx + 1, title
                ),
                bg=PINK if chosen else PALE,
                fg=WHITE if chosen else INK,
                size_hint_y=None,
                height=dp(44)
            )
            b.bind(on_release=lambda _b, i=idx: self.select_page(i))
            self.part_list.add_widget(b)

    def select_page(self, idx):
        if self.busy:
            return
        self.selected_page = idx
        self.all_pages = False
        self.all_btn.text = '全部分P：关闭'
        self.qualities = list(getattr(self, 'qualities_by_page', {}).get(idx, []))
        self.quality_index = 0
        self.quality_btn.disabled = not bool(self.qualities)
        self.quality_btn.text = (
            ('清晰度：' + self.qualities[0][1] + ' (%s)' % self.qualities[0][0])
            if self.qualities else '当前分P暂无可用清晰度'
        )
        self.refresh_parts()

    def _output_dir(self):
        try:
            root = Path(App.get_running_app().user_data_dir) / 'downloads'
        except Exception:
            root = BASE_DIR / 'downloads'
        root.mkdir(parents=True, exist_ok=True)
        return root

    def start_download(self, *_):
        if self.busy:
            self.task_label.text = '已有任务运行中。'
            return
        if not self.info or not self.pages:
            self.result_label.text = '请先成功解析视频。'
            return
        if self.all_pages:
            pages = [dict(p) for p in self.pages]
        else:
            pages = [dict(self.pages[self.selected_page])]
        if self.mode_index == 0 and not self.qualities:
            self.result_label.text = '没有可用清晰度，请重新解析。'
            return
        self.busy = True
        self.cancel_event = threading.Event()
        cancel = self.cancel_event
        self.download_btn.disabled = True
        self.parse_btn.disabled = True
        self.cancel_btn.disabled = False
        self.progress.value = 0
        self.task_label.text = '正在准备下载…'
        info_snapshot = dict(self.info)
        cookie = self.app_ref.get_cookie() or ''
        mode = self.mode_index
        if not self.qualities:
            self.result_label.text = '没有从 B 站接口获取到真实清晰度，请重新解析或检查登录状态。'
            return
        quality = self.qualities[self.quality_index][0]
        out_dir = self._output_dir()

        def set_status(message, pct=None):
            message = safe_text(message, 240)

            @mainthread
            def update():
                self.task_label.text = message
                if pct is not None:
                    self.progress.value = max(
                        0, min(100, float(pct))
                    )
            update()

        def worker():
            successes, failures = [], []
            try:
                for order, page in enumerate(pages):
                    if cancel.is_set():
                        break
                    page_no = int(page.get('page') or (order + 1))
                    cid = page.get('cid')
                    if not cid:
                        failures.append('P%d：缺少 cid' % page_no)
                        continue
                    bvid = safe_text(
                        info_snapshot.get('bvid') or 'video', 40
                    )
                    base_title = safe_name(
                        info_snapshot.get('title') or bvid
                    )
                    part_title = safe_name(
                        page.get('part') or ('P%d' % page_no)
                    )
                    try:
                        if mode == 0:
                            part_info = dict(info_snapshot)
                            part_info['cid'] = cid
                            part_info['page'] = page_no

                            def progress_cb(done, total):
                                pct = (
                                    done * 100.0 / total
                                    if total else 0
                                )
                                set_status(
                                    'P%d 下载中：%.1f%%'
                                    % (page_no, pct),
                                    pct
                                )
                            result = download_video(
                                part_info,
                                cookie,
                                quality,
                                str(out_dir),
                                progress=progress_cb,
                                cancel=cancel,
                                log=lambda msg: set_status(
                                    'P%d：%s' % (page_no, msg)
                                )
                            )
                            verified = _verified_file(result)
                            # Persist the source cover beside the downloaded media and in a stable cache.
                            try:
                                raw_cover = self.cover_bytes
                                if raw_cover:
                                    if raw_cover.startswith((b'GIF87a', b'GIF89a')):
                                        cover_ext = 'gif'
                                    elif raw_cover.startswith(b'RIFF') and raw_cover[8:12] == b'WEBP':
                                        cover_ext = 'webp'
                                    elif raw_cover.startswith(b'\x89PNG\r\n\x1a\n'):
                                        cover_ext = 'png'
                                    else:
                                        cover_ext = 'jpg'
                                    cover_name = safe_name(verified.stem) + '_cover.' + cover_ext
                                    cover_file = verified.with_name(cover_name)
                                    cover_file.write_bytes(raw_cover)
                                    cache_dir = Path(self.app_ref.user_data_dir) / 'cover_cache'
                                    cache_dir.mkdir(parents=True, exist_ok=True)
                                    (cache_dir / (bvid + '.' + cover_ext)).write_bytes(raw_cover)
                                    self.app_ref._last_download_bvid = bvid
                            except Exception as cover_exc:
                                print('[bilibilidl] cover cache save failed:', safe_text(cover_exc, 160))
                            public_uri = _export_to_public_downloads(
                                verified
                            )
                            successes.append(
                                str(verified)
                                + (
                                    ' | ' + public_uri
                                    if public_uri.startswith('content://')
                                    else ''
                                )
                            )
                        elif mode == 1:
                            raw = self.cover_bytes
                            if not raw:
                                raise RuntimeError(
                                    '封面原始数据为空，请重新解析'
                                )
                            if raw.startswith((b'GIF87a', b'GIF89a')):
                                ext = 'gif'
                            elif (
                                raw.startswith(b'RIFF')
                                and raw[8:12] == b'WEBP'
                            ):
                                ext = 'webp'
                            elif raw.startswith(b'\x89PNG\r\n\x1a\n'):
                                ext = 'png'
                            elif raw.startswith(b'\xff\xd8\xff'):
                                ext = 'jpg'
                            else:
                                raise RuntimeError(
                                    '无法识别封面格式，已拒绝保存'
                                )
                            dest = out_dir / (bvid + '_cover.' + ext)
                            dest.write_bytes(raw)
                            verified = _verified_file(dest)
                            public_uri = _export_to_public_downloads(
                                verified
                            )
                            successes.append(
                                str(verified)
                                + (
                                    ' | ' + public_uri
                                    if public_uri.startswith('content://')
                                    else ''
                                )
                            )
                        elif mode == 2:
                            session = new_session(cookie)
                            url = (
                                'https://comment.bilibili.com/%s.xml'
                                % cid
                            )
                            resp = session.get(
                                url, timeout=(10, 30)
                            )
                            resp.raise_for_status()
                            data = resp.content
                            if not data or b'<i' not in data[:1000]:
                                raise RuntimeError(
                                    '该分P没有可用弹幕 XML'
                                )
                            dest = out_dir / (
                                bvid + '_P%d_danmaku.xml' % page_no
                            )
                            dest.write_bytes(data)
                            verified = _verified_file(dest)
                            public_uri = _export_to_public_downloads(
                                verified
                            )
                            successes.append(
                                str(verified)
                                + (
                                    ' | ' + public_uri
                                    if public_uri.startswith('content://')
                                    else ''
                                )
                            )
                        elif mode == 3:
                            session = new_session(cookie)
                            params = {'cid': cid}
                            if info_snapshot.get('bvid'):
                                params['bvid'] = info_snapshot['bvid']
                            elif info_snapshot.get('aid'):
                                params['aid'] = info_snapshot['aid']
                            resp = session.get(
                                'https://api.bilibili.com/x/player/v2',
                                params=params,
                                timeout=(10, 25)
                            )
                            resp.raise_for_status()
                            jdata = resp.json()
                            subs = (
                                ((jdata.get('data') or {}).get('subtitle') or {})
                                .get('subtitles') or []
                            )
                            if not subs:
                                raise RuntimeError('该分P没有可用字幕')
                            sub_url = subs[0].get('subtitle_url') or ''
                            if sub_url.startswith('//'):
                                sub_url = 'https:' + sub_url
                            if not sub_url.startswith('https://'):
                                raise RuntimeError('字幕地址无效')
                            sr = session.get(
                                sub_url, timeout=(10, 25)
                            )
                            sr.raise_for_status()
                            sj = sr.json()
                            lines = []
                            for i, item in enumerate(
                                sj.get('body') or [], 1
                            ):
                                def stamp(value):
                                    sec = max(0.0, float(value or 0))
                                    whole = int(sec)
                                    ms = int((sec - whole) * 1000)
                                    return '%02d:%02d:%02d,%03d' % (
                                        whole // 3600,
                                        (whole % 3600) // 60,
                                        whole % 60,
                                        ms
                                    )
                                lines.extend([
                                    str(i),
                                    stamp(item.get('from'))
                                    + ' --> '
                                    + stamp(item.get('to')),
                                    safe_text(item.get('content'), 1000),
                                    ''
                                ])
                            dest = out_dir / (
                                bvid + '_P%d.srt' % page_no
                            )
                            dest.write_text(
                                '\n'.join(lines),
                                encoding='utf-8-sig'
                            )
                            verified = _verified_file(dest)
                            public_uri = _export_to_public_downloads(
                                verified
                            )
                            successes.append(
                                str(verified)
                                + (
                                    ' | ' + public_uri
                                    if public_uri.startswith('content://')
                                    else ''
                                )
                            )
                        set_status(
                            'P%d 完成' % page_no,
                            (order + 1) * 100.0 / len(pages)
                        )
                    except Exception as exc:
                        failures.append(
                            'P%d：%s' % (
                                page_no, safe_text(exc, 180)
                            )
                        )
                        set_status(
                            'P%d 失败：%s' % (
                                page_no, safe_text(exc, 150)
                            )
                        )
                if cancel.is_set():
                    set_status(
                        '任务已取消；已完成 %d 项，失败 %d 项'
                        % (len(successes), len(failures))
                    )
                elif failures:
                    set_status(
                        '任务结束：成功 %d 项，失败 %d 项'
                        % (len(successes), len(failures))
                    )
                else:
                    set_status(
                        '全部完成：%d 项。已核实文件；'
                        'Android 公共目录：Download/bilibilidl'
                        % len(successes),
                        100
                    )
                summary = '完成 %d 项' % len(successes)
                if failures:
                    summary += '\n失败：\n' + '\n'.join(failures[:8])
                if successes:
                    summary += (
                        '\n应用私有目录：' + str(out_dir)
                        + '\nAndroid 公共目录：Download/bilibilidl'
                    )

                @mainthread
                def show_summary():
                    self.result_label.text = summary[:1200]
                show_summary()
            except Exception as exc:
                set_status('任务异常：' + safe_text(exc, 180))
            finally:
                @mainthread
                def finish():
                    self.busy = False
                    self.download_btn.disabled = False
                    self.parse_btn.disabled = False
                    self.cancel_btn.disabled = True
                    # 页面由 App.build() 统一创建；分别刷新，避免一个页面刷新失败
                    # 导致另一个页面也跳过。进入对应 tab 时仍会再次刷新兜底。
                    for screen_name, refresh_name in (
                            ('downloaded', 'refresh_files'),
                            ('profile', 'refresh_profile')):
                        try:
                            screen = getattr(self.app_ref, screen_name, None)
                            refresh = getattr(screen, refresh_name, None)
                            if callable(refresh):
                                refresh()
                        except Exception as refresh_exc:
                            # 保留下载结果，不把刷新失败伪装成下载失败。
                            print('[bilibilidl] %s refresh failed: %s' % (
                                screen_name, safe_text(refresh_exc, 180)))
                finish()
        threading.Thread(
            target=worker, daemon=True, name='bili-download'
        ).start()

    def cancel_download(self, *_):
        if self.busy:
            self.cancel_event.set()
            self.task_label.text = '正在取消…等待当前网络块结束。'

    def show_download_location(self, *_):
        path = str(self._output_dir())
        message = (
            '应用私有目录：\n' + path
            + '\n\nAndroid 公共下载目录：\nDownload/bilibilidl'
        )
        Popup(
            title='下载保存位置',
            content=ui_label(message, 13, INK, 100),
            size_hint=(.9, .4)
        ).open()


_BRIDGE_TASKS = {}
_BRIDGE_TASKS_LOCK = threading.Lock()


class _BridgeHandler(BaseHTTPRequestHandler):
    app_ref = None

    def log_message(self, *_args):
        return

    def _reply(self, payload, status=200):
        raw = json.dumps(
            payload, ensure_ascii=False
        ).encode('utf-8')
        self.send_response(status)
        self.send_header(
            'Content-Type', 'application/json; charset=utf-8'
        )
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header(
            'Access-Control-Allow-Headers', 'Content-Type'
        )
        self.send_header(
            'Access-Control-Allow-Methods', 'POST, OPTIONS'
        )
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self._reply({'ok': True})

    def do_POST(self):
        try:
            length = min(
                int(self.headers.get('Content-Length', '0')),
                1024 * 1024
            )
            data = json.loads(
                self.rfile.read(length).decode('utf-8') or '{}'
            )
            action = str(data.get('action') or '')
            app = self.app_ref
            if app is None:
                return self._reply(
                    {'ok': False, 'error': '应用尚未启动'}, 503
                )
            if action == 'ping':
                return self._reply({
                    'ok': True,
                    'app': 'bilibilidl',
                    'bridge': 1
                })
            if action == 'account':
                profile = (
                    app.profile_data
                    if isinstance(app.profile_data, dict)
                    else {}
                )
                return self._reply({
                    'ok': True,
                    'logged_in': bool(app.get_cookie()),
                    'profile': profile
                })
            if action == 'parse':
                source = str(
                    data.get('url') or data.get('input') or ''
                ).strip()
                if not source:
                    return self._reply({
                        'ok': False,
                        'error': '请输入 BV / AV / 视频链接'
                    })
                cookie = app.get_cookie() or ''
                session = new_session(cookie)
                info = get_video_info(session, source)
                pages = info.get('pages') or [{
                    'page': 1,
                    'cid': info.get('cid'),
                    'part': info.get('title', 'P1'),
                    'duration': info.get('duration', 0)
                }]
                play = get_play_info(session, info, cookie)
                payload = play.get('data') or {}
                ids = payload.get('accept_quality') or []
                desc = payload.get('accept_description') or []
                qualities_by_qn = {}
                for i, q in enumerate(ids):
                    try:
                        qn = int(q)
                    except (TypeError, ValueError):
                        continue
                    if qn <= 0 or (not cookie and qn >= 80):
                        continue
                    label = str(desc[i]).strip() if i < len(desc) and desc[i] else ''
                    # Only expose labels actually returned by the Bilibili API.
                    if not label or label.isdigit():
                        continue
                    qualities_by_qn[qn] = label
                qualities = [{'qn': qn, 'label': label} for qn, label in sorted(qualities_by_qn.items(), reverse=True)]
                clean_pages = []
                for page in pages:
                    clean_pages.append({
                        'page': int(page.get('page') or 1),
                        'cid': page.get('cid'),
                        'part': str(page.get('part') or ''),
                        'duration': page.get('duration') or 0
                    })
                app._bridge_last_info = dict(info)
                app._bridge_last_pages = clean_pages
                app._bridge_last_qualities = qualities
                return self._reply({
                    'ok': True,
                    'video': {
                        'bvid': info.get('bvid'),
                        'aid': info.get('aid'),
                        'title': info.get('title'),
                        'owner': info.get('owner'),
                        'pic': info.get('pic'),
                        'duration': info.get('duration')
                    },
                    'pages': clean_pages,
                    'qualities': qualities
                })
            if action == 'start_download':
                info = getattr(app, '_bridge_last_info', None)
                pages = getattr(app, '_bridge_last_pages', [])
                if not info or not pages:
                    return self._reply({
                        'ok': False,
                        'error': '请先解析视频'
                    })
                task_id = str(int(time.time() * 1000))
                selected = data.get('pages', 'all')
                if selected == 'all' or selected is None:
                    chosen = list(pages)
                elif isinstance(selected, list):
                    try:
                        page_numbers = {int(x) for x in selected}
                    except (TypeError, ValueError):
                        return self._reply({
                            'ok': False,
                            'error': '分P编号格式不正确'
                        })
                    chosen = [
                        p for p in pages
                        if int(p.get('page') or 1) in page_numbers
                    ]
                else:
                    try:
                        page_num = int(selected)
                    except (TypeError, ValueError):
                        return self._reply({
                            'ok': False,
                            'error': '分P编号不正确'
                        })
                    chosen = [
                        p for p in pages
                        if int(p.get('page') or 1) == page_num
                    ]
                if not chosen:
                    return self._reply({
                        'ok': False,
                        'error': '没有找到所选分P'
                    })
                try:
                    qn = int(
                        data.get('quality')
                        or getattr(
                            app, '_bridge_last_qualities', [{}]
                        )[0].get('qn', 0)
                    )
                except Exception:
                    qn = 0
                mode = str(data.get('mode') or 'video').strip().lower()
                allowed_modes = {'video', 'cover', 'danmaku', 'subtitle'}
                if mode not in allowed_modes:
                    return self._reply({
                        'ok': False,
                        'error': '不支持的下载类型'
                    })
                out_dir = Path(app.user_data_dir) / 'downloads'
                out_dir.mkdir(parents=True, exist_ok=True)
                cancel = threading.Event()
                with _BRIDGE_TASKS_LOCK:
                    _BRIDGE_TASKS[task_id] = {
                        'state': 'running',
                        'progress': 0,
                        'message': '准备下载',
                        'files': [],
                        'error': '',
                        'cancel': cancel
                    }

                def worker():
                    failures = []
                    try:
                        total = max(1, len(chosen))
                        for idx, page in enumerate(chosen):
                            if cancel.is_set():
                                break
                            page_no = int(page.get('page') or idx + 1)
                            cid = page.get('cid')
                            if not cid:
                                failures.append(
                                    'P%d：缺少 cid' % page_no
                                )
                                continue
                            part_info = dict(info)
                            part_info['cid'] = cid
                            part_info['page'] = page_no

                            def progress(done, size, index=idx,
                                         current_page=page_no):
                                pct = int(
                                    (
                                        index
                                        + (done / size if size else 0)
                                    ) / total * 100
                                )
                                with _BRIDGE_TASKS_LOCK:
                                    task = _BRIDGE_TASKS.get(task_id)
                                    if task:
                                        task.update(
                                            progress=pct,
                                            message='P%d 下载中'
                                            % current_page
                                        )
                            try:
                                cookie = app.get_cookie() or ''
                                dest = None
                                if mode == 'video':
                                    result = download_video(
                                        part_info,
                                        cookie,
                                        qn,
                                        str(out_dir),
                                        progress=progress,
                                        cancel=cancel,
                                        log=lambda msg: None
                                    )
                                    dest = _verified_file(result)
                                elif mode == 'cover':
                                    pic = str(info.get('pic') or '')
                                    if pic.startswith('//'):
                                        pic = 'https:' + pic
                                    if not pic.startswith(('https://', 'http://')):
                                        raise RuntimeError('视频封面地址无效')
                                    resp = requests.get(
                                        pic,
                                        timeout=(10, 30),
                                        headers={
                                            'Referer': 'https://www.bilibili.com/'
                                        }
                                    )
                                    resp.raise_for_status()
                                    raw = resp.content
                                    if not raw or len(raw) > 20 * 1024 * 1024:
                                        raise RuntimeError('封面为空或文件过大')
                                    if raw.startswith((b'GIF87a', b'GIF89a')):
                                        ext = 'gif'
                                    elif raw.startswith(b'\x89PNG\r\n\x1a\n'):
                                        ext = 'png'
                                    elif raw.startswith(b'\xff\xd8\xff'):
                                        ext = 'jpg'
                                    elif raw.startswith(b'RIFF') and raw[8:12] == b'WEBP':
                                        ext = 'webp'
                                    else:
                                        raise RuntimeError('无法识别封面格式')
                                    dest = out_dir / (
                                        str(info.get('bvid') or info.get('aid') or 'video')
                                        + '_cover.' + ext
                                    )
                                    dest.write_bytes(raw)
                                    dest = _verified_file(dest)
                                elif mode == 'danmaku':
                                    session = new_session(cookie)
                                    resp = session.get(
                                        'https://comment.bilibili.com/%s.xml' % cid,
                                        timeout=(10, 30)
                                    )
                                    resp.raise_for_status()
                                    raw = resp.content
                                    if not raw or b'<i' not in raw[:1000]:
                                        raise RuntimeError('该分P没有可用弹幕 XML')
                                    dest = out_dir / (
                                        str(info.get('bvid') or info.get('aid') or 'video')
                                        + '_P%d_danmaku.xml' % page_no
                                    )
                                    dest.write_bytes(raw)
                                    dest = _verified_file(dest)
                                elif mode == 'subtitle':
                                    session = new_session(cookie)
                                    params = {'cid': cid}
                                    if info.get('bvid'):
                                        params['bvid'] = info['bvid']
                                    elif info.get('aid'):
                                        params['aid'] = info['aid']
                                    resp = session.get(
                                        'https://api.bilibili.com/x/player/v2',
                                        params=params,
                                        timeout=(10, 25)
                                    )
                                    resp.raise_for_status()
                                    jdata = resp.json()
                                    subs = (
                                        ((jdata.get('data') or {}).get('subtitle') or {})
                                        .get('subtitles') or []
                                    )
                                    if not subs:
                                        raise RuntimeError('该分P没有可用字幕')
                                    sub_url = subs[0].get('subtitle_url') or ''
                                    if sub_url.startswith('//'):
                                        sub_url = 'https:' + sub_url
                                    if not sub_url.startswith('https://'):
                                        raise RuntimeError('字幕地址无效')
                                    sr = session.get(sub_url, timeout=(10, 25))
                                    sr.raise_for_status()
                                    subtitle_data = sr.json()
                                    subtitle_lines = []

                                    def srt_stamp(value):
                                        sec = max(0.0, float(value or 0))
                                        whole = int(sec)
                                        ms = int((sec - whole) * 1000)
                                        return '%02d:%02d:%02d,%03d' % (
                                            whole // 3600,
                                            (whole % 3600) // 60,
                                            whole % 60,
                                            ms
                                        )
                                    for line_no, item in enumerate(
                                        subtitle_data.get('body') or [], 1
                                    ):
                                        subtitle_lines.extend([
                                            str(line_no),
                                            srt_stamp(item.get('from'))
                                            + ' --> '
                                            + srt_stamp(item.get('to')),
                                            str(item.get('content') or ''),
                                            ''
                                        ])
                                    if not subtitle_lines:
                                        raise RuntimeError('字幕内容为空')
                                    dest = out_dir / (
                                        str(info.get('bvid') or info.get('aid') or 'video')
                                        + '_P%d.srt' % page_no
                                    )
                                    dest.write_text(
                                        '\n'.join(subtitle_lines),
                                        encoding='utf-8-sig'
                                    )
                                    dest = _verified_file(dest)
                                public_uri = _export_to_public_downloads(dest)
                                display_path = str(dest)
                                if public_uri.startswith('content://'):
                                    display_path += ' | ' + public_uri
                                with _BRIDGE_TASKS_LOCK:
                                    task = _BRIDGE_TASKS.get(task_id)
                                    if task:
                                        task['files'].append(display_path)
                            except Exception as exc:
                                failures.append(
                                    'P%d：%s' % (page_no, safe_text(exc, 220))
                                )
                            with _BRIDGE_TASKS_LOCK:
                                task = _BRIDGE_TASKS.get(task_id)
                                if task:
                                    task['progress'] = int(
                                        (idx + 1) / total * 100
                                    )
                        with _BRIDGE_TASKS_LOCK:
                            task = _BRIDGE_TASKS.get(task_id)
                            if task:
                                if cancel.is_set():
                                    task.update(
                                        state='cancelled',
                                        message='已取消'
                                    )
                                elif failures:
                                    task.update(
                                        state='failed',
                                        message='部分任务失败或文件导出失败',
                                        error='；'.join(failures)[:1500]
                                    )
                                else:
                                    task.update(
                                        state='completed',
                                        progress=100,
                                        message=(
                                            '下载完成，文件已验证；'
                                            'Android 公共目录为 '
                                            'Download/bilibilidl'
                                        )
                                    )
                    except Exception as exc:
                        with _BRIDGE_TASKS_LOCK:
                            task = _BRIDGE_TASKS.get(task_id)
                            if task:
                                task.update(
                                    state='failed',
                                    message='下载失败',
                                    error=safe_text(exc, 500)
                                )
                threading.Thread(
                    target=worker,
                    daemon=True,
                    name='bili-html-download'
                ).start()
                return self._reply({
                    'ok': True,
                    'task_id': task_id
                })
            if action == 'task_status':
                task_id = str(data.get('task_id') or '')
                with _BRIDGE_TASKS_LOCK:
                    task = _BRIDGE_TASKS.get(task_id)
                    if not task:
                        return self._reply({
                            'ok': False,
                            'error': '任务不存在'
                        })
                    result = {
                        k: v for k, v in task.items()
                        if k != 'cancel'
                    }
                return self._reply({
                    'ok': True,
                    'task': result
                })
            if action == 'cancel_download':
                task_id = str(data.get('task_id') or '')
                with _BRIDGE_TASKS_LOCK:
                    task = _BRIDGE_TASKS.get(task_id)
                    if task:
                        task['cancel'].set()
                return self._reply({
                    'ok': bool(task),
                    'error': '' if task else '任务不存在'
                })
            if action == 'history':
                folder = Path(app.user_data_dir) / 'downloads'
                files = []
                if folder.exists():
                    try:
                        items = sorted(
                            folder.iterdir(),
                            key=lambda x: x.stat().st_mtime,
                            reverse=True
                        )
                    except OSError:
                        items = []
                    for item in items:
                        try:
                            if not item.is_file():
                                continue
                            stat = item.stat()
                            if stat.st_size <= 0:
                                continue
                            files.append({
                                'name': item.name,
                                'path': str(item),
                                'size': stat.st_size,
                                'mtime': stat.st_mtime
                            })
                        except OSError:
                            continue
                return self._reply({
                    'ok': True,
                    'files': files[:500]
                })
            return self._reply({
                'ok': False,
                'error': '未知操作：' + action
            })
        except Exception as exc:
            return self._reply({
                'ok': False,
                'error': safe_text(exc, 300)
            }, 500)


class _BridgeServer:
    def __init__(self, app):
        self.app = app
        self.server = None
        self.thread = None

    def start(self):
        if self.server:
            return
        handler = type(
            'BiliDlBridgeHandler',
            (_BridgeHandler,),
            {'app_ref': self.app}
        )
        try:
            self.server = ThreadingHTTPServer(
                ('127.0.0.1', 18765), handler
            )
            self.server.daemon_threads = True
            self.thread = threading.Thread(
                target=self.server.serve_forever,
                daemon=True
            )
            self.thread.start()
        except OSError:
            self.server = None

    def stop(self):
        try:
            if self.server:
                self.server.shutdown()
                self.server.server_close()
        except Exception:
            pass




class BiliSyncScreen(Screen):
    """Local cache of the signed-in user's Bilibili history and favorites."""
    def __init__(self, app, **kwargs):
        super().__init__(**kwargs)
        self.app_ref = app
        self.mode = 'history'
        self.items = []
        self.cache_file = Path(app.user_data_dir) / 'bili_sync_cache.json'
        root = BoxLayout(orientation='vertical', padding=[dp(15), dp(13), dp(15), dp(10)], spacing=dp(9))
        root.add_widget(ui_label('B站同步', 25, INK, 36, bold=True))
        root.add_widget(ui_label('历史记录 · 收藏夹 · 全站搜索', 12, GRAY, 23))
        self.search_input = PinkInput(hint_text='搜索视频标题或 UP 主', multiline=False, size_hint_y=None, height=dp(45))
        root.add_widget(self.search_input)
        actions = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(7))
        search_btn = PinkButton(text='搜索', bg=PINK, fg=WHITE)
        search_btn.bind(on_release=lambda *_: self.search())
        sync_btn = PinkButton(text='同步账号数据', bg=PALE, fg=PINK)
        sync_btn.bind(on_release=lambda *_: self.sync_all())
        actions.add_widget(search_btn); actions.add_widget(sync_btn)
        root.add_widget(actions)
        tabs = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(5))
        for key, title in [('history','历史记录'),('favorites','收藏夹'),('online','B站搜索')]:
            b = PinkButton(text=title, bg=PALE, fg=PINK, font_size=dp(12))
            b.bind(on_release=lambda _b, m=key: self.set_mode(m))
            tabs.add_widget(b)
        root.add_widget(tabs)
        self.status = ui_label('同步后可离线搜索已缓存的历史和收藏。', 11, GRAY, 34)
        root.add_widget(self.status)
        sc = ScrollView(do_scroll_x=False, bar_width=dp(3))
        self.list_box = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(8), padding=[0, dp(2), 0, dp(10)])
        self.list_box.bind(minimum_height=self.list_box.setter('height'))
        sc.add_widget(self.list_box); root.add_widget(sc)
        self.add_widget(root)
        self.load_cache()
        self.render_items()

    def set_mode(self, mode):
        self.mode = mode
        self.search_input.hint_text = {'history':'搜索本地观看历史','favorites':'搜索本地收藏视频','online':'搜索 B 站全站公开视频'}.get(mode, '搜索视频')
        self.render_items()

    def load_cache(self):
        try:
            data = json.loads(self.cache_file.read_text(encoding='utf-8')) if self.cache_file.exists() else {}
            self.cache = data if isinstance(data, dict) else {}
        except Exception:
            self.cache = {}
        self.cache.setdefault('history', [])
        self.cache.setdefault('favorites', [])
        self.cache.setdefault('folders', [])

    def save_cache(self):
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_file.with_suffix('.tmp')
            tmp.write_text(json.dumps(self.cache, ensure_ascii=False), encoding='utf-8')
            tmp.replace(self.cache_file)
        except Exception as exc:
            self.status.text = '本地缓存保存失败：' + safe_text(exc, 100)

    def _request_json(self, session, url, params=None):
        response = session.get(url, params=params or {}, timeout=18)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get('code') != 0:
            raise RuntimeError((payload or {}).get('message') or (payload or {}).get('msg') or 'B站接口请求失败')
        return payload.get('data') or {}

    def _history_item(self, row):
        h = row.get('history') or {}
        return {'bvid': row.get('bvid') or h.get('bvid') or '', 'aid': row.get('aid') or h.get('oid') or 0,
                'cid': h.get('cid') or row.get('cid') or 0, 'title': row.get('title') or '',
                'author': row.get('author_name') or row.get('owner', {}).get('name') or '',
                'cover': row.get('cover') or '', 'duration': row.get('duration') or 0,
                'view_at': h.get('view_at') or row.get('view_at') or 0, 'progress': h.get('progress') or 0,
                'source': 'history'}

    def _favorite_item(self, row, folder_name=''):
        owner = row.get('upper') or row.get('owner') or {}
        return {'bvid': row.get('bvid') or '', 'aid': row.get('id') or row.get('aid') or 0,
                'cid': row.get('cid') or 0, 'title': row.get('title') or '',
                'author': owner.get('name') or row.get('upper_name') or '', 'cover': row.get('cover') or row.get('pic') or '',
                'duration': row.get('duration') or 0, 'folder': folder_name, 'source': 'favorites'}

    def sync_all(self, *_):
        if not self.app_ref.get_cookie():
            self.status.text = '请先在「我的」页面完成 B 站授权登录。'; return
        self.status.text = '正在同步历史记录和收藏夹…'
        def worker():
            try:
                session = new_session(self.app_ref.get_cookie())
                # A live nav check prevents stale cached usernames being treated as authenticated.
                nav = get_nav(session)
                if not nav:
                    raise RuntimeError('登录 Cookie 已失效，请重新登录')
                mid = int(nav.get('mid') or self.app_ref.profile_data.get('mid') or 0)
                history = []
                cursor = {'max': 0, 'view_at': 0, 'business': 'archive', 'ps': 30}
                for _ in range(8):
                    data = self._request_json(session, 'https://api.bilibili.com/x/web-interface/history/cursor', cursor)
                    rows = data.get('list') or []
                    history.extend(self._history_item(x) for x in rows if isinstance(x, dict))
                    c = data.get('cursor') or {}
                    if not rows or not c or int(c.get('view_at') or 0) == int(cursor.get('view_at') or 0) and int(c.get('max') or 0) == int(cursor.get('max') or 0):
                        break
                    cursor = {'max': c.get('max', 0), 'view_at': c.get('view_at', 0), 'business': c.get('business') or 'archive', 'ps': 30}
                folders, favorites = [], []
                if mid:
                    fdata = self._request_json(session, 'https://api.bilibili.com/x/v3/fav/folder/created/list-all', {'up_mid': mid, 'ps': 50, 'pn': 1})
                    folders = (fdata.get('list') or []) if isinstance(fdata, dict) else []
                    for folder in folders[:30]:
                        fid = folder.get('id') or folder.get('media_id')
                        if not fid: continue
                        try:
                            fcontent = self._request_json(session, 'https://api.bilibili.com/x/v3/fav/resource/list', {'media_id': fid, 'pn': 1, 'ps': 100, 'order': 'mtime', 'type': 0})
                            media = fcontent.get('medias') or []
                            favorites.extend(self._favorite_item(x, folder.get('title') or '') for x in media if isinstance(x, dict))
                        except Exception:
                            continue
                # De-duplicate by video id, keeping newest item.
                def dedupe(items):
                    out, seen = [], set()
                    for x in items:
                        key = x.get('bvid') or str(x.get('aid') or '')
                        if key and key not in seen: seen.add(key); out.append(x)
                    return out
                new_cache = {'history': dedupe(history), 'favorites': dedupe(favorites),
                             'folders': [{'id': f.get('id') or f.get('media_id'), 'title': f.get('title') or '收藏夹', 'media_count': f.get('media_count') or 0} for f in folders],
                             'synced_at': int(time.time())}
                @mainthread
                def done():
                    self.cache = new_cache; self.save_cache(); self.set_mode(self.mode)
                    self.status.text = '同步完成：历史 %d 条 · 收藏视频 %d 条 · 收藏夹 %d 个' % (len(new_cache['history']), len(new_cache['favorites']), len(new_cache['folders']))
                    self.render_items()
                done()
            except Exception as exc:
                msg = safe_text(exc, 220)
                @mainthread
                def failed(): self.status.text = '同步失败：' + msg
                failed()
        threading.Thread(target=worker, daemon=True, name='bili-sync').start()

    def search(self, *_):
        keyword = safe_text(self.search_input.text.strip(), 100)
        if not keyword:
            self.render_items(); return
        if self.mode != 'online':
            source = self.cache.get(self.mode, []) if self.mode in ('history','favorites') else []
            self.items = [x for x in source if keyword.lower() in (str(x.get('title',''))+' '+str(x.get('author',''))+' '+str(x.get('folder',''))).lower()]
            self.render_items(self.items)
            self.status.text = '本地搜索：找到 %d 条' % len(self.items)
            return
        if not self.app_ref.get_cookie():
            self.status.text = '在线搜索暂时无需登录，但请检查网络。正在搜索…'
        self.status.text = '正在搜索 B 站…'
        def worker():
            try:
                session = new_session(self.app_ref.get_cookie())
                data = self._request_json(session, 'https://api.bilibili.com/x/web-interface/search/type', {'search_type':'video','keyword':keyword,'page':1,'order':'totalrank'})
                rows = data.get('result') or []
                items = [{'bvid':x.get('bvid') or '', 'aid':x.get('aid') or 0, 'cid':0,
                          'title':re.sub(r'<[^>]+>', '', x.get('title') or ''), 'author':x.get('author') or '',
                          'cover':x.get('pic') or '', 'duration':x.get('duration') or '', 'source':'online'} for x in rows]
                @mainthread
                def done():
                    self.items = items; self.render_items(items); self.status.text = 'B站搜索结果：%d 条' % len(items)
                done()
            except Exception as exc:
                msg = safe_text(exc, 200)
                @mainthread
                def failed(): self.status.text = '搜索失败：' + msg
                failed()
        threading.Thread(target=worker, daemon=True, name='bili-online-search').start()

    def render_items(self, items=None):
        self.list_box.clear_widgets()
        if items is None:
            items = self.cache.get(self.mode, []) if self.mode in ('history','favorites') else []
        items = items or []
        if not items:
            card = Card(bg=WHITE, radius=dp(17), padding=dp(16), size_hint_y=None, height=dp(110))
            card.add_widget(ui_label('暂无内容', 16, INK, 28, halign='center'))
            card.add_widget(ui_label('点击「同步账号数据」获取历史记录和收藏，或切换到「B站搜索」。', 11, GRAY, 42, halign='center'))
            self.list_box.add_widget(card); return
        for item in items[:300]:
            card = Card(bg=WHITE, radius=dp(15), padding=[dp(11), dp(9), dp(11), dp(9)], spacing=dp(4), size_hint_y=None, height=dp(102))
            title = safe_text(item.get('title') or '无标题', 140)
            card.add_widget(ui_label(title, 12, INK, 36, bold=True))
            meta = safe_text(item.get('author') or '', 80)
            if item.get('folder'): meta += ' · ' + safe_text(item.get('folder'), 50)
            if item.get('view_at'): meta += ' · ' + time.strftime('%Y-%m-%d', time.localtime(int(item['view_at'])))
            card.add_widget(ui_label(meta or (item.get('bvid') or 'B站视频'), 10, GRAY, 20, bold=False))
            row = BoxLayout(size_hint_y=None, height=dp(30), spacing=dp(6))
            play = PinkButton(text='播放', bg=PALE, fg=PINK, font_size=dp(11))
            play.bind(on_release=lambda _b, x=item: self.open_item(x))
            download = PinkButton(text='解析并下载', bg=PINK, fg=WHITE, font_size=dp(11))
            download.bind(on_release=lambda _b, x=item: self.download_item(x))
            row.add_widget(play); row.add_widget(download); card.add_widget(row)
            self.list_box.add_widget(card)

    def open_item(self, item):
        bvid = item.get('bvid')
        if not bvid:
            self.status.text = '该结果没有可用 BV 号。'; return
        # Route through the existing parser/player flow so the normal download UI remains intact.
        self.app_ref.navigate('home')
        self.app_ref.home.link_input.text = bvid
        self.app_ref.home.parse()
        self.status.text = '已将视频交给首页解析；解析完成后可在线播放。'

    def download_item(self, item):
        self.open_item(item)
        self.status.text = '已填入首页解析框；请在解析完成后确认清晰度并下载。'


class BiliDlApp(App):
    """bilibilidl Android UI with an in-app native WebView player overlay."""
    def build(self):
        try:
            from kivy.core.window import Window
            Window.clearcolor = BG
            Window.clearcolor = (0.965, 0.961, 0.969, 1)
        except Exception:
            pass
        self.title = APP_NAME
        self.cookie_data = ''
        self.profile_data = {}
        self._last_download_bvid = ''
        self.cookie_file = Path(self.user_data_dir) / 'cookie_store.json'
        self.sm = ScreenManager()
        self.home = HomeScreen(self, name='home')
        self.downloaded = DownloadedScreen(self, name='downloaded')
        self.profile = ProfileScreen(self, name='profile')
        self.sync_screen = BiliSyncScreen(self, name='sync')
        self.sm.add_widget(self.home)
        self.sm.add_widget(self.downloaded)
        self.sm.add_widget(self.profile)
        self.sm.add_widget(self.sync_screen)
        shell = BoxLayout(orientation='vertical', spacing=0)
        shell.add_widget(self.sm)
        nav = BoxLayout(size_hint_y=None, height=dp(66), padding=[dp(8), dp(5), dp(8), dp(5)], spacing=dp(6))
        self.nav_buttons = {}
        for key, label in [('home','首页'),('downloaded','已下载'),('sync','B站同步'),('profile','我的')]:
            btn = PinkButton(text=label, bg=WHITE, fg=GRAY, size_hint_x=1, height=dp(54))
            btn.bind(on_release=lambda _b, dest=key: self.navigate(dest))
            self.nav_buttons[key]=btn
            nav.add_widget(btn)
        shell.add_widget(nav)
        self.root_shell = shell
        self.update_nav('home')
        try:
            self.load_cookie_local()
        except Exception:
            pass
        # Java 视频合并器预加载失败不能阻断主界面启动。
        if platform == 'android':
            try:
                preload_merger()
            except Exception:
                pass
        return self.root_shell

    def navigate(self, destination):
        if destination not in ('home','downloaded','sync','profile'):
            return
        self.sm.current = destination
        if destination == 'downloaded':
            self.downloaded.refresh_files()
        elif destination == 'sync':
            self.sync_screen.load_cache()
            self.sync_screen.render_items()
        elif destination == 'profile':
            self.profile.refresh_profile()
        self.update_nav(destination)

    def update_nav(self, active):
        for key, btn in getattr(self, 'nav_buttons', {}).items():
            btn.bg = PINK if key == active else (1.0, 0.95, 0.97, 1)
            btn.fg = WHITE if key == active else (0.38, 0.39, 0.44, 1)
            btn.bold = True
            btn.font_size = dp(16 if key == active else 13)
            try:
                btn.opacity = 1.0
            except Exception:
                pass

    def get_cookie(self):
        return self.cookie_data or ''

    def validate_saved_cookie(self):
        """Validate saved credentials against Bilibili; clear stale login state."""
        cookie = self.get_cookie()
        if not cookie:
            return False
        try:
            session = new_session(cookie)
            profile = get_nav(session)
            if not profile:
                self.cookie_data = ''
                self.profile_data = {}
                try:
                    if self.cookie_file.exists():
                        self.cookie_file.unlink()
                except Exception:
                    pass
                Clock.schedule_once(lambda _dt: self.home.refresh_account({}), 0)
                return False
            # Refresh name/UID/avatar from the live account response.
            fresh = {
                'uname': profile.get('uname') or 'B站用户',
                'mid': profile.get('mid') or 0,
                'vipType': profile.get('vipType') or 0,
                'face': profile.get('face') or ''
            }
            self.profile_data = fresh
            Clock.schedule_once(lambda _dt: self.home.refresh_account(fresh), 0)
            return True
        except Exception:
            # Network failure is not proof that Cookie expired; leave it intact.
            return False

    @mainthread
    def save_cookie_local(self, cookie, profile):
        self.cookie_data = safe_text(cookie, 20000)
        self.profile_data = profile if isinstance(profile, dict) else {}
        try:
            self.cookie_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cookie_file.with_suffix('.tmp')
            tmp.write_text(json.dumps({
                'cookie': self.cookie_data,
                'profile': self.profile_data
            }, ensure_ascii=False), encoding='utf-8')
            tmp.replace(self.cookie_file)
        except Exception as exc:
            try:
                self.home.result_label.text = (
                    '登录成功，但本地保存 Cookie 失败：' + safe_text(exc, 120)
                )
            except Exception:
                pass
        try:
            self.home.refresh_account(self.profile_data)
        except Exception:
            pass

    def load_cookie_local(self):
        try:
            if self.cookie_file.is_file():
                data = json.loads(self.cookie_file.read_text(encoding='utf-8'))
                self.cookie_data = safe_text(data.get('cookie') or '', 20000)
                profile = data.get('profile')
                self.profile_data = profile if isinstance(profile, dict) else {}
                if hasattr(self, 'home'):
                    self.home.refresh_account(self.profile_data)
        except Exception:
            self.cookie_data = ''
            self.profile_data = {}

    def on_pause(self):
        return True

    def on_stop(self):
        try:
            if hasattr(self, 'home') and self.home.busy:
                self.home.cancel_event.set()
        except Exception:
            pass


if __name__ == '__main__':
    BiliDlApp().run()
