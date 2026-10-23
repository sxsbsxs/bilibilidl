# -*- coding: utf-8 -*-
"""bilibilidl: native Android-oriented Bilibili downloader UI."""
import os
import io
import json
import time
import threading
from pathlib import Path

import requests
import qrcode
from PIL import Image as PILImage, ImageFile

from kivy.app import App
from kivy.clock import Clock
from kivy.animation import Animation
from kivy.metrics import dp
from kivy.utils import platform
from kivy.core.window import Window
from kivy.core.clipboard import Clipboard
from kivy.core.text import LabelBase
from kivy.core.image import Image as CoreImage
from kivy.uix.image import Image
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.uix.spinner import Spinner, SpinnerOption
from kivy.uix.progressbar import ProgressBar
from kivy.uix.popup import Popup
from kivy.graphics import Color, RoundedRectangle

from bili_api import (new_session, get_video_info, get_play_info, format_duration,
                      get_popular_videos, get_comments, get_danmaku,
                      get_stream_urls, like_video, coin_video,
                      favorite_video, follow_user)
from login_manager import qr_generate, qr_poll, validate_login
from downloader import download_video, preload_merger

APP = 'bilibilidl'
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _find_cjk_font():
    preferred = [
        'NotoSansCJKsc-Regular.ttf', 'NotoSansCJKsc-Regular.otf',
        'NotoSansSC-Regular.ttf', 'NotoSansSC-Regular.otf',
        'NotoSansSC.ttf', 'NotoSansSC[wght].ttf',
    ]
    folders = [BASE_DIR, os.path.join(BASE_DIR, 'fonts')]
    for folder in folders:
        for name in preferred:
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                return path
    for folder in folders:
        try:
            for name in sorted(os.listdir(folder)):
                if name.lower().endswith(('.ttf', '.otf')):
                    return os.path.join(folder, name)
        except OSError:
            pass
    return None


APP_FONT = 'Roboto'
APP_FONT_BOLD = 'Roboto'
_font_path = _find_cjk_font()
if _font_path:
    try:
        LabelBase.register(name='BiliPinkCJK', fn_regular=_font_path)
        APP_FONT = APP_FONT_BOLD = 'BiliPinkCJK'
    except Exception:
        APP_FONT = APP_FONT_BOLD = 'Roboto'
try:
    SpinnerOption.font_name = APP_FONT
except Exception:
    pass
print('[bilibilidl] 字体路径: %s' % _font_path if _font_path else
      '[bilibilidl] 未找到中文字体文件，使用 Roboto（中文将显示为方块）')

PINK = (0.94, 0.29, 0.52, 1)
PALE = (1, 0.94, 0.97, 1)
DARK = (0.14, 0.13, 0.15, 1)
MUTED = (0.48, 0.47, 0.5, 1)
WHITE = (1, 1, 1, 1)
BLUE = (0.35, 0.58, 0.78, 1)

if platform == 'android':
    from android.runnable import run_on_ui_thread
else:
    def run_on_ui_thread(fn):
        return fn


@run_on_ui_thread
def style_system_bars():
    try:
        from jnius import autoclass
        PythonActivity = autoclass('org.kivy.android.PythonActivity')
        LayoutParams = autoclass('android.view.WindowManager$LayoutParams')
        Color = autoclass('android.graphics.Color')
        VERSION = autoclass('android.os.Build$VERSION')
        window = PythonActivity.mActivity.getWindow()
        window.clearFlags(0x04000000 | 0x08000000)
        window.addFlags(LayoutParams.FLAG_DRAWS_SYSTEM_BAR_BACKGROUNDS)
        white = Color.parseColor('#FFFFFF')
        window.setStatusBarColor(white)
        window.setNavigationBarColor(white)
        flags = 0x2000
        if VERSION.SDK_INT >= 26:
            flags |= 0x10
        window.getDecorView().setSystemUiVisibility(flags)
    except Exception:
        pass


def ui(fn):
    Clock.schedule_once(lambda _dt: fn(), 0)


def make_image(**kw):
    try:
        return Image(fit_mode='contain', **kw)
    except Exception:
        return Image(allow_stretch=True, keep_ratio=True, **kw)


class RoundButton(Button):
    def __init__(self, bg=PINK, fg=WHITE, **kw):
        kw.setdefault('font_name', APP_FONT_BOLD)
        super().__init__(**kw)
        self.background_normal = ''
        self.background_down = ''
        self.background_color = (0, 0, 0, 0)
        self.color = fg
        self._bg = bg
        with self.canvas.before:
            Color(*self._bg)
            self._shape = RoundedRectangle(pos=self.pos, size=self.size,
                                           radius=[dp(18)])
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        self._shape.pos = self.pos
        self._shape.size = self.size


