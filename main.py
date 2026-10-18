# -*- coding: utf-8 -*-
"""bilibilidl: native Android-oriented Bilibili downloader UI."""
import os
import io
import json
import time
import threading
import traceback
from pathlib import Path

import requests
import qrcode
from PIL import Image as PILImage

from kivy.app import App
from kivy.clock import Clock
from kivy.metrics import dp
from kivy.utils import platform
from kivy.core.window import Window
from kivy.core.text import LabelBase
from kivy.core.image import Image as CoreImage
from kivy.uix.image import Image
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.uix.spinner import Spinner
from kivy.uix.progressbar import ProgressBar
from kivy.uix.popup import Popup
from kivy.graphics import Color, RoundedRectangle

from bili_api import (
    new_session,
    extract_video_id,
    get_video_info,
    get_play_info,
    get_nav,
    format_duration,
    BiliAPIError,
)
from login_manager import qr_generate, qr_poll, validate_login
from downloader import download_video, DownloadError


APP = 'bilibilidl'

# 字体文件放在 main.py 同级目录。
FONT_DIR = os.path.dirname(os.path.abspath(__file__))
CJK_FONT = os.path.join(FONT_DIR, 'NotoSansCJKsc-Regular.ttf')

if os.path.isfile(CJK_FONT):
    LabelBase.register(name='BiliPinkCJK', fn_regular=CJK_FONT)
else:
    CJK_FONT = 'Roboto'

APP_FONT = 'BiliPinkCJK' if CJK_FONT != 'Roboto' else 'Roboto'
APP_FONT_BOLD = APP_FONT

PINK = (0.94, 0.29, 0.52, 1)
PALE = (1, 0.94, 0.97, 1)
DARK = (0.14, 0.13, 0.15, 1)
MUTED = (0.48, 0.47, 0.5, 1)
WHITE = (1, 1, 1, 1)
BLUE = (0.35, 0.58, 0.78, 1)


def ui(fn):
    """把 UI 更新调度到 Kivy 主线程。"""
    Clock.schedule_once(lambda _dt: fn(), 0)


class RoundButton(Button):
    def __init__(self, bg=PINK, fg=WHITE, **kw):
        kw.setdefault('font_name', APP_FONT_BOLD)
        super().__init__(**kw)

        self.background_normal = ''
        self.background_down = ''
        self.background_color = (0, 0, 0, 0)
        self.color = fg
        self.bold = True
        self._bg = bg

        with self.canvas.before:
            Color(*self._bg)
            self._shape = RoundedRectangle(
                pos=self.pos,
                size=self.size,
                radius=[dp(18)],
            )

        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        self._shape.pos = self.pos
        self._shape.size = self.size


