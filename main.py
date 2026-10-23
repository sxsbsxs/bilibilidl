# -*- coding: utf-8 -*-
"""bilibilidl: native Android-oriented Bilibili downloader UI."""
import os
import io
import re
import json
import time
import threading
from pathlib import Path

import requests
import qrcode
from PIL import Image as PILImage, ImageFile

from kivy.app import App
from kivy.clock import Clock
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

from bili_api import (new_session, get_video_info, get_play_info, format_duration)
from login_manager import qr_generate, qr_poll, validate_login
from downloader import download_video, preload_merger

APP = 'bilibilidl'
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _find_cjk_font():
    # 首选随 APK 一起打包的完整 Noto CJK 字体；不要依赖 Roboto 的中文覆盖。
    preferred = [
        'NotoSansCJKsc-Regular.ttf', 'NotoSansCJKsc-Regular.otf',
        'NotoSansCJK-Regular.ttc',
        'NotoSansSC-Regular.ttf', 'NotoSansSC-Regular.otf',
        'NotoSansSC.ttf', 'NotoSansSC[wght].ttf',
    ]
    folders = [BASE_DIR, os.path.join(BASE_DIR, 'fonts')]
    for folder in folders:
        for name in preferred:
            path = os.path.join(folder, name)
            if os.path.isfile(path) and os.path.getsize(path) > 0:
                return path
    # 兼容用户没有把字体放进项目的情况，尝试 Android 系统中文字体。
    if platform == 'android':
        for path in (
            '/system/fonts/NotoSansCJK-Regular.ttc',
            '/system/fonts/NotoSansCJKsc-Regular.otf',
            '/system/fonts/DroidSansFallback.ttf',
        ):
            if os.path.isfile(path) and os.path.getsize(path) > 0:
                return path
    for folder in folders:
        try:
            for name in sorted(os.listdir(folder)):
                if name.lower().endswith(('.ttf', '.otf', '.ttc')):
                    path = os.path.join(folder, name)
                    if os.path.isfile(path) and os.path.getsize(path) > 0:
                        return path
        except OSError:
            pass
    return None


APP_FONT = 'Roboto'
APP_FONT_BOLD = 'Roboto'
_font_path = _find_cjk_font()
_FONT_LOADED = False
if _font_path:
    try:
        LabelBase.register(name='BiliPinkCJK', fn_regular=_font_path)
        APP_FONT = APP_FONT_BOLD = 'BiliPinkCJK'
        _FONT_LOADED = True
        print('[bilibilidl] 中文字体加载成功: %s' % _font_path)
    except Exception as exc:
        print('[bilibilidl] 中文字体注册失败: %s' % repr(exc))
else:
    print('[bilibilidl] 严重警告：没有找到中文字体；请将 NotoSansCJKsc-Regular.ttf 放在 main.py 同目录。')
try:
    SpinnerOption.font_name = APP_FONT