class AppRoot(BoxLayout):
    def __init__(self, app, **kw):
        super().__init__(orientation='vertical',
                         padding=[dp(16), dp(12), dp(16), dp(8)],
                         spacing=dp(8), **kw)
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

    def _label(self, text, size=14, color=DARK, height=None,
               bold=False, halign='left'):
        kw = dict(text=str(text), font_name=APP_FONT, font_size=dp(size),
                  color=color, bold=bold, halign=halign, valign='middle')
        if height:
            kw['size_hint_y'] = None
            kw['height'] = dp(height)
        w = Label(**kw)
        w.bind(size=lambda obj, val: setattr(
            obj, 'text_size', (max(1, val[0]), None)))
        return w

    def _load_json(self, name, default):
        try:
            with open(os.path.join(self.app.user_data_dir, name),
                      'r', encoding='utf-8') as f:
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

    def _logged_in(self):
        return bool(self.account and self.account.get('cookie'))

    def _build(self):
        head = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        head.add_widget(self._label('bilibilidl', 22, DARK, bold=True))
        head.add_widget(Label(font_name=APP_FONT))
        self.login_btn = RoundButton(
            text=(self.account.get('uname', '已登录')
                  if self.account else 'B站授权登录'),
            size_hint_x=None, width=dp(130), bg=PALE, fg=PINK)
        self.login_btn.bind(on_release=lambda *_: self.show_login())
        head.add_widget(self.login_btn)
        self.add_widget(head)
        self.body = BoxLayout(orientation='vertical', spacing=dp(8))
        self.add_widget(self.body)
        nav = BoxLayout(size_hint_y=None, height=dp(54), spacing=dp(8))
        for text, page in [('首页', 'home'), ('下载', 'download'), ('已下载', 'history'),
                           ('我的', 'mine')]:
            b = RoundButton(text=text, bg=PALE, fg=PINK)
            b.bind(on_release=lambda _, p=page: self.show_page(p))
            nav.add_widget(b)
        self.add_widget(nav)

    def show_page(self, page):
        self.current_page = page
        self.body.clear_widgets()
        if page == 'home':
            self._feed_page()
        elif page == 'download':
            self._home_page()
        elif page == 'player':
            self._player_page()
        elif page == 'history':
            self._history_page()
        else:
            self._mine_page()

    def _home_page(self):
        sc = ScrollView()
        col = BoxLayout(orientation='vertical', spacing=dp(10),
                        size_hint_y=None)
        col.bind(minimum_height=col.setter('height'))
        col.add_widget(self._label('B站视频下载', 20, DARK, 34, True))
        col.add_widget(self._label(
            '支持 BV / AV / 视频链接 / b23.tv 短链接', 12, MUTED, 26))
        self.url_input = TextInput(
            hint_text='粘贴 BV 号、AV 号、B站视频链接或 b23.tv 短链接',
            multiline=True, size_hint_y=None, height=dp(84),
            padding=dp(12), font_name=APP_FONT, font_size=dp(15),
            foreground_color=DARK, background_normal='',
            background_active='', background_color=PALE, cursor_color=PINK)
        col.add_widget(self.url_input)
        row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        self.parse_btn = RoundButton(text='解析视频')
        self.parse_btn.bind(on_release=lambda *_: self.parse_async())
        row.add_widget(self.parse_btn)
        self.cover = make_image(size_hint_x=None, width=dp(80))
        row.add_widget(self.cover)
        col.add_widget(row)
        self.title_lbl = self._label('解析后显示标题和视频信息',
                                     15, DARK, 48, True)
        col.add_widget(self.title_lbl)
        self.meta_lbl = self._label(
            '画质会按接口返回的实际可用列表显示。', 12, MUTED, 56)
        col.add_widget(self.meta_lbl)
        col.add_widget(self._label('分P选择', 13, MUTED, 24))
        self.part_spinner = Spinner(
            text='请先解析视频', values=(), font_name=APP_FONT,
            size_hint_y=None, height=dp(46), background_normal='',
            background_color=PALE, color=DARK)
        col.add_widget(self.part_spinner)
        col.add_widget(self._label('清晰度', 13, MUTED, 24))
        self.quality_spinner = Spinner(
            text='请先解析视频', values=(), font_name=APP_FONT,
            size_hint_y=None, height=dp(46), background_normal='',
            background_color=PALE, color=DARK)
        col.add_widget(self.quality_spinner)
        col.add_widget(self._label('下载内容', 13, MUTED, 24))
        self.mode_spinner = Spinner(
            text='视频 + 音频（MP4）', font_name=APP_FONT,
            values=('视频 + 音频（MP4）', '封面图片', '弹幕 XML', '字幕 SRT'),
            size_hint_y=None, height=dp(46), background_normal='',
            background_color=PALE, color=DARK)
        col.add_widget(self.mode_spinner)
        self.dl_btn = RoundButton(text='开始下载',
                                  size_hint_y=None, height=dp(52))
        self.dl_btn.bind(on_release=lambda *_: self.start_download())
        col.add_widget(self.dl_btn)
        self.progress = ProgressBar(max=100, value=0,
                                    size_hint_y=None, height=dp(8))
        col.add_widget(self.progress)
        self.status = self._label(
            '下载文件先保存在应用私有目录，可在“已下载”中导出。',
            12, MUTED, 52)
        col.add_widget(self.status)
        self.log_lbl = self._label('', 11, MUTED, 68)
        col.add_widget(self.log_lbl)
        sc.add_widget(col)
        self.body.add_widget(sc)

    # ---------------------------- feed / player ---------------------------
    def _feed_page(self):
        """Bilibili public popular feed. Download tools remain on the Download tab."""
        sc = ScrollView()
        col = BoxLayout(orientation='vertical', spacing=dp(9), size_hint_y=None)
        col.bind(minimum_height=col.setter('height'))
        row = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(8))
        row.add_widget(self._label('推荐视频', 21, DARK, 38, True))
        refresh = RoundButton(text='刷新', bg=PALE, fg=PINK, size_hint_x=None, width=dp(76))
        refresh.bind(on_release=lambda *_: self._load_feed())
        row.add_widget(refresh)
        col.add_widget(row)
        col.add_widget(self._label('来自 B 站公开热门视频。点开卡片查看视频信息、弹幕和评论；下载功能仍在底部“下载”。', 12, MUTED, 48))
        self.feed_status = self._label('正在加载推荐视频…', 12, MUTED, 32)
        col.add_widget(self.feed_status)
        self.feed_list = BoxLayout(orientation='vertical', spacing=dp(8), size_hint_y=None)
        self.feed_list.bind(minimum_height=self.feed_list.setter('height'))
        col.add_widget(self.feed_list)
        sc.add_widget(col)
        self.body.add_widget(sc)
        self._load_feed()

    def _load_feed(self):
        if not hasattr(self, 'feed_status'):
            return
        self.feed_status.text = '正在获取 B 站推荐视频…'
        self.feed_list.clear_widgets()
        threading.Thread(target=self._feed_worker, daemon=True).start()

    def _feed_worker(self):
        try:
            session = new_session(self.account.get('cookie', '') if self.account else '')
            items = get_popular_videos(session, page=1, page_size=20)
            ui(lambda items=items: self._feed_done(items))
        except Exception as e:
            msg = str(e)
            ui(lambda msg=msg: setattr(
                self.feed_status, 'text',
                '推荐加载失败：%s。请检查网络后点刷新。' % msg[:100]
            ) if hasattr(self, 'feed_status') else None)

    def _feed_done(self, items):
        self.feed_list.clear_widgets()
        self._feed_items = {}
        if not items:
            self.feed_status.text = '暂时没有返回视频。'
            return
        self.feed_status.text = '已加载 %d 个视频' % len(items)
        for item in items:
            bvid = item.get('bvid') or ''
            aid = item.get('aid') or 0
            self._feed_items[bvid or str(aid)] = item
            card = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(4),
                             size_hint_y=None, height=dp(116))
            title = str(item.get('title') or '未命名视频').replace('&amp;', '&')
            card.add_widget(self._label(title, 14, DARK, 42, True))
            owner_value = item.get('owner') or {}
            owner = owner_value.get('name', '未知 UP 主') if isinstance(owner_value, dict) else str(owner_value)
            stat = item.get('stat') or {}
            views = stat.get('view', item.get('view', 0))
            danmaku_count = stat.get('danmaku', item.get('danmaku', 0))
            likes = stat.get('like', item.get('like', 0))
            card.add_widget(self._label('%s  ·  ▶ %s  ·  弹幕 %s  ·  👍 %s' % (
                owner, views, danmaku_count, likes), 11, MUTED, 24))
            open_btn = RoundButton(text='打开视频详情', size_hint_y=None, height=dp(38))
            open_btn.bind(on_release=lambda _, data=item: self.open_video(data))
            card.add_widget(open_btn)
            self.feed_list.add_widget(card)

    def open_video(self, item):
        self.current_video = dict(item)
        self.current_page = 'player'
        self.body.clear_widgets()
        self._player_page()

    def _player_page(self):
        """Video detail page with best-effort in-app playback and real account actions."""
        item = getattr(self, 'current_video', {}) or {}
        sc = ScrollView()
        col = BoxLayout(orientation='vertical', spacing=dp(8), size_hint_y=None)
        col.bind(minimum_height=col.setter('height'))
        back = RoundButton(text='← 返回推荐', bg=PALE, fg=PINK,
                           size_hint_y=None, height=dp(42))
        back.bind(on_release=lambda *_: self.show_page('home'))
        col.add_widget(back)
        title = str(item.get('title') or '视频详情')
        col.add_widget(self._label(title, 18, DARK, 58, True))
        owner_value = item.get('owner') or {}
        owner = owner_value.get('name', '未知 UP 主') if isinstance(owner_value, dict) else str(owner_value)
        stat = item.get('stat') or {}
        col.add_widget(self._label('UP主：%s\n播放：%s  弹幕：%s  点赞：%s' % (
            owner, stat.get('view', item.get('view', 0)),
            stat.get('danmaku', item.get('danmaku', 0)),
            stat.get('like', item.get('like', 0))), 12, MUTED, 54))

        # Stable Android playback strategy: hand the Bilibili page to Android's
        # ACTION_VIEW handler (official Bilibili app if installed, otherwise browser).
        # Kivy's embedded player is deliberately not used because it often fails on DASH.
        play_hint = self._label(
            '视频使用手机上的 B 站 App 或浏览器播放，兼容性比应用内播放器更稳定。',
            13, MUTED, 64)
        col.add_widget(play_hint)
        bvid = item.get('bvid') or ''
        aid = item.get('aid') or 0
        url = 'https://www.bilibili.com/video/%s' % bvid if bvid else 'https://www.bilibili.com/video/av%s' % aid
        play_row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        external = RoundButton(text='▶ 打开 B 站播放', size_hint_x=0.62)
        external.bind(on_release=lambda *_: self._open_video_url(url))
        copy_btn = RoundButton(text='复制视频链接', bg=PALE, fg=PINK, size_hint_x=0.38)
        copy_btn.bind(on_release=lambda *_: self._copy_video_url(url))
        play_row.add_widget(external)
        play_row.add_widget(copy_btn)
        col.add_widget(play_row)
        self.player_status = self._label('正在加载弹幕和评论…', 12, PINK, 36)
        col.add_widget(self.player_status)

        action_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(5))
        for text, action in [('点赞', 'like'), ('投币', 'coin'), ('收藏', 'favorite'), ('关注UP', 'follow')]:
            btn = RoundButton(text=text, bg=PALE, fg=PINK)
            btn.bind(on_release=lambda _, a=action: self._video_action(a))
            action_row.add_widget(btn)
        col.add_widget(action_row)
        col.add_widget(self._label('弹幕预览', 16, DARK, 32, True))
        self.danmaku_box = BoxLayout(orientation='vertical', spacing=dp(3), size_hint_y=None)
        self.danmaku_box.bind(minimum_height=self.danmaku_box.setter('height'))
        col.add_widget(self.danmaku_box)
        col.add_widget(self._label('最新评论', 16, DARK, 32, True))
        self.comment_box = BoxLayout(orientation='vertical', spacing=dp(5), size_hint_y=None)
        self.comment_box.bind(minimum_height=self.comment_box.setter('height'))
        col.add_widget(self.comment_box)
        dl = RoundButton(text='下载这个视频', bg=PINK, size_hint_y=None, height=dp(46))
        dl.bind(on_release=lambda *_: self._send_video_to_downloader(item))
        col.add_widget(dl)
        sc.add_widget(col)
        self.body.add_widget(sc)
        self._inline_play_url = None
        self._danmaku_events = []
        self._danmaku_index = 0
        threading.Thread(target=self._player_data_worker, args=(item,), daemon=True).start()

    def _start_inline_playback(self, item):
        """Compatibility fallback for older callbacks: open the official video page."""
        bvid = item.get('bvid') or ''
        aid = item.get('aid') or 0
        url = 'https://www.bilibili.com/video/%s' % bvid if bvid else 'https://www.bilibili.com/video/av%s' % aid
        self._open_video_url(url)

    def _copy_video_url(self, url):
        try:
            Clipboard.copy(str(url))
            self._alert('视频链接已复制')
        except Exception as e:
            self._alert('复制链接失败：' + str(e))

    def _video_action(self, action):
        item = getattr(self, 'current_video', {}) or {}
        if not self._logged_in():
            self._alert('请先使用 B 站授权登录，再执行点赞、投币、收藏或关注。')
            return
        threading.Thread(target=self._video_action_worker, args=(action, item), daemon=True).start()

    def _video_action_worker(self, action, item):
        try:
            cookie = self.account.get('cookie', '') if self.account else ''
            session = new_session(cookie)
            # Resolve full detail when a feed item lacks aid/cid/mid fields.
            bvid = item.get('bvid') or ''
            aid = item.get('aid')
            if not aid and bvid:
                detail = get_video_info(session, bvid)
                item = dict(detail)
                owner = {'mid': detail.get('mid')}
            else:
                owner = item.get('owner') or {}
                if not isinstance(owner, dict):
                    owner = {'mid': item.get('mid')}
            if action == 'like':
                like_video(session, item, like=True)
                label = '点赞成功'
            elif action == 'coin':
                coin_video(session, item, multiply=1, select_like=False)
                label = '投币成功'
            elif action == 'favorite':
                favorite_video(session, item, add=True)
                label = '收藏请求成功'
            elif action == 'follow':
                mid = owner.get('mid') or item.get('mid')
                if not mid:
                    detail = get_video_info(session, bvid or ('av' + str(aid)))
                    mid = detail.get('mid')
                follow_user(session, mid, follow=True)
                label = '关注请求成功'
            else:
                raise RuntimeError('未知操作')
            ui(lambda msg=label: self._alert(msg))
        except Exception as e:
            msg = str(e)
            ui(lambda msg=msg: self._alert('操作失败：' + msg[:180]))

    def _open_video_url(self, url):
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
        except Exception as e:
            self._alert('无法打开 B 站播放器：' + str(e))

    def _send_video_to_downloader(self, item):
        bvid = item.get('bvid') or ''
        aid = item.get('aid') or ''
        self.show_page('download')
        if hasattr(self, 'url_input'):
            self.url_input.text = bvid or ('av' + str(aid))
            self.parse_async()

    def _player_data_worker(self, item):
        try:
            cookie = self.account.get('cookie', '') if self.account else ''
            session = new_session(cookie)
            bvid = item.get('bvid') or ''
            aid = item.get('aid')
            # Normalize feed cards through the same detail parser used by downloads.
            if bvid or aid:
                detail = get_video_info(session, bvid or ('av' + str(aid)))
                item.update(detail)
            cid = item.get('cid')
            if not cid:
                pages = item.get('pages') or []
                if pages:
                    cid = pages[0].get('cid')
                    item['cid'] = cid
            # Playback is delegated to the official app/browser; no fragile DASH URL needed.
            play_url = None
            danmaku = []
            if cid:
                try:
                    danmaku = get_danmaku(session, cid, limit=1000)
                except Exception:
                    pass
            comments = []
            if item.get('aid'):
                try:
                    comments = get_comments(session, item.get('aid'), page=1, page_size=20)
                except Exception:
                    pass
            ui(lambda d=danmaku, c=comments, u=play_url: self._player_data_done(d, c, u))
        except Exception as e:
            msg = str(e)
            ui(lambda msg=msg: setattr(self.player_status, 'text',
                                       '弹幕/评论加载失败：' + msg[:90])
               if hasattr(self, 'player_status') else None)

    def _player_data_done(self, danmaku, comments, play_url=None):
        if not hasattr(self, 'danmaku_box'):
            return
        self._inline_play_url = play_url
        self.danmaku_box.clear_widgets()
        self.comment_box.clear_widgets()
        if danmaku:
            self._danmaku_events = sorted(
                [d for d in danmaku if isinstance(d, dict)],
                key=lambda d: float(d.get('time') or 0))
            self._danmaku_index = 0
            for entry in self._danmaku_events[:20]:
                self.danmaku_box.add_widget(self._label('💬 ' + str(entry.get('text', '')), 12, DARK, 26))
        else:
            self._danmaku_events = []
            self._danmaku_index = 0
            self.danmaku_box.add_widget(self._label('没有获取到公开弹幕，或该视频暂不提供弹幕 XML。', 12, MUTED, 34))
        if comments:
            for item in comments[:15]:
                member = item.get('member') or {}
                content = item.get('content') or {}
                uname = item.get('user') or member.get('uname', '用户')
                message = str(item.get('message') if item.get('message') is not None else content.get('message', '')).replace('\n', ' ')
                self.comment_box.add_widget(self._label('%s：%s' % (uname, message), 12, DARK, 48))
        else:
            self.comment_box.add_widget(self._label('没有获取到公开评论，稍后可刷新重试。', 12, MUTED, 34))
        self.player_status.text = '已读取 %d 条弹幕、%d 条评论。点击“打开 B 站播放”观看视频。' % (len(danmaku), len(comments))

    def _sync_danmaku_overlay(self, _dt):
        """Render timestamped XML danmaku over the inline video when it is playing."""
        try:
            video = getattr(self, 'inline_video', None)
            overlay = getattr(self, 'danmaku_overlay', None)
            events = getattr(self, '_danmaku_events', [])
            if not video or not overlay or not events or video.state != 'play':
                return
            position = float(getattr(video, 'position', 0) or 0)
            index = getattr(self, '_danmaku_index', 0)
            # Catch up after seeks without flooding the screen.
            while index < len(events) and float(events[index].get('time') or 0) < position - 1.5:
                index += 1
            shown = 0
            while index < len(events) and float(events[index].get('time') or 0) <= position + 0.35 and shown < 4:
                entry = events[index]
                text = str(entry.get('text') or '').strip()
                if text:
                    label = Label(text=text, font_name=APP_FONT, font_size=dp(14),
                                  color=(1, 1, 1, 1), outline_width=dp(1.2),
                                  outline_color=(0, 0, 0, 1), size_hint=(None, None),
                                  size=(max(dp(120), min(dp(330), dp(8 * len(text) + 40))), dp(28)),
                                  pos_hint={'x': 0.95, 'y': 0.72 - (shown % 4) * 0.17},
                                  halign='center', valign='middle')
                    label.text_size = label.size
                    overlay.add_widget(label)
                    start_x = overlay.width + dp(10)
                    end_x = -label.width - dp(10)
                    label.x = start_x
                    Animation(x=end_x, duration=7.0).start(label)
                    Clock.schedule_once(lambda _t, w=label, parent=overlay: parent.remove_widget(w) if w.parent else None, 7.2)
                    shown += 1
                index += 1
            self._danmaku_index = index
        except Exception:
            pass

    # ---------------------------- parse ---------------------------------
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
        threading.Thread(target=self._parse_worker, args=(text,),
                         daemon=True).start()

    def _parse_worker(self, text):
        try:
            cookie = self.account.get('cookie', '') if self.account else ''
            session = new_session(cookie)
            info = get_video_info(session, text)
            # 取第一个 P 的 cid 查询当前视频可用画质
            first = dict(info)
            pages = info.get('pages') or []
            if pages:
                first['cid'] = pages[0].get('cid')
                first['duration'] = pages[0].get('duration', info.get('duration', 0))
            play = get_play_info(session, first, cookie)
            data = play['data']
            accepted = data.get('accept_quality') or []
            descriptions = data.get('accept_description') or []
            qlist = []
            logged_in = self._logged_in()
            for q, d in zip(accepted, descriptions):
                q = int(q)
                if q >= 80 and not logged_in:
                    continue
                qlist.append((q, str(d)))
            if not qlist:
                for item in (data.get('dash') or {}).get('video', []):
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
            err = str(e)
            ui(lambda err=err: self._alert('解析失败：' + err))
        finally:
            self.busy = False
            ui(self._enable)

    def _parse_done(self, info, qlist):
        self.title_lbl.text = str(info.get('title', '未知标题'))
        pages = info.get('pages') or []
        if not pages:
            pages = [{
                'page': 1, 'cid': info.get('cid'),
                'part': info.get('title', 'P1'),
                'duration': info.get('duration', 0),
            }]
        self._pages = pages
        self._page_labels = []
        for p in pages:
            n = int(p.get('page') or len(self._page_labels) + 1)
            part = str(p.get('part') or ('P%d' % n))
            dur = format_duration(p.get('duration', 0))
            self._page_labels.append('P%d · %s · %s' % (n, part, dur))
        # “全部分P”始终是第一项；后续项对应单个P
        labels = ['全部分P（%d个）' % len(pages)] + self._page_labels
        self.part_spinner.values = labels
        self.part_spinner.text = labels[1] if len(pages) == 1 else labels[0]
        self.meta_lbl.text = (
            'UP主：%s  |  总时长：%s\n播放：%s  弹幕：%s  点赞：%s\n%s' % (
                info.get('owner', '未知'),
                format_duration(info.get('duration', 0)),
                info.get('view', 0), info.get('danmaku', 0),
                info.get('like', 0), (info.get('description') or '')[:120]))
        qlabels = ['%s（%s）' % (desc, q) for q, desc in qlist]
        self.quality_spinner.values = qlabels
        self.quality_spinner.text = qlabels[0] if qlabels else '无可用画质'
        try:
            for spinner in (self.part_spinner, self.quality_spinner,
                            self.mode_spinner):
                for opt in spinner.dropdown.values:
                    opt.font_name = APP_FONT
        except Exception:
            pass
        self.status.text = (
            '解析成功：%d 个分P，%d 个当前可用画质。' %
            (len(pages), len(qlist))) + (
            ' 未登录仅显示 720P 及以下。' if not self._logged_in()
            else ' 登录状态已启用。')
        self.progress.value = 0
        if info.get('pic'):
            threading.Thread(target=self._load_cover, args=(info['pic'],),
                             daemon=True).start()

    def _load_cover(self, url):
        """Load a safe static thumbnail; GIF/WEBP/legacy covers become JPEG."""
        try:
            if not url:
                return
            url = str(url).strip()
            if url.startswith('//'):
                url = 'https:' + url
            elif url.startswith('http://'):
                # Bilibili's older archive metadata can still contain HTTP URLs.
                url = 'https://' + url[len('http://'):]
            headers = {
                'Referer': 'https://www.bilibili.com/',
                'User-Agent': ('Mozilla/5.0 (Linux; Android 14) '
                               'AppleWebKit/537.36 Chrome/124.0 Mobile Safari/537.36'),
                'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
            }
            r = requests.get(url, timeout=(8, 18), headers=headers)
            r.raise_for_status()
            if not r.content or len(r.content) > 15 * 1024 * 1024:
                raise ValueError('封面文件为空或超过 15 MB')
            # Do not hand GIF/WEBP/unknown image bytes directly to Kivy's decoder.
            # Pillow seeks to frame 0 and normalizes the image to a bounded JPEG.
            ImageFile.LOAD_TRUNCATED_IMAGES = True
            with PILImage.open(io.BytesIO(r.content)) as source:
                try:
                    source.seek(0)
                except Exception:
                    pass
                frame = source.convert('RGB')
                frame.thumbnail((640, 400), PILImage.Resampling.LANCZOS)
                out = io.BytesIO()
                frame.save(out, format='JPEG', quality=86, optimize=True)
                data = out.getvalue()
            def apply_cover():
                if hasattr(self, 'cover') and self.cover:
                    try:
                        self.cover.texture = CoreImage(io.BytesIO(data), ext='jpg').texture
                    except Exception:
                        # A bad thumbnail must never break parsing or the UI.
                        pass
            ui(apply_cover)
        except Exception:
            # Cover loading is optional: title, quality and download remain usable.
            return

    # ---------------------------- download --------------------------------
    def _downloads_dir(self):
        path = os.path.join(self.app.user_data_dir, 'downloads')
        os.makedirs(path, exist_ok=True)
        return path

    def _selected_quality(self):
        selected = self.quality_spinner.text
        return next((q for q, d in self.qualities
                     if ('%s（%s）' % (d, q)) == selected), 0)

    def _selected_pages(self):
        pages = getattr(self, '_pages', None) or []
        if not pages:
            return [dict(self.info)]
        selected = self.part_spinner.text
        if selected.startswith('全部分P'):
            return [dict(p) for p in pages]
        for i, label in enumerate(getattr(self, '_page_labels', [])):
            if label == selected:
                return [dict(pages[i])]
        return [dict(pages[0])]

    def start_download(self):
        if self.busy:
            return
        if not self.info:
            self._alert('请先解析视频，确认信息和可用画质后再下载。')
            self.parse_async()
            return
        mode = self.mode_spinner.text
        pages = self._selected_pages()
        # 附件模式只对当前单个P有效，避免将同一封面/弹幕/字幕重复下载。
        if mode in ('封面图片', '弹幕 XML', '字幕 SRT'):
            if len(pages) != 1:
                self._alert('封面、弹幕和字幕请先选择单个分P。')
                return
            self._attachment_page = pages[0]
            self.busy = True
            self.dl_btn.disabled = True
            self.parse_btn.disabled = True
            threading.Thread(target=self._download_attachment,
                             args=(mode,), daemon=True).start()
            return
        quality = self._selected_quality()
        if quality >= 80 and not self._logged_in():
            self._alert('1080P 及以上画质需要先点击右上角“登录”，使用 B 站授权扫码登录。')
            self.show_login()
            return
        self.busy = True
        self.cancel.clear()
        self.dl_btn.disabled = True
        self.parse_btn.disabled = True
        self.progress.value = 0
        threading.Thread(target=self._download_worker,
                         args=(quality, mode, pages), daemon=True).start()

    def _download_attachment(self, mode):
        try:
            if not self.info:
                raise RuntimeError('请先解析视频')
            selected = getattr(self, '_attachment_page', None) or self.info
            part_info = dict(self.info)
            part_info.update(selected)
            session = new_session(self.account.get('cookie', '') if self.account else '')
            stem = (self.info.get('bvid') or
                    'av' + str(self.info.get('aid') or 'video'))
            page_num = int(selected.get('page') or 1)
            if mode == '封面图片':
                url = self.info.get('pic')
                if not url:
                    raise RuntimeError('没有封面地址')
                name = 'cover_' + stem + '.jpg'
                response = session.get(url, timeout=25)
                response.raise_for_status()
                content = response.content
                mime = 'image/jpeg'
            elif mode == '弹幕 XML':
                url = 'https://comment.bilibili.com/%s.xml' % selected.get('cid')
                response = session.get(url, timeout=25)
                response.raise_for_status()
                content = response.content
                if not content or b'<i' not in content[:500]:
                    raise RuntimeError('B站未返回有效弹幕 XML')
                name = '%s_P%d_danmaku.xml' % (stem, page_num)
                mime = 'application/xml'
            else:
                params = {'cid': selected.get('cid')}
                if self.info.get('bvid'):
                    params['bvid'] = self.info['bvid']
                response = session.get('https://api.bilibili.com/x/player/v2',
                                       params=params, timeout=25)
                response.raise_for_status()
                payload = response.json()
                subs = ((payload.get('data') or {}).get('subtitle') or {}).get('subtitles') or []
                if not subs:
                    raise RuntimeError('该分P没有公开字幕')
                suburl = subs[0].get('subtitle_url') or ''
                if suburl.startswith('//'):
                    suburl = 'https:' + suburl
                if not suburl:
                    raise RuntimeError('字幕地址为空')
                sr = session.get(suburl, timeout=25)
                sr.raise_for_status()
                subdata = sr.json()
                def stamp(value):
                    value = max(0, float(value or 0))
                    h = int(value // 3600)
                    m = int((value % 3600) // 60)
                    sec = int(value % 60)
                    ms = int((value - int(value)) * 1000)
                    return '%02d:%02d:%02d,%03d' % (h, m, sec, ms)
                lines = []
                for i, item in enumerate(subdata.get('body') or [], 1):
                    lines.extend([str(i),
                        stamp(item.get('from')) + ' --> ' + stamp(item.get('to')),
                        str(item.get('content', '')), ''])
                if not lines:
                    raise RuntimeError('字幕内容为空')
                content = ('\ufeff' + '\n'.join(lines)).encode('utf-8')
                name = '%s_P%d_subtitle.srt' % (stem, page_num)
                mime = 'application/x-subrip'
            path = os.path.join(self._downloads_dir(), name)
            with open(path, 'wb') as f:
                f.write(content)
            self._add_history(name, path, mime)
            ui(lambda: setattr(self.status, 'text', mode + '下载完成：' + name))
        except Exception as e:
            err = str(e)
            ui(lambda err=err: self._alert(mode + '下载失败：' + err))
        finally:
            self.busy = False
            ui(self._enable)

    def _download_cover(self):
        return self._download_attachment('封面图片')

    def _download_worker(self, quality, mode, pages):
        try:
            out = self._downloads_dir()
            cookie = self.account.get('cookie', '') if self.account else ''
            count = len(pages)
            completed = 0
            for index, page in enumerate(pages, 1):
                if self.cancel.is_set():
                    raise RuntimeError('下载已取消')
                part_info = dict(self.info)
                part_info.update(page)
                part_info['cid'] = page.get('cid') or self.info.get('cid')
                page_num = int(page.get('page') or index)
                part_title = str(page.get('part') or ('P%d' % page_num))
                part_info['title'] = '%s - P%d %s' % (
                    self.info.get('title', 'B站视频'), page_num, part_title)
                ui(lambda i=index, n=count, t=part_title:
                   setattr(self.status, 'text',
                           '正在下载第 %d/%d P：%s' % (i, n, t)))
                def prog(done, total, idx=index):
                    pct = int(((idx - 1) + (done / total if total else 0)) * 100 / max(1, count))
                    done_mb = round(done / 1048576, 1)
                    total_mb = round(total / 1048576, 1) if total else '?'
                    ui(lambda v=pct: setattr(self.progress, 'value', v))
                    ui(lambda d=done_mb, t=total_mb, i=idx, n=count:
                       setattr(self.status, 'text',
                               '第 %d/%d P 下载中：%s / %s MB' % (i, n, d, t)))
                def log(line):
                    value = str(line)[-250:]
                    ui(lambda v=value: setattr(self.log_lbl, 'text', v))
                path = download_video(part_info, cookie, quality, out,
                                      progress=prog, cancel=self.cancel, log=log)
                # 与原版行为保持一致：Android 上每个分P完成后导出到 Download。
                if platform == 'android':
                    from jnius import autoclass
                    PythonActivity = autoclass('org.kivy.android.PythonActivity')
                    Helper = autoclass('org.bilibilidl.MediaMerger')
                    exported = str(Helper.exportToDownloads(
                        PythonActivity.mActivity, path, os.path.basename(path)))
                    if not exported.startswith('content://'):
                        raise RuntimeError(
                            '第 %d P 已下载到应用内部，但导出到手机 Download 失败：%s'
                            % (page_num, exported))
                self._add_history(os.path.basename(path), path, 'video/mp4')
                completed += 1
            ui(lambda c=completed, n=count: setattr(
                self.status, 'text', '下载完成：成功 %d/%d 个分P。' % (c, n)))
            ui(lambda: setattr(self.progress, 'value', 100))
        except Exception as e:
            err = str(e)
            ui(lambda err=err: self._alert('下载失败：' + err))
            ui(lambda: setattr(self.status, 'text', '下载失败。查看提示并重新尝试。'))
        finally:
            self.busy = False
            ui(self._enable)

    def _enable(self):
        if hasattr(self, 'dl_btn'):
            self.dl_btn.disabled = False
        if hasattr(self, 'parse_btn'):
            self.parse_btn.disabled = False

    # ---------------------------- history --------------------------------
    def _add_history(self, name, path, mime):
        self.history = [h for h in self.history if h.get('path') != path]
        self.history.insert(0, {
            'name': name, 'path': path, 'mime': mime,
            'created': int(time.time()),
            'title': (self.info or {}).get('title', name),
        })
        self.history = self.history[:200]
        self._save_json('history.json', self.history)

    def _history_page(self):
        sc = ScrollView()
        col = BoxLayout(orientation='vertical', spacing=dp(8), size_hint_y=None)
        col.bind(minimum_height=col.setter('height'))
        col.add_widget(self._label('已下载', 20, DARK, 38, True))
        col.add_widget(self._label(
            '视频下载完成后会自动保存到手机 Download/bilibilidl，'
            '无需申请存储权限。应用内保留副本用于历史记录。',
            12, MUTED, 48))
        if not self.history:
            col.add_widget(self._label('还没有下载记录。', 14, MUTED, 60))
        for item in self.history:
            card = BoxLayout(orientation='vertical', padding=dp(10),
                             spacing=dp(5), size_hint_y=None, height=dp(112))
            card.add_widget(self._label(item.get('name', '文件'), 14, DARK, 28, True))
            card.add_widget(self._label(item.get('title', ''), 11, MUTED, 24))
            row = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(8))
            exp = RoundButton(text='导出到手机', bg=PINK)
            exp.bind(on_release=lambda _, it=item: self.export_file(it))
            delete = RoundButton(text='删除记录', bg=PALE, fg=PINK)
            delete.bind(on_release=lambda _, it=item: self.delete_history(it))
            row.add_widget(exp)
            row.add_widget(delete)
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
            filename = item.get('name', os.path.basename(path))
            if platform == 'android':
                from jnius import autoclass
                PythonActivity = autoclass('org.kivy.android.PythonActivity')
                Helper = autoclass('org.bilibilidl.MediaMerger')
                result = str(Helper.exportToDownloads(PythonActivity.mActivity, path, filename))
                if not result.startswith('content://'):
                    raise RuntimeError(result)
            else:
                import shutil
                target = os.path.join(str(Path.home() / 'Downloads'), filename)
                shutil.copy2(path, target)
                result = target
            self._alert('导出成功：\n' + result)
        except Exception as e:
            self._alert('导出失败：' + str(e))

    def delete_history(self, item):
        self.history = [h for h in self.history if h.get('path') != item.get('path')]
        self._save_json('history.json', self.history)
        self.show_page('history')

    # ---------------------------- mine ------------------------------------
    def _mine_page(self):
        self.body.add_widget(self._label('我的', 22, DARK, 44, True))
        self.body.add_widget(self._label('bilibilidl · 仅在本机处理登录信息', 13, MUTED, 32))
        if self.account:
            self.body.add_widget(self._label(
                '账号：%s\nUID：%s\nVIP 类型：%s' % (
                    self.account.get('uname', 'B站用户'),
                    self.account.get('mid', '未知'),
                    self.account.get('vipType', 0)), 15, DARK, 80))
            b = RoundButton(text='退出登录并清除 Cookie', bg=PALE, fg=PINK,
                            size_hint_y=None, height=dp(48))
            b.bind(on_release=lambda *_: self.logout())
            self.body.add_widget(b)
        else:
            b = RoundButton(text='B站扫码登录', size_hint_y=None, height=dp(48))
            b.bind(on_release=lambda *_: self.show_login())
            self.body.add_widget(b)
        self.body.add_widget(self._label(
            '说明：画质由 B 站接口实际返回结果决定；登录不代表拥有大会员专属权益。',
            12, MUTED, 60))
        self.body.add_widget(Label(font_name=APP_FONT))

    # ---------------------------- login -----------------------------------
    def _make_qr_png(self, url):
        qr = qrcode.QRCode(box_size=8, border=3)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color='black', back_color='white').convert('RGB')
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return buf.getvalue()

    def show_login(self):
        if self.account:
            self._mine_page_confirm()
            return
        self.login_cancel.set()
        self.login_cancel = threading.Event()
        cancel = self.login_cancel
        content = BoxLayout(orientation='vertical', padding=dp(14), spacing=dp(8))
        content.add_widget(self._label(
            '使用哔哩哔哩 App 扫描二维码并确认登录。',
            13, MUTED, 48, halign='center'))
        self.qr_image = make_image(size_hint=(1, 1))
        content.add_widget(self.qr_image)
        self.qr_status = self._label('正在获取二维码…', 13, PINK, 32, halign='center')
        content.add_widget(self.qr_status)
        row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        refresh = RoundButton(text='刷新二维码', bg=PALE, fg=PINK)
        close = RoundButton(text='取消', bg=(0.9, 0.89, 0.91, 1), fg=DARK)
        row.add_widget(refresh)
        row.add_widget(close)
        content.add_widget(row)
        pop = Popup(title='B站扫码登录', title_font=APP_FONT, content=content,
                    size_hint=(0.9, 0.78), auto_dismiss=False)
        self.login_popup = pop
        refresh.bind(on_release=lambda *_: self._refresh_qr())
        close.bind(on_release=lambda *_: self._close_login())
        pop.open()
        threading.Thread(target=self._qr_worker, args=(cancel,), daemon=True).start()

    def _refresh_qr(self):
        self.login_cancel.set()
        self.login_cancel = threading.Event()
        self.qr_status.text = '正在刷新二维码…'
        threading.Thread(target=self._qr_worker, args=(self.login_cancel,), daemon=True).start()

    def _set_qr_status(self, text):
        ui(lambda t=text: setattr(self.qr_status, 'text', t) if self.login_popup else None)

    def _qr_worker(self, cancel):
        session = new_session()
        try:
            url, key = qr_generate(session)
            png = self._make_qr_png(url)
            def apply_qr():
                if self.login_popup:
                    self.qr_image.texture = CoreImage(io.BytesIO(png), ext='png').texture
            ui(apply_qr)
            self._set_qr_status('请使用 B 站 App 扫码')
            end = time.time() + 175
            while time.time() < end and not cancel.is_set():
                code, msg = qr_poll(session, key)
                if cancel.is_set():
                    return
                if code == 0:
                    cookie, profile = validate_login(session)
                    self._save_account(cookie, profile)
                    ui(lambda p=profile: self._login_done(p))
                    return
                if code == 86038:
                    self._set_qr_status('二维码已过期，请刷新')
                    return
                self._set_qr_status('已扫码，请在手机上确认' if code == 86090 else '等待扫码…')
                for _ in range(20):
                    if cancel.is_set():
                        return
                    time.sleep(0.1)
            if not cancel.is_set():
                self._set_qr_status('二维码超时，请刷新')
        except Exception as e:
            self._set_qr_status('登录失败：' + str(e)[:90])

    def _save_account(self, cookie, profile):
        self.account = {
            'cookie': cookie, 'uname': profile.get('uname', 'B站用户'),
            'mid': profile.get('mid', 0), 'vipType': profile.get('vipType', 0),
            'face': profile.get('face', ''), 'saved_at': int(time.time())}
        self._save_json('account.json', self.account)

    def _login_done(self, profile):
        if self.login_popup:
            self.login_popup.dismiss()
            self.login_popup = None
        self.login_btn.text = profile.get('uname', '已登录')
        self._alert('登录成功：' + profile.get('uname', 'B站用户'))
        if self.current_page == 'mine':
            self.show_page('mine')

    def _close_login(self):
        self.login_cancel.set()
        if self.login_popup:
            self.login_popup.dismiss()
            self.login_popup = None

    def logout(self):
        self.login_cancel.set()
        self.account = None
        try:
            os.remove(os.path.join(self.app.user_data_dir, 'account.json'))
        except OSError:
            pass
        self.login_btn.text = 'B站授权登录'
        self.show_page('mine')
        self._alert('已退出登录并清除本地 Cookie。')

    def _mine_page_confirm(self):
        self.show_page('mine')

    def _alert(self, text):
        def show():
            msg = str(text)
            if len(msg) > 300:
                msg = msg[:300] + '…'
            label = Label(text=msg, font_name=APP_FONT, color=WHITE,
                          halign='center', valign='middle')
            label.bind(size=lambda obj, val: setattr(obj, 'text_size', (max(1, val[0]), None)))
            Popup(title='提示', title_font=APP_FONT, content=label,
                  size_hint=(0.9, 0.5)).open()
        ui(show)


class BiliBilidlApp(App):
    title = 'bilibilidl'

    def build(self):
        Window.clearcolor = WHITE
        return AppRoot(self)

    def on_start(self):
        if platform == 'android':
            style_system_bars()
            preload_merger()


if __name__ == '__main__':
    BiliBilidlApp().run()