class AppRoot(BoxLayout):
    def __init__(self, app, **kw):
        super().__init__(
            orientation='vertical',
            padding=[dp(16), dp(12), dp(16), dp(8)],
            spacing=dp(8),
            **kw
        )

        self.app = app
        self.info = None
        self.play = None
        self.qualities = []
        self.busy = False

        self.cancel = threading.Event()
        self.login_cancel = threading.Event()
        self.login_popup = None

        self.account = self._load_json('account.json', None)
        self.history = self._load_json('history.json', [])
        self.current_page = 'home'

        self._build()
        self.show_page('home')

    def _label(
        self,
        text,
        size=14,
        color=DARK,
        height=None,
        bold=False,
        halign='left',
    ):
        """
        创建标签。

        修复重点：
        1. 将字号转换为有效数值。
        2. 避免把 height=None 传入 Kivy NumericProperty。
        3. 仅在高度有效时设置固定高度。
        """
        try:
            font_size = float(size)
            if font_size <= 0:
                font_size = 14.0
        except (TypeError, ValueError):
            font_size = 14.0

        try:
            fixed_height = (
                float(height) if height is not None else None
            )
            if fixed_height is not None and fixed_height <= 0:
                fixed_height = None
        except (TypeError, ValueError):
            fixed_height = None

        options = {
            'text': str(text),
            'font_name': APP_FONT,
            'font_size': dp(font_size),
            'color': color,
            'bold': bool(bold),
            'halign': halign,
            'valign': 'middle',
            'size_hint_y': None if fixed_height is not None else 1,
        }

        if fixed_height is not None:
            options['height'] = dp(fixed_height)

        widget = Label(**options)

        widget.bind(
            size=lambda obj, val: setattr(
                obj,
                'text_size',
                (max(1, val[0]), None),
            )
        )

        return widget

    def _load_json(self, name, default):
        try:
            path = os.path.join(self.app.user_data_dir, name)
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return default

    def _save_json(self, name, data):
        os.makedirs(self.app.user_data_dir, exist_ok=True)

        path = os.path.join(self.app.user_data_dir, name)
        tmp = path + '.tmp'

        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        os.replace(tmp, path)

    def _build(self):
        head = BoxLayout(
            size_hint_y=None,
            height=dp(44),
            spacing=dp(8),
        )

        head.add_widget(
            self._label('bilibilidl', 22, DARK, bold=True)
        )
        head.add_widget(Label(font_name=APP_FONT))

        account_name = (
            self.account.get('uname', '已登录')
            if self.account else 'B站授权登录'
        )

        self.login_btn = RoundButton(
            text=account_name,
            size_hint_x=None,
            width=dp(130),
            bg=PALE,
            fg=PINK,
        )
        self.login_btn.bind(
            on_release=lambda *_: self.show_login()
        )
        head.add_widget(self.login_btn)
        self.add_widget(head)

        self.body = BoxLayout(
            orientation='vertical',
            spacing=dp(8),
        )
        self.add_widget(self.body)

        nav = BoxLayout(
            size_hint_y=None,
            height=dp(54),
            spacing=dp(8),
        )

        for text, page in (
            ('主页', 'home'),
            ('已下载', 'history'),
            ('我的', 'mine'),
        ):
            button = RoundButton(
                text=text,
                bg=PALE,
                fg=PINK,
            )
            button.bind(
                on_release=lambda _, p=page: self.show_page(p)
            )
            nav.add_widget(button)

        self.add_widget(nav)

    def show_page(self, page):
        self.current_page = page
        self.body.clear_widgets()

        if page == 'home':
            self._home_page()
        elif page == 'history':
            self._history_page()
        else:
            self._mine_page()

    def _home_page(self):
        sc = ScrollView()

        col = BoxLayout(
            orientation='vertical',
            spacing=dp(10),
            size_hint_y=None,
        )
        col.bind(minimum_height=col.setter('height'))

        col.add_widget(
            self._label('B站视频下载', 20, DARK, 34, True)
        )
        col.add_widget(
            self._label(
                '支持 BV / AV / 视频链接 / b23.tv 短链接',
                12,
                MUTED,
                26,
            )
        )

        self.url_input = TextInput(
            hint_text='粘贴 BV 号、AV 号、B站视频链接或 b23.tv 短链接',
            multiline=True,
            size_hint_y=None,
            height=dp(84),
            padding=dp(12),
            font_name=APP_FONT,
            font_size=dp(15),
            foreground_color=DARK,
            background_normal='',
            background_active='',
            background_color=PALE,
            cursor_color=PINK,
        )
        col.add_widget(self.url_input)

        row = BoxLayout(
            size_hint_y=None,
            height=dp(48),
            spacing=dp(8),
        )

        self.parse_btn = RoundButton(text='解析视频')
        self.parse_btn.bind(
            on_release=lambda *_: self.parse_async()
        )
        row.add_widget(self.parse_btn)

        self.cover = Image(
            size_hint_x=None,
            width=dp(80),
            allow_stretch=True,
            keep_ratio=True,
        )
        row.add_widget(self.cover)
        col.add_widget(row)

        self.title_lbl = self._label(
            '解析后显示标题和视频信息',
            15,
            DARK,
            48,
            True,
        )
        col.add_widget(self.title_lbl)

        self.meta_lbl = self._label(
            '画质会按接口返回的实际可用列表显示。',
            12,
            MUTED,
            56,
        )
        col.add_widget(self.meta_lbl)

        col.add_widget(self._label('清晰度', 13, MUTED, 24))

        self.quality_spinner = Spinner(
            text='请先解析视频',
            values=(),
            font_name=APP_FONT,
            size_hint_y=None,
            height=dp(46),
            background_normal='',
            background_color=PALE,
            color=DARK,
        )
        col.add_widget(self.quality_spinner)

        col.add_widget(self._label('下载内容', 13, MUTED, 24))

        self.mode_spinner = Spinner(
            text='视频 + 音频（MP4）',
            font_name=APP_FONT,
            values=(
                '视频 + 音频（MP4）',
                '封面图片',
                '弹幕 XML',
                '字幕 SRT',
            ),
            size_hint_y=None,
            height=dp(46),
            background_normal='',
            background_color=PALE,
            color=DARK,
        )
        col.add_widget(self.mode_spinner)

        self.dl_btn = RoundButton(
            text='开始下载',
            size_hint_y=None,
            height=dp(52),
        )
        self.dl_btn.bind(
            on_release=lambda *_: self.start_download()
        )
        col.add_widget(self.dl_btn)

        self.progress = ProgressBar(
            max=100,
            value=0,
            size_hint_y=None,
            height=dp(8),
        )
        col.add_widget(self.progress)

        self.status = self._label(
            '下载文件先保存在应用私有目录，可在“已下载”中导出。',
            12,
            MUTED,
            52,
        )
        col.add_widget(self.status)

        self.log_lbl = self._label('', 11, MUTED, 68)
        col.add_widget(self.log_lbl)

        sc.add_widget(col)
        self.body.add_widget(sc)

    def parse_async(self):
        if self.busy:
            return

        text = self.url_input.text.strip()
        if not text:
            self._alert('请先粘贴 BV / AV 号或 B 站链接')
            return

        self.busy = True
        self.parse_btn.disabled = True
        self.status.text = '正在请求 B 站视频信息…'

        threading.Thread(
            target=self._parse_worker,
            args=(text,),
            daemon=True,
        ).start()

    def _parse_worker(self, text):
        try:
            cookie = (
                self.account.get('cookie', '')
                if self.account else ''
            )

            session = new_session(cookie)
            info = get_video_info(session, text)
            play = get_play_info(session, info, cookie)

            data = play['data']
            accepted = data.get('accept_quality') or []
            descriptions = data.get('accept_description') or []

            qlist = []
            logged_in = bool(
                self.account and self.account.get('cookie')
            )

            for q, desc in zip(accepted, descriptions):
                q = int(q)

                if q >= 80 and not logged_in:
                    continue

                qlist.append((q, str(desc)))

            if not qlist:
                videos = (data.get('dash') or {}).get('video', [])

                for item in videos:
                    q = int(item.get('id', 0))

                    if q >= 80 and not logged_in:
                        continue

                    qlist.append((q, str(q) + 'P'))

            if not qlist and data.get('durl'):
                qlist = [(0, '自动 / 当前可用画质')]

            self.info = info
            self.play = play
            self.qualities = qlist

            ui(lambda: self._parse_done(info, qlist))

        except Exception as e:
            message = str(e)
            ui(
                lambda msg=message:
                self._alert('解析失败：' + msg)
            )

        finally:
            self.busy = False

            ui(
                lambda: setattr(
                    self.parse_btn,
                    'disabled',
                    False,
                ) if hasattr(self, 'parse_btn') else None
            )

    def _parse_done(self, info, qlist):
        self.title_lbl.text = info.get('title', '未知标题')

        self.meta_lbl.text = (
            'UP主：%s  |  时长：%s\n'
            '播放：%s  弹幕：%s  点赞：%s\n%s'
        ) % (
            info.get('owner', '未知'),
            format_duration(info.get('duration', 0)),
            info.get('view', 0),
            info.get('danmaku', 0),
            info.get('like', 0),
            (info.get('description') or '')[:120],
        )

        labels = [
            '%s（%s）' % (desc, q)
            for q, desc in qlist
        ]

        self.quality_spinner.values = labels
        self.quality_spinner.text = (
            labels[0] if labels else '无可用画质'
        )

        logged_in = bool(
            self.account and self.account.get('cookie')
        )

        self.status.text = (
            '解析成功：%d 个当前可用画质。' % len(qlist)
        ) + (
            ' 未登录仅显示 720P 及以下；登录后可查看 1080P 及以上。'
            if not logged_in else ' 登录状态已启用。'
        )

        self.progress.value = 0

        if info.get('pic'):
            threading.Thread(
                target=self._load_cover,
                args=(info['pic'],),
                daemon=True,
            ).start()

    def _load_cover(self, url):
        try:
            response = requests.get(url, timeout=15)
            response.raise_for_status()

            content_type = response.headers.get(
                'content-type', ''
            ).lower()

            ext = 'png' if 'png' in content_type else 'jpg'
            texture = CoreImage(
                io.BytesIO(response.content),
                ext=ext,
            ).texture

            ui(
                lambda: setattr(self.cover, 'texture', texture)
                if hasattr(self, 'cover') else None
            )
        except Exception:
            pass

    def _downloads_dir(self):
        path = os.path.join(
            self.app.user_data_dir,
            'downloads',
        )
        os.makedirs(path, exist_ok=True)
        return path

    def start_download(self):
        if self.busy:
            return

        if not self.info:
            self._alert('请先解析视频，确认信息和可用画质后再下载。')
            self.parse_async()
            return

        mode = self.mode_spinner.text

        if mode in ('封面图片', '弹幕 XML', '字幕 SRT'):
            self.busy = True
            self.cancel.clear()
            self.dl_btn.disabled = True
            self.parse_btn.disabled = True

            threading.Thread(
                target=self._download_attachment,
                args=(mode,),
                daemon=True,
            ).start()
            return

        try:
            selected = self.quality_spinner.text
            quality = next(
                (
                    q for q, desc in self.qualities
                    if ('%s（%s）' % (desc, q)) == selected
                ),
                0,
            )
        except Exception:
            quality = 0

        if quality >= 80 and not (
            self.account and self.account.get('cookie')
        ):
            self._alert(
                '1080P 及以上画质需要先点击右上角“登录”，'
                '使用 B 站授权扫码登录。'
            )
            self.show_login()
            return

        self.busy = True
        self.cancel.clear()
        self.dl_btn.disabled = True
        self.parse_btn.disabled = True
        self.progress.value = 0

        threading.Thread(
            target=self._download_worker,
            args=(quality, mode),
            daemon=True,
        ).start()

    def _download_attachment(self, mode):
        try:
            if not self.info:
                raise RuntimeError('请先解析视频')

            cookie = (
                self.account.get('cookie', '')
                if self.account else ''
            )
            session = new_session(cookie)

            stem = (
                self.info.get('bvid')
                or 'av' + str(self.info.get('aid') or 'video')
            )

            if mode == '封面图片':
                url = self.info.get('pic')

                if not url:
                    raise RuntimeError('没有封面地址')

                response = session.get(url, timeout=25)
                response.raise_for_status()

                content = response.content
                name = 'cover_' + stem + '.jpg'
                mime = 'image/jpeg'

            elif mode == '弹幕 XML':
                url = (
                    'https://comment.bilibili.com/%s.xml'
                    % self.info.get('cid')
                )

                response = session.get(url, timeout=25)
                response.raise_for_status()

                content = response.content

                if not content or b'<i' not in content[:500]:
                    raise RuntimeError('B站未返回有效弹幕 XML')

                name = stem + '_danmaku.xml'
                mime = 'application/xml'

            else:
                params = {'cid': self.info.get('cid')}

                if self.info.get('bvid'):
                    params['bvid'] = self.info['bvid']

                response = session.get(
                    'https://api.bilibili.com/x/player/v2',
                    params=params,
                    timeout=25,
                )
                response.raise_for_status()

                payload = response.json()
                data = payload.get('data') or {}
                subtitles = (
                    (data.get('subtitle') or {}).get('subtitles')
                    or []
                )

                if not subtitles:
                    raise RuntimeError('该视频没有公开字幕')

                suburl = subtitles[0].get('subtitle_url') or ''

                if suburl.startswith('//'):
                    suburl = 'https:' + suburl

                if not suburl:
                    raise RuntimeError('字幕地址为空')

                subresponse = session.get(suburl, timeout=25)
                subresponse.raise_for_status()
                subdata = subresponse.json()

                def stamp(value):
                    value = max(0, float(value or 0))
                    hours = int(value // 3600)
                    minutes = int((value % 3600) // 60)
                    seconds = int(value % 60)
                    milliseconds = int(
                        (value - int(value)) * 1000
                    )

                    return '%02d:%02d:%02d,%03d' % (
                        hours,
                        minutes,
                        seconds,
                        milliseconds,
                    )

                lines = []

                for i, item in enumerate(
                    subdata.get('body') or [],
                    1,
                ):
                    lines.extend([
                        str(i),
                        stamp(item.get('from'))
                        + ' --> '
                        + stamp(item.get('to')),
                        str(item.get('content', '')),
                        '',
                    ])

                if not lines:
                    raise RuntimeError('字幕内容为空')

                content = (
                    '\ufeff' + '\n'.join(lines)
                ).encode('utf-8')

                name = stem + '_subtitle.srt'
                mime = 'application/x-subrip'

            path = os.path.join(self._downloads_dir(), name)

            with open(path, 'wb') as f:
                f.write(content)

            self._add_history(name, path, mime)

            ui(
                lambda msg=mode + '下载完成：' + name:
                setattr(self.status, 'text', msg)
            )

        except Exception as e:
            message = str(e)
            ui(
                lambda msg=message, m=mode:
                self._alert(m + '下载失败：' + msg)
            )

        finally:
            self.busy = False
            ui(self._enable)

    def _download_cover(self):
        return self._download_attachment('封面图片')

    def _download_worker(self, quality, mode):
        try:
            output_dir = self._downloads_dir()

            cookie = (
                self.account.get('cookie', '')
                if self.account else ''
            )

            def progress(done, total):
                pct = int(done * 100 / total) if total else 0

                ui(
                    lambda p=pct:
                    setattr(self.progress, 'value', p)
                )

                ui(
                    lambda d=done, t=total:
                    setattr(
                        self.status,
                        'text',
                        '下载中：%s / %s MB' % (
                            round(d / 1048576, 1),
                            round(t / 1048576, 1)
                            if t else '?',
                        ),
                    )
                )

            def log(line):
                ui(
                    lambda value=line[-250:]:
                    setattr(self.log_lbl, 'text', value)
                )

            path = download_video(
                self.info,
                cookie,
                quality,
                output_dir,
                progress=progress,
                cancel=self.cancel,
                log=log,
            )

            if platform == 'android':
                from jnius import autoclass

                PythonActivity = autoclass(
                    'org.kivy.android.PythonActivity'
                )
                Helper = autoclass(
                    'org.bilibilidl.MediaMerger'
                )

                exported = Helper.exportToDownloads(
                    PythonActivity.mActivity,
                    path,
                    os.path.basename(path),
                )

                if not str(exported).startswith('content://'):
                    raise RuntimeError(
                        '视频已下载到应用内部，但保存到手机 '
                        'Download 失败：' + str(exported)
                    )

                saved_location = '手机 Download/bilibilidl'
            else:
                saved_location = path

            self._add_history(
                os.path.basename(path),
                path,
                'video/mp4',
            )

            ui(
                lambda loc=saved_location, n=os.path.basename(path):
                setattr(
                    self.status,
                    'text',
                    '下载完成，已保存到 %s：%s' % (loc, n),
                )
            )

            ui(lambda: setattr(self.progress, 'value', 100))

        except Exception as e:
            message = str(e)

            ui(
                lambda msg=message:
                self._alert('下载失败：' + msg)
            )
            ui(
                lambda: setattr(
                    self.status,
                    'text',
                    '下载失败。查看提示并重新尝试。',
                )
            )

        finally:
            self.busy = False
            ui(self._enable)

    def _enable(self):
        if hasattr(self, 'dl_btn'):
            self.dl_btn.disabled = False

        if hasattr(self, 'parse_btn'):
            self.parse_btn.disabled = False

    def _add_history(self, name, path, mime):
        self.history = [
            item for item in self.history
            if item.get('path') != path
        ]

        self.history.insert(0, {
            'name': name,
            'path': path,
            'mime': mime,
            'created': int(time.time()),
            'title': (self.info or {}).get('title', name),
        })

        self.history = self.history[:200]
        self._save_json('history.json', self.history)

    def _history_page(self):
        sc = ScrollView()

        col = BoxLayout(
            orientation='vertical',
            spacing=dp(8),
            size_hint_y=None,
        )
        col.bind(minimum_height=col.setter('height'))

        col.add_widget(
            self._label('已下载', 20, DARK, 38, True)
        )
        col.add_widget(
            self._label(
                '视频下载完成后会自动保存到手机 '
                'Download/bilibilidl，无需申请存储权限。'
                '应用内保留副本用于历史记录。',
                12,
                MUTED,
                48,
            )
        )

        if not self.history:
            col.add_widget(
                self._label('还没有下载记录。', 14, MUTED, 60)
            )

        for item in self.history:
            card = BoxLayout(
                orientation='vertical',
                padding=dp(10),
                spacing=dp(5),
                size_hint_y=None,
                height=dp(112),
            )

            card.add_widget(
                self._label(
                    item.get('name', '文件'),
                    14,
                    DARK,
                    28,
                    True,
                )
            )
            card.add_widget(
                self._label(
                    item.get('title', ''),
                    11,
                    MUTED,
                    24,
                )
            )

            row = BoxLayout(
                size_hint_y=None,
                height=dp(38),
                spacing=dp(8),
            )

            export_btn = RoundButton(
                text='导出到手机',
                bg=PINK,
            )
            export_btn.bind(
                on_release=lambda _, it=item: self.export_file(it)
            )

            delete_btn = RoundButton(
                text='删除记录',
                bg=PALE,
                fg=PINK,
            )
            delete_btn.bind(
                on_release=lambda _, it=item: self.delete_history(it)
            )

            row.add_widget(export_btn)
            row.add_widget(delete_btn)
            card.add_widget(row)
            col.add_widget(card)

        sc.add_widget(col)
        self.body.add_widget(sc)

    def export_file(self, item):
        path = item.get('path', '')

        if not os.path.isfile(path):
            self._alert('找不到文件，可能已被系统清理。')
            return

        try:
            filename = item.get(
                'name',
                os.path.basename(path),
            )

            if platform == 'android':
                from jnius import autoclass

                PythonActivity = autoclass(
                    'org.kivy.android.PythonActivity'
                )
                Helper = autoclass(
                    'org.bilibilidl.MediaMerger'
                )

                result = Helper.exportToDownloads(
                    PythonActivity.mActivity,
                    path,
                    filename,
                )

                if not str(result).startswith('content://'):
                    raise RuntimeError(str(result))

            else:
                import shutil

                target = os.path.join(
                    str(Path.home() / 'Downloads'),
                    filename,
                )
                shutil.copy2(path, target)
                result = target

            self._alert('导出成功：\n' + str(result))

        except Exception as e:
            self._alert('导出失败：' + str(e))

    def delete_history(self, item):
        self.history = [
            entry for entry in self.history
            if entry.get('path') != item.get('path')
        ]

        self._save_json('history.json', self.history)
        self.show_page('history')

    def _mine_page(self):
        self.body.add_widget(
            self._label('我的', 22, DARK, 44, True)
        )
        self.body.add_widget(
            self._label(
                'bilibilidl · 仅在本机处理登录信息',
                13,
                MUTED,
                32,
            )
        )

        if self.account:
            self.body.add_widget(
                self._label(
                    '账号：%s\nUID：%s\nVIP 类型：%s' % (
                        self.account.get('uname', 'B站用户'),
                        self.account.get('mid', '未知'),
                        self.account.get('vipType', 0),
                    ),
                    15,
                    DARK,
                    80,
                )
            )

            button = RoundButton(
                text='退出登录并清除 Cookie',
                bg=PALE,
                fg=PINK,
                size_hint_y=None,
                height=dp(48),
            )
            button.bind(on_release=lambda *_: self.logout())
            self.body.add_widget(button)

        else:
            button = RoundButton(
                text='B站扫码登录',
                size_hint_y=None,
                height=dp(48),
            )
            button.bind(on_release=lambda *_: self.show_login())
            self.body.add_widget(button)

        self.body.add_widget(
            self._label(
                '说明：画质由 B 站接口实际返回结果决定；'
                '登录不代表拥有大会员专属权益。',
                12,
                MUTED,
                60,
            )
        )

    def _make_qr_texture(self, url):
        qr = qrcode.QRCode(box_size=8, border=3)
        qr.add_data(url)
        qr.make(fit=True)

        img = qr.make_image(
            fill_color='black',
            back_color='white',
        ).convert('RGB')

        buf = io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)

        return Cor
