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
from kivy.uix.progressbar import ProgressBar
from kivy.uix.image import Image, AsyncImage
from kivy.uix.widget import Widget
from kivy.graphics import Color, RoundedRectangle, Ellipse, Line

from bili_api import new_session, get_video_info, get_play_info
from login_manager import qr_generate, qr_poll, validate_login
from downloader import download_video, preload_merger, DownloadError, safe_name

BASE_DIR = Path(__file__).resolve().parent
APP_NAME = 'bilibilidl'
FONT_REG = BASE_DIR / 'NotoSansCJKsc-Regular.ttf'
FONT_BOLD = BASE_DIR / 'NotoSansCJKsc-Bold.ttf'

PINK = (0.96, 0.36, 0.56, 1)
PALE = (1.0, 0.93, 0.96, 1)
WHITE = (1, 1, 1, 1)
INK = (0.13, 0.13, 0.15, 1)
GRAY = (0.43, 0.43, 0.47, 1)
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
    """将文件导出到 Android 的 Download/bilibilidl 目录。"""
    source = _verified_file(path)

    if platform != 'android':
        return str(source)

    try:
        from jnius import autoclass

        PythonActivity = autoclass('org.kivy.android.PythonActivity')
        MediaMerger = autoclass('org.bilibilidl.MediaMerger')

        result = str(
            MediaMerger.exportToDownloads(
                PythonActivity.mActivity,
                str(source.resolve()),
                source.name
            )
        )

        if result.startswith('content://'):
            return result

        raise RuntimeError(
            result or '导出接口没有返回成功地址'
        )
    except Exception as exc:
        raise RuntimeError(
            '文件已生成，但导出到 Download/bilibilidl 失败。'
            '原文件仍在应用私有目录：' + str(source)
            + '；错误：' + safe_text(exc, 180)
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
             bold=False, halign='left'):
    lab = Label(
        text=safe_text(text),
        font_name=FONT_BOLD_NAME if bold else FONT,
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
            obj, 'text_size', (max(1, val[0]), None)
        )
    )
    return lab


class Card(BoxLayout):
    def __init__(self, bg=WHITE, radius=dp(18), **kwargs):
        super().__init__(**kwargs)
        self.orientation = kwargs.get('orientation', 'vertical')
        self.padding = kwargs.get('padding', dp(12))
        self.spacing = kwargs.get('spacing', dp(8))
        with self.canvas.before:
            Color(*bg)
            self._rect = RoundedRectangle(
                pos=self.pos, size=self.size, radius=[radius]
            )
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        self._rect.pos = self.pos
        self._rect.size = self.size


class PinkButton(Button):
    def __init__(self, bg=PINK, fg=WHITE, **kwargs):
        super().__init__(**kwargs)
        self.background_normal = ''
        self.background_down = ''
        self.background_color = (0, 0, 0, 0)
        self.color = fg
        self.font_name = FONT
        self.font_size = dp(14)
        with self.canvas.before:
            Color(*bg)
            self._rect = RoundedRectangle(
                pos=self.pos, size=self.size, radius=[dp(16)]
            )
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        self._rect.pos = self.pos
        self._rect.size = self.size


class PinkInput(TextInput):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.background_normal = ''
        self.background_active = ''
        self.background_color = PALE
        self.foreground_color = INK
        self.cursor_color = PINK
        self.font_name = FONT
        self.font_size = dp(15)
        self.padding = [dp(12), dp(14), dp(12), dp(10)]


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