except Exception:
    pass

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
        self._pending_export_path = None
        self._pending_export_name = None
        self._export_request_code = 7301
        self._activity_result_bound = False
        self.account = self._load_json('account.json', None)
        self.history = self._load_json('history.json', [])
        self.current_page = 'download'
        self._build()
        self.show_page('download')
        self._bind_activity_result()

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

    def _auto_label(self, text, size=14, color=DARK, bold=False):
        """Wrapped label whose height follows its text to avoid overlap on phones."""
        w = Label(text=str(text), font_name=APP_FONT, font_size=dp(size),
                  color=color, bold=bold, halign='left', valign='top',
                  size_hint_y=None)
        def fit_width(widget, _value):
            widget.text_size = (max(1, widget.width), None)
        def fit_height(widget, texture_size):
            widget.height = max(dp(size + 8), texture_size[1] + dp(10))
        w.bind(size=fit_width, texture_size=fit_height)
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
        for text, page in [('解析/下载', 'download'), ('已下载', 'history'),
                           ('我的', 'mine')]:
            b = RoundButton(text=text, bg=PALE, fg=PINK)
            b.bind(on_release=lambda _, p=page: self.show_page(p))
            nav.add_widget(b)
        self.add_widget(nav)

    def show_page(self, page):
        self.current_page = page
        self.body.clear_widgets()
        if page in ('home', 'download'):
            self._home_page()
        elif page == 'history':
            self._history_page()
        else:
            self._mine_page()

    def _home_page(self):
        sc = ScrollView()
        self.parse_scroll = sc
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
        row = BoxLayout(size_hint_y=None, height=dp(96), spacing=dp(8))
        self.parse_btn = RoundButton(text='解析视频')
        self.parse_btn.bind(on_release=lambda *_: self.parse_async())
        row.add_widget(self.parse_btn)
        self.cover = make_image(size_hint_x=None, size_hint_y=None, width=dp(116), height=dp(88))
        row.add_widget(self.cover)
        col.add_widget(row)
        self.title_lbl = self._auto_label('解析后显示标题和视频信息', 17, DARK, True)
        col.add_widget(self.title_lbl)
        self.meta_lbl = self._auto_label(
            '画质会按接口返回的实际可用列表显示。', 13, MUTED)
        col.add_widget(self.meta_lbl)
        self.original_btn = RoundButton(
            text='▶ 原视频，直接观看', size_hint_y=None, height=dp(50), bg=PALE, fg=PINK)
        self.original_btn.disabled = True
        self.original_btn.bind(on_release=lambda *_: self._open_original_video())
        col.add_widget(self.original_btn)
        col.add_widget(self._label(
            '在 bilibilidl 应用内嵌入 B 站官方播放器；视频由网页播放器流式播放。',
            12, MUTED, 42))
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
            '下载先保存在应用内部，不会自动复制到相册；需要时可在“已下载”中选择保存位置。',
            12, MUTED, 52)
        col.add_widget(self.status)
        self.log_lbl = self._label('', 11, MUTED, 68)
        col.add_widget(self.log_lbl)
        sc.add_widget(col)
        self.body.add_widget(sc)

    # ---------------------------- embedded player -------------------------
    def _open_original_video(self):
        """Show Bilibili's official embedded player in an Android WebView overlay."""
        info = getattr(self, 'info', None) or {}
        bvid = str(info.get('bvid') or '').strip()
        aid = str(info.get('aid') or '').strip()
        if not bvid and not aid:
            self._alert('请先成功解析视频，再打开原视频。')
            return
        if platform != 'android':
            # Desktop/dev fallback only; Android release uses the in-app WebView.
            url = ('https://player.bilibili.com/player.html?bvid=' + bvid
                   if bvid else 'https://www.bilibili.com/blackboard/html5player.html?aid=' + aid)
            self._open_video_url(url)
            return
        try:
            from jnius import autoclass, PythonJavaClass, java_method
            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            WebView = autoclass('android.webkit.WebView')
            WebViewClient = autoclass('android.webkit.WebViewClient')
            WebChromeClient = autoclass('android.webkit.WebChromeClient')
            FrameLayout = autoclass('android.widget.FrameLayout')
            Button = autoclass('android.widget.Button')
            ViewGroup = autoclass('android.view.ViewGroup$LayoutParams')
            Gravity = autoclass('android.view.Gravity')
            Color = autoclass('android.graphics.Color')
            BuildVersion = autoclass('android.os.Build$VERSION')
            CookieManager = autoclass('android.webkit.CookieManager')
            activity = PythonActivity.mActivity
            self._close_inline_player()

            # Keep the Android WebView in the same Activity, layered over Kivy.
            root = FrameLayout(activity)
            web = WebView(activity)
            settings = web.getSettings()
            settings.setJavaScriptEnabled(True)
            settings.setDomStorageEnabled(True)
            settings.setMediaPlaybackRequiresUserGesture(True)
            settings.setLoadsImagesAutomatically(True)
            if int(BuildVersion.SDK_INT) >= 21:
                settings.setMixedContentMode(0)  # MIXED_CONTENT_ALWAYS_ALLOW, for legacy embeds
                CookieManager.getInstance().setAcceptThirdPartyCookies(web, True)
            web.setBackgroundColor(Color.BLACK)
            web.setWebViewClient(WebViewClient())
            web.setWebChromeClient(WebChromeClient())
            root.addView(web, FrameLayout.LayoutParams(
                ViewGroup.MATCH_PARENT, ViewGroup.MATCH_PARENT))

            close_btn = Button(activity)
            close_btn.setText('关闭播放器  ✕')
            close_btn.setTextColor(Color.WHITE)
            close_btn.setBackgroundColor(Color.rgb(220, 45, 105))
            density = float(activity.getResources().getDisplayMetrics().density)
            button_params = FrameLayout.LayoutParams(
                int(132 * density), int(48 * density), Gravity.TOP | Gravity.RIGHT)
            button_params.topMargin = int(28 * density)
            button_params.rightMargin = int(10 * density)
            root.addView(close_btn, button_params)

            class _CloseListener(PythonJavaClass):
                __javainterfaces__ = ['android/view/View$OnClickListener']
                __javacontext__ = 'app'
                def __init__(self, owner):
                    super().__init__()
                    self.owner = owner
                @java_method('(Landroid/view/View;)V')
                def onClick(self, view):
                    Clock.schedule_once(lambda _dt: self.owner._close_inline_player(), 0)

            self._inline_player_root = root
            self._inline_player_web = web
            self._inline_player_activity = activity
            self._inline_player_listener = _CloseListener(self)
            close_btn.setOnClickListener(self._inline_player_listener)
            activity.addContentView(root, ViewGroup.LayoutParams(
                ViewGroup.MATCH_PARENT, ViewGroup.MATCH_PARENT))
            if bvid:
                player_url = 'https://player.bilibili.com/player.html?bvid=%s&autoplay=0&high_quality=1' % bvid
            else:
                player_url = 'https://player.bilibili.com/player.html?aid=%s&autoplay=0&high_quality=1' % aid
            web.loadUrl(player_url)
        except Exception as exc:
            print('[bilibilidl] 内嵌播放器启动失败: %s' % repr(exc))
            self._close_inline_player()
            self._alert('应用内播放器启动失败：%s' % str(exc)[:120])

    def _close_inline_player(self):
        """Release the overlay WebView so playback buffers can be reclaimed."""
        web = getattr(self, '_inline_player_web', None)
        root = getattr(self, '_inline_player_root', None)
        activity = getattr(self, '_inline_player_activity', None)
        try:
            if web is not None:
                web.stopLoading()
                web.loadUrl('about:blank')
                web.clearHistory()
                web.removeAllViews()
                web.destroy()
        except Exception as exc:
            print('[bilibilidl] 释放内嵌播放器失败: %s' % repr(exc))
        try:
            if root is not None and activity is not None:
                activity.runOnUiThread(lambda: activity.removeContentView(root))
        except Exception:
            try:
                parent = root.getParent() if root is not None else None
                if parent is not None:
                    parent.removeView(root)
            except Exception:
                pass
        self._inline_player_web = None
        self._inline_player_root = None
        self._inline_player_activity = None
        self._inline_player_listener = None

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
        try:
            self.cover.texture = None
            self.original_btn.disabled = True
        except Exception:
            pass
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
            for q, d in zip(accepted, descriptions):
                q = int(q)
                qlist.append((q, str(d)))
            if not qlist:
                for item in (data.get('dash') or {}).get('video', []):
                    q = int(item.get('id', 0))
                    if q and (q, str(q) + 'P') not in qlist:
                        qlist.append((q, str(q) + 'P'))
            if not qlist and data.get('durl'):
                qlist = [(0, '自动 / 当前可用画质')]
            self.info = info
            self.play = play
            self.qualities = qlist
            # Guard the UI callback too: exceptions raised on Kivy's main thread
            # do not reach the worker's try/except and can terminate the app.
            ui(lambda info=info, qlist=qlist: self._safe_parse_done(info, qlist))
        except Exception as e:
            err = str(e)
            ui(lambda err=err: self._alert('解析失败：' + err))
        finally:
            self.busy = False
            ui(self._enable)

    def _safe_parse_done(self, info, qlist):
        """Prevent a malformed legacy video/thumbnail UI update from crashing the app."""
        try:
            self._parse_done(info, qlist)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            # Keep parsing results usable even if one optional UI widget fails.
            try:
                title = str(info.get('title') or '未知标题')
                if hasattr(self, 'title_lbl'):
                    self.title_lbl.text = title
                if hasattr(self, 'status'):
                    self.status.text = '视频信息已获取，但界面显示部分内容失败；可继续重试。'
                if hasattr(self, 'meta_lbl'):
                    self.meta_lbl.text = '封面为可选内容；若未显示，不影响下载。'
            except Exception:
                pass

    def _parse_done(self, info, qlist):
        self.title_lbl.text = str(info.get('title') or '未知标题')
        self.original_btn.disabled = not bool(info.get('bvid') or info.get('aid'))
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
        description = ' '.join(str(info.get('description') or '').split())
        if len(description) > 180:
            description = description[:180] + '…'
        self.meta_lbl.text = (
            'UP主：%s\n总时长：%s\n播放：%s  点赞：%s\n简介：%s' % (
                info.get('owner', '未知'),
                format_duration(info.get('duration', 0)),
                info.get('view', 0), info.get('like', 0),
                description or '无简介'))
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
            '解析成功：%d 个分P，B站接口返回 %d 个画质选项。' %
            (len(pages), len(qlist))) + (
            ' 当前未登录；实际可播放/下载的最高画质由 B 站接口权限决定。'
            if not self._logged_in() else ' 已登录。')
        self.progress.value = 0
        # Bring the parsed title/metadata into view after the network request finishes.
        try:
            Clock.schedule_once(lambda _dt: self.parse_scroll.scroll_to(self.title_lbl, padding=dp(18), animate=False), 0.15)
        except Exception:
            pass
        if info.get('pic'):
            threading.Thread(target=self._load_cover, args=(info['pic'], info),
                             daemon=True).start()

    def _find_page_cover(self, info):
        """Try page metadata when a legacy archive API returns transparent.gif."""
        bvid = str(info.get('bvid') or '').strip()
        aid = info.get('aid')
        page_url = ('https://www.bilibili.com/video/' + bvid if bvid else
                    ('https://www.bilibili.com/video/av%s' % aid if aid else ''))
        if not page_url:
            return ''
        try:
            response = requests.get(page_url, timeout=(5, 8), headers={
                'Referer': 'https://www.bilibili.com/',
                'User-Agent': ('Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 '
                               'Chrome/124.0 Mobile Safari/537.36'),
            })
            response.raise_for_status()
            html = response.text[:2_000_000]
            patterns = (
                r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
                r'<meta[^>]+itemprop=["\']image["\'][^>]+content=["\']([^"\']+)',
            )
            for pattern in patterns:
                match = re.search(pattern, html, re.I)
                if match:
                    candidate = match.group(1).replace('&amp;', '&').strip()
                    if candidate.startswith('//'):
                        candidate = 'https:' + candidate
                    if candidate.startswith('http') and 'transparent.gif' not in candidate.lower():
                        return candidate
        except Exception as exc:
            print('[bilibilidl] 老视频网页封面回退失败: %s' % repr(exc))
        return ''

    def _load_cover(self, url, info=None):
        """Load a static thumbnail with low memory; retry the video page's og:image."""
        info = info or {}
        headers = {
            'Referer': 'https://www.bilibili.com/',
            'User-Agent': ('Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 '
                           'Chrome/124.0 Mobile Safari/537.36'),
            'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
        }
        candidates = []
        def add_candidate(value):
            value = str(value or '').strip()
            if value.startswith('//'):
                value = 'https:' + value
            elif value.startswith('http://'):
                value = 'https://' + value[len('http://'):]
            if value and 'transparent.gif' not in value.lower() and value not in candidates:
                candidates.append(value)
        add_candidate(url)
        # Old archives such as av2 may expose transparent.gif; obtain webpage metadata too.
        if not candidates:
            add_candidate(self._find_page_cover(info))
        last_error = None
        tried = 0
        while tried < 2:
            if tried >= len(candidates):
                add_candidate(self._find_page_cover(info))
            if tried >= len(candidates):
                break
            target = candidates[tried]
            tried += 1
            try:
                response = requests.get(target, timeout=(6, 12), headers=headers, stream=True)
                response.raise_for_status()
                chunks = []
                total = 0
                for chunk in response.iter_content(64 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > 12 * 1024 * 1024:
                        raise ValueError('封面超过 12 MB，已停止读取以保护内存')
                    chunks.append(chunk)
                raw = b''.join(chunks)
                if not raw:
                    raise ValueError('封面文件为空')
                ImageFile.LOAD_TRUNCATED_IMAGES = True
                with PILImage.open(io.BytesIO(raw)) as source:
                    source.seek(0)  # Animated GIF/WebP: decode only its first frame.
                    width, height = source.size
                    if width <= 2 or height <= 2 or width * height > 40_000_000:
                        raise ValueError('封面尺寸异常或为透明占位图')
                    rgba = source.convert('RGBA')
                    alpha = rgba.getchannel('A')
                    alpha_extrema = alpha.getextrema()
                    alpha.close()
                    if alpha_extrema == (0, 0):
                        rgba.close()
                        raise ValueError('封面是全透明占位图')
                    resampling = getattr(PILImage, 'Resampling', PILImage)
                    lanczos = getattr(resampling, 'LANCZOS', 1)
                    rgba.thumbnail((640, 400), lanczos)
                    mask = rgba.getchannel('A')
                    frame = PILImage.new('RGB', rgba.size, (255, 255, 255))
                    frame.paste(rgba, mask=mask)
                    mask.close()
                    rgba.close()
                    out = io.BytesIO()
                    frame.save(out, format='JPEG', quality=82, optimize=True)
                    image_bytes = out.getvalue()
                    frame.close()
                def apply_cover():
                    try:
                        widget = getattr(self, 'cover', None)
                        if widget is not None:
                            widget.texture = CoreImage(io.BytesIO(image_bytes), ext='jpg').texture
                            widget.opacity = 1
                        if getattr(self, 'status', None) is not None:
                            self.status.text = '解析成功，封面已加载。'
                    except Exception as exc:
                        print('[bilibilidl] 封面显示失败（不影响解析/下载）：%s' % repr(exc))
                ui(apply_cover)
                return
            except Exception as exc:
                last_error = exc
                print('[bilibilidl] 封面候选加载失败：%s' % repr(exc))
                # If the API image failed, add webpage metadata as the final fallback.
                if tried == 1:
                    add_candidate(self._find_page_cover(info))
        message = str(last_error or 'B站没有提供可用的封面图片')[:120]
        print('[bilibilidl] 封面不可用（不影响解析/下载）：' + message)
        def report_cover_issue():
            try:
                if getattr(self, 'meta_lbl', None) is not None:
                    base = self.meta_lbl.text
                    if '封面状态：' not in base:
                        self.meta_lbl.text = base + '\n封面状态：暂时无法获取真实图片，解析和下载仍可用。'
            except Exception:
                pass
        ui(report_cover_issue)

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
        # Do not hide/block qualities in the app; Bilibili itself decides whether
        # this account/session can obtain the selected stream.
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
                # 下载完成时仍自动保存到 Download/bilibilidl；历史页会提醒用户不要重复导出。
                if platform == 'android':
                    from jnius import autoclass
                    PythonActivity = autoclass('org.kivy.android.PythonActivity')
                    Helper = autoclass('org.bilibilidl.MediaMerger')
                    exported = str(Helper.exportToDownloads(
                        PythonActivity.mActivity, path, os.path.basename(path)))
                    if not exported.startswith('content://'):
                        raise RuntimeError(
                            '第 %d P 已下载到应用内部，但自动保存到 Download 失败：%s'
                            % (page_num, exported))
                self._add_history(os.path.basename(path), path, 'video/mp4')
                completed += 1
            ui(lambda c=completed, n=count: setattr(
                self.status, 'text', '下载完成：成功 %d/%d 个分P。已自动保存到手机 Download/bilibilidl；通常不需要再导出，避免生成重复副本。' % (c, n)))
            ui(lambda: setattr(self.progress, 'value', 100))
            ui(lambda c=completed: self._alert(
                '下载完成：%d 个文件。\n\n视频已自动保存到手机 Download/bilibilidl。通常不需要再次导出；如果确实要另存到其他位置，请在“已下载”点击“另存到…”，并选择不同位置/文件名，以免产生重复副本。' % c))
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
            '视频下载完成后已自动保存到手机 Download/bilibilidl。若相册/文件管理器已经能看到文件，通常不需要再导出；“另存到…”用于复制到其他位置，重复操作可能产生副本。点击后会打开 Android 系统保存位置选择器。',
            12, MUTED, 64))
        if not self.history:
            col.add_widget(self._label('还没有下载记录。', 14, MUTED, 60))
        for item in self.history:
            card = BoxLayout(orientation='vertical', padding=dp(10),
                             spacing=dp(5), size_hint_y=None, height=dp(112))
            card.add_widget(self._label(item.get('name', '文件'), 14, DARK, 28, True))
            card.add_widget(self._label(item.get('title', ''), 11, MUTED, 24))
            row = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(8))
            exp = RoundButton(text='另存到…', bg=PINK)
            exp.bind(on_release=lambda _, it=item: self._confirm_export_file(it))
            delete = RoundButton(text='删除记录', bg=PALE, fg=PINK)
            delete.bind(on_release=lambda _, it=item: self.delete_history(it))
            row.add_widget(exp)
            row.add_widget(delete)
            card.add_widget(row)
            col.add_widget(card)
        sc.add_widget(col)
        self.body.add_widget(sc)

    def _bind_activity_result(self):
        """Bind Android activity results once, for the system document picker."""
        if platform != 'android' or self._activity_result_bound:
            return
        try:
            from jnius import autoclass
            activity = autoclass('org.kivy.android.PythonActivity').mActivity
            activity.bind(on_activity_result=self._on_activity_result)
            self._activity_result_bound = True
        except Exception as exc:
            print('[bilibilidl] 无法绑定系统文件选择器回调: %s' % repr(exc))

    def _confirm_export_file(self, item):
        """Warn that the downloaded file may already exist in public Downloads."""
        content = BoxLayout(orientation='vertical', spacing=dp(12), padding=dp(14))
        if item.get('mime') == 'video/mp4':
            reminder = ('提醒：视频下载完成时已自动保存到手机 Download/bilibilidl。\n\n'
                        '如果你只是想在文件管理器里找到它，通常不需要再次导出。继续操作会把文件另存一份到你选择的位置，可能产生重复副本。')
        else:
            reminder = ('提醒：封面、弹幕或字幕等附件通常只保存在应用内部。\n\n'
                        '继续后会打开 Android 系统文件选择器，让你选择保存位置；如果选择了已有文件名，系统可能提示覆盖或创建副本。')
        content.add_widget(self._auto_label(reminder, 14, DARK))
        buttons = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(10))
        cancel_btn = RoundButton(text='取消', bg=PALE, fg=PINK)
        continue_btn = RoundButton(text='继续选择路径', bg=PINK)
        buttons.add_widget(cancel_btn)
        buttons.add_widget(continue_btn)
        content.add_widget(buttons)
        popup = Popup(title='导出前提醒', content=content, size_hint=(0.9, None),
                      height=dp(260), auto_dismiss=False)
        cancel_btn.bind(on_release=lambda *_: popup.dismiss())
        continue_btn.bind(on_release=lambda *_: (popup.dismiss(), self.export_file(item)))
        popup.open()

    def export_file(self, item):
        """Use Android's system Save As picker; never silently duplicate exports."""
        path = item.get('path', '')
        if not os.path.isfile(path):
            self._alert('找不到文件，可能已被系统清理。')
            return
        filename = item.get('name') or os.path.basename(path)
        mime = item.get('mime') or 'application/octet-stream'
        if platform == 'android':
            try:
                from jnius import autoclass
                Intent = autoclass('android.content.Intent')
                activity = autoclass('org.kivy.android.PythonActivity').mActivity
                self._bind_activity_result()
                self._pending_export_path = path
                self._pending_export_name = filename
                intent = Intent(Intent.ACTION_CREATE_DOCUMENT)
                intent.addCategory(Intent.CATEGORY_OPENABLE)
                intent.setType(mime)
                intent.putExtra(Intent.EXTRA_TITLE, filename)
                intent.addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
                activity.startActivityForResult(intent, self._export_request_code)
            except Exception as exc:
                self._pending_export_path = None
                self._pending_export_name = None
                self._alert('无法打开系统保存位置选择器：' + str(exc))
        else:
            # Development fallback; Android release always uses the system picker.
            from shutil import copy2
            target_dir = str(Path.home() / 'Downloads')
            os.makedirs(target_dir, exist_ok=True)
            target = os.path.join(target_dir, filename)
            if os.path.exists(target):
                stem, ext = os.path.splitext(filename)
                n = 2
                while os.path.exists(os.path.join(target_dir, '%s (%d)%s' % (stem, n, ext))):
                    n += 1
                target = os.path.join(target_dir, '%s (%d)%s' % (stem, n, ext))
            copy2(path, target)
            self._alert('导出成功：\n' + target)

    def _on_activity_result(self, *args):
        """Copy the selected file to the user-chosen SAF URI in bounded chunks."""
        try:
            if len(args) < 3:
                return
            request_code, result_code, data = args[-3], args[-2], args[-1]
            if int(request_code) != self._export_request_code:
                return
            source = self._pending_export_path
            filename = self._pending_export_name or 'download'
            self._pending_export_path = None
            self._pending_export_name = None
            if int(result_code) != -1 or data is None:
                ui(lambda: self._alert('已取消导出，原文件仍保留在应用内部。'))
                return
            if not source or not os.path.isfile(source):
                ui(lambda: self._alert('原文件不存在，无法导出。'))
                return
            uri = data.getData()
            if uri is None:
                ui(lambda: self._alert('系统没有返回保存位置，导出未完成。'))
                return
            from jnius import autoclass
            activity = autoclass('org.kivy.android.PythonActivity').mActivity
            resolver = activity.getContentResolver()
            output = resolver.openOutputStream(uri)
            if output is None:
                raise RuntimeError('系统无法打开所选保存位置')
            try:
                with open(source, 'rb') as src:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(bytearray(chunk))
                output.flush()
            finally:
                output.close()
            ui(lambda: self._alert(
                '导出成功：\n%s\n\n原文件仍保留在应用内部。若再次导出，请选择不同文件名或位置，以免产生副本。' % filename))
        except Exception as exc:
            message = str(exc)
            ui(lambda message=message: self._alert('导出失败：' + message))

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