class LoginPopup(Popup):
    def __init__(self, app, **kwargs):
        super().__init__(
            title='B站授权登录',
            size_hint=(.9, .82),
            auto_dismiss=True,
            **kwargs
        )
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
        self.qr_image = Image(size_hint_y=None, height=dp(220))
        make = PinkButton(
            text='生成登录二维码',
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
        root.add_widget(self.qr_image)
        root.add_widget(make)
        root.add_widget(close)
        self.content = root
        self.bind(on_dismiss=lambda *_: self.stop_event.set())

    def generate(self, *_):
        self.stop_event.set()
        self.stop_event = threading.Event()
        self.status.text = '正在获取二维码…'
        stop = self.stop_event

        def work():
            try:
                session = new_session()
                url, key = qr_generate(session)

                qr = qrcode.QRCode(box_size=6, border=2)
                qr.add_data(url)
                qr.make(fit=True)
                image = qr.make_image(
                    fill_color='black',
                    back_color='white'
                ).convert('RGB')

                buf = io.BytesIO()
                image.save(buf, format='PNG')
                buf.seek(0)
                texture = CoreImage(buf, ext='png').texture

                @mainthread
                def show():
                    if not stop.is_set():
                        self.qr_key = key
                        self.qr_image.texture = texture
                        self.status.text = '请用 B 站 App 扫码确认登录'

                show()

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
        self.quality_index = 0
        self.mode_index = 0
        self.selected_page = 0
        self.all_pages = False
        self.cover_bytes = None
        self.cover_url = ''
        self.busy = False
        self.cancel_event = threading.Event()
        self._parse_generation = 0

        outer = BoxLayout(
            orientation='vertical',
            spacing=dp(8),
            padding=[dp(12), dp(8), dp(12), dp(8)]
        )
        header = BoxLayout(
            size_hint_y=None, height=dp(48), spacing=dp(8)
        )
        header.add_widget(
            ui_label('bilibilidl', 22, INK, 44, bold=True)
        )
        header.add_widget(Widget())
        self.account_btn = PinkButton(
            text='B站授权登录',
            bg=PALE,
            fg=INK,
            size_hint_x=None,
            width=dp(132),
            height=dp(42)
        )
        self.account_btn.bind(on_release=self.open_login)
        header.add_widget(self.account_btn)
        outer.add_widget(header)

        sc = ScrollView(do_scroll_x=False, bar_width=dp(3))
        content = BoxLayout(
            orientation='vertical',
            size_hint_y=None,
            spacing=dp(12),
            padding=[dp(2), dp(4), dp(2), dp(18)]
        )
        content.bind(minimum_height=content.setter('height'))

        self.link_input = PinkInput(
            hint_text='粘贴 BV / AV / 视频链接 / b23.tv',
            multiline=False,
            size_hint_y=None,
            height=dp(54)
        )
        content.add_widget(self.link_input)

        self.parse_btn = PinkButton(
            text='解析视频',
            size_hint_y=None,
            height=dp(50)
        )
        self.parse_btn.bind(on_release=self.parse)
        content.add_widget(self.parse_btn)

        self.result_label = ui_label(
            '输入视频链接后解析，清晰度与分P将按接口实际返回显示。',
            13, GRAY, 62, halign='center'
        )
        content.add_widget(self.result_label)

        self.cover_preview = Image(
            size_hint_y=None,
            height=dp(150),
            allow_stretch=True,
            keep_ratio=True
        )
        content.add_widget(self.cover_preview)

        self.title_label = ui_label('', 16, INK, 48, bold=True)
        content.add_widget(self.title_label)
        self.owner_label = ui_label('', 12, GRAY, 26)
        content.add_widget(self.owner_label)

        self.quality_btn = PinkButton(
            text='清晰度：请先解析',
            bg=PALE, fg=INK,
            size_hint_y=None, height=dp(46)
        )
        self.quality_btn.bind(on_release=self.choose_quality)
        content.add_widget(self.quality_btn)

        self.mode_btn = PinkButton(
            text=self.MODES[0],
            bg=PALE, fg=INK,
            size_hint_y=None, height=dp(46)
        )
        self.mode_btn.bind(on_release=self.choose_mode)
        content.add_widget(self.mode_btn)

        part_row = BoxLayout(
            size_hint_y=None, height=dp(42), spacing=dp(8)
        )
        part_row.add_widget(ui_label('分P列表', 15, INK, 36, bold=True))
        part_row.add_widget(Widget())
        self.all_btn = PinkButton(
            text='全部分P：关闭',
            bg=PALE, fg=INK,
            size_hint_x=None, width=dp(145), height=dp(38)
        )
        self.all_btn.bind(on_release=self.toggle_all)
        part_row.add_widget(self.all_btn)
        content.add_widget(part_row)

        self.part_list = BoxLayout(
            orientation='vertical',
            size_hint_y=None,
            spacing=dp(6)
        )
        self.part_list.bind(minimum_height=self.part_list.setter('height'))
        content.add_widget(self.part_list)

        self.download_btn = PinkButton(
            text='开始下载',
            size_hint_y=None, height=dp(54)
        )
        self.download_btn.bind(on_release=self.start_download)
        content.add_widget(self.download_btn)

        self.progress = ProgressBar(
            max=100, value=0,
            size_hint_y=None, height=dp(8)
        )
        content.add_widget(self.progress)
        self.task_label = ui_label('准备就绪', 12, GRAY, 44)
        content.add_widget(self.task_label)

        self.cancel_btn = PinkButton(
            text='取消当前任务',
            bg=(.78, .78, .8, 1),
            size_hint_y=None, height=dp(42),
            disabled=True
        )
        self.cancel_btn.bind(on_release=self.cancel_download)
        content.add_widget(self.cancel_btn)

        self.history_btn = PinkButton(
            text='打开下载目录',
            bg=PALE, fg=INK,
            size_hint_y=None, height=dp(42)
        )
        self.history_btn.bind(on_release=self.show_download_location)
        content.add_widget(self.history_btn)

        sc.add_widget(content)
        outer.add_widget(sc)
        self.add_widget(outer)
        self.refresh_parts()

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

                play = get_play_info(session, info, cookie)
                data = play.get('data') or {}
                qns = data.get('accept_quality') or []
                descs = data.get('accept_description') or []
                qlist = []

                for i, qn in enumerate(qns):
                    try:
                        qn = int(qn)
                    except Exception:
                        continue

                    desc = safe_text(
                        descs[i] if i < len(descs)
                        else ('画质 ' + str(qn)),
                        50
                    )
                    if qn >= 80 and not cookie:
                        continue
                    qlist.append((qn, desc))

                if not qlist:
                    qlist = [(0, '接口默认画质')]

                cover_url = safe_text(info.get('pic'), 2048)
                if cover_url.startswith('//'):
                    cover_url = 'https:' + cover_url

                cover = None
                if cover_url.startswith(('https://', 'http://')):
                    try:
                        response = session.get(
                            cover_url,
                            timeout=(8, 15),
                            headers={
                                'Referer': 'https://www.bilibili.com/'
                            }
                        )
                        response.raise_for_status()
                        if len(response.content) <= 20 * 1024 * 1024:
                            cover = response.content
                    except Exception:
                        pass

                @mainthread
                def done():
                    if generation != self._parse_generation:
                        return

                    self.info = info
                    self.pages = pages
                    self.qualities = qlist
                    self.quality_index = 0
                    self.selected_page = 0
                    self.all_pages = False
                    self.cover_url = cover_url
                    self.cover_bytes = cover

                    self.title_label.text = safe_text(
                        info.get('title'), 180
                    )
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

                    self.quality_btn.text = (
                        '清晰度：' + qlist[0][1]
                        + (' (%s)' % qlist[0][0] if qlist[0][0] else '')
                    )

                    self.cover_preview.source = ''
                    if cover:
                        try:
                            self._set_cover_preview(cover)
                        except Exception:
                            self.cover_preview.source = ''

                    self.result_label.text = (
                        '解析成功：%d 个分P。可选择单P或全部分P。'
                        % len(pages)
                    )
                    self.all_btn.text = '全部分P：关闭'
                    self.refresh_parts()
                    self.parse_btn.disabled = False

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
    @mainthread
    def _set_cover_preview(self, raw):
        try:
            from PIL import Image as PILImage
            im = PILImage.open(io.BytesIO(raw))
            im.seek(0)
            im.thumbnail((720, 420))
            buf = io.BytesIO()
            im.convert('RGB').save(buf, format='JPEG', quality=82)
            buf.seek(0)
            self.cover_preview.texture = CoreImage(
                buf, ext='jpg'
            ).texture
        except Exception:
            self.cover_preview.texture = None

    def choose_quality(self, *_):
        if not self.qualities:
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
        quality = (
            self.qualities[self.quality_index][0]
            if self.qualities else 0
        )
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
        # ---------------- HTML <-> Python local API ----------------
# HTML 前端通过 Android WebAppBridge.call(JSON) 调用此本地接口。
# 本地接口只监听 127.0.0.1，不需要外部服务器。

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
                qualities = []

                for i, q in enumerate(ids):
                    try:
                        qn = int(q)
                    except Exception:
                        continue

                    if not cookie and qn >= 80:
                        continue

                    qualities.append({
                        'qn': qn,
                        'label': str(
                            desc[i] if i < len(desc) else qn
                        )
                    })

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

                # 支持：全部分P、单个分P、多个分P编号列表
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

                            except Exception as exc:
                                failures.append(
                                    'P%d：%s'
                                    % (page_no, safe_text(exc, 220))
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


class BiliDlApp(App):
    def build(self):
        self.title = APP_NAME
        self.cookie_data = ''
        self.profile_data = {}
        self.cookie_file = (
        Path(self.user_data_dir) / 'cookie_store.json'
    )

    self.sm = ScreenManager()
    self.home = HomeScreen(self, name='home')
    self.sm.add_widget(self.home)

    self.load_cookie_local()

    self._bridge_last_info = None
    self._bridge_last_pages = []
    self._bridge_last_qualities = []

    self._bridge_server = _BridgeServer(self)
    self._bridge_server.start()

    if platform == 'android':
        try:
            preload_merger()
        except Exception:
            pass

        html_file = BASE_DIR / 'index.html'
        splash_file = BASE_DIR / 'bilibilidl加载图.png'

        if html_file.is_file() and splash_file.is_file():
            self._startup_view = FloatLayout()
            self._startup_image = Image(
                source=str(splash_file),
                allow_stretch=True,
                keep_ratio=False,
                size_hint=(1, 1),
                pos_hint={'x': 0, 'y': 0}
            )
            self._startup_view.add_widget(
                self._startup_image
            )

            Clock.schedule_once(
                lambda _dt: self.open_html_frontend(html_file),
                0.25
            )
            return self._startup_view

        return self.sm

    return self.sm

def open_html_frontend(self, html_file):
    try:
        from jnius import autoclass
        from android.runnable import run_on_ui_thread

        PythonActivity = autoclass(
            'org.kivy.android.PythonActivity'
        )
        Bridge = autoclass(
            'org.bilibilidl.WebAppBridge'
        )

        activity = PythonActivity.mActivity
        html_uri = Path(html_file).resolve().as_uri()
        api_url = 'http://127.0.0.1:18765/api'

        @run_on_ui_thread
        def show_webview():
            Bridge.open(activity, html_uri, api_url)

        show_webview()

    except Exception as exc:
        message = (
            'HTML 界面启动失败：'
            + safe_text(exc, 300)
        )
        try:
            self.home.result_label.text = message
            self.root = self.sm
        except Exception:
            pass

def get_cookie(self):
    return self.cookie_data or ''

    @mainthread
    def save_cookie_local(self, cookie, profile):
        self.cookie_data = safe_text(cookie, 20000)
        self.profile_data = (
            profile if isinstance(profile, dict) else {}
        )

        try:
            self.cookie_file.parent.mkdir(
                parents=True, exist_ok=True
            )
            tmp = self.cookie_file.with_suffix('.tmp')
            tmp.write_text(
                json.dumps({
                    'cookie': self.cookie_data,
                    'profile': self.profile_data
                }, ensure_ascii=False),
                encoding='utf-8'
            )
            tmp.replace(self.cookie_file)

        except Exception as exc:
            self.home.result_label.text = (
                '登录成功，但本地保存 Cookie 失败：'
                + safe_text(exc, 120)
            )

        self.home.refresh_account(self.profile_data)

    def load_cookie_local(self):
        try:
            if self.cookie_file.is_file():
                data = json.loads(
                    self.cookie_file.read_text(encoding='utf-8')
                )
                self.cookie_data = safe_text(
                    data.get('cookie') or '', 20000
                )
                profile = data.get('profile')
                self.profile_data = (
                    profile if isinstance(profile, dict) else {}
                )
                self.home.refresh_account(self.profile_data)

        except Exception:
            self.cookie_data = ''
            self.profile_data = {}

    def on_pause(self):
        return True

    def on_stop(self):
        try:
            if hasattr(self, '_bridge_server'):
                self._bridge_server.stop()
        except Exception:
            pass

        try:
            if hasattr(self, 'home') and self.home.busy:
                self.home.cancel_event.set()
        except Exception:
            pass


if __name__ == '__main__':
    BiliDlApp().run()
