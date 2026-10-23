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

from kivy.app import App
from kivy.clock import Clock
from kivy.metrics import dp
from kivy.utils import platform
from kivy.core.window import Window
from kivy.core.text import LabelBase
from kivy.core.image import Image as CoreImage
from kivy.uix.image import Image
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.uix.spinner import Spinner, SpinnerOption
from kivy.uix.progressbar import ProgressBar
from kivy.uix.popup import Popup
from kivy.uix.video import Video
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.relativelayout import RelativeLayout
from kivy.graphics import Color, RoundedRectangle, Rectangle

from bili_api import (new_session, get_video_info, get_play_info,
                      format_duration, get_stream_sources)
from login_manager import qr_generate, qr_poll, validate_login
from downloader import download_video, preload_merger


def _safe_name(name):
    import re
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', '_',
                  (name or 'bilibili')).strip(' .')
    return (name or 'bilibili')[:80]

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
        # 同一 TTF 同时注册 regular/bold，粗体在多数机型上更明显
        LabelBase.register(name='BiliPinkCJK', fn_regular=_font_path, fn_bold=_font_path)
        LabelBase.register(name='BiliPinkCJKBold', fn_regular=_font_path, fn_bold=_font_path)
        APP_FONT = 'BiliPinkCJK'
        APP_FONT_BOLD = 'BiliPinkCJKBold'
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
# 次要文字略加深，避免发灰发细
MUTED = (0.36, 0.35, 0.38, 1)
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


class CardBox(BoxLayout):
    """浅粉圆角卡片容器，统一信息区/历史记录样式。"""
    def __init__(self, radius=16, bg=None, **kw):
        kw.setdefault('orientation', 'vertical')
        kw.setdefault('padding', dp(12))
        kw.setdefault('spacing', dp(6))
        super().__init__(**kw)
        self._bg_color = bg or PALE
        with self.canvas.before:
            Color(*self._bg_color)
            self._shape = RoundedRectangle(pos=self.pos, size=self.size,
                                           radius=[dp(radius)])
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

    def _label(self, text, size=15, color=DARK, height=None,
               bold=False, halign='left'):
        # 加粗时优先用 APP_FONT_BOLD；单字重字体在部分机型上更明显
        font = APP_FONT_BOLD if bold else APP_FONT
        kw = dict(text=str(text), font_name=font, font_size=dp(size),
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
                  if self.account else 'B站一键登录'),
            size_hint_x=None, width=dp(130), bg=PALE, fg=PINK)
        self.login_btn.bind(on_release=lambda *_: self.show_login())
        head.add_widget(self.login_btn)
        self.add_widget(head)
        self.body = BoxLayout(orientation='vertical', spacing=dp(8))
        self.add_widget(self.body)
        nav = BoxLayout(size_hint_y=None, height=dp(54), spacing=dp(8))
        for text, page in [('主页', 'home'), ('已下载', 'history'),
                           ('我的', 'mine')]:
            b = RoundButton(text=text, bg=PALE, fg=PINK)
            b.bind(on_release=lambda _, p=page: self.show_page(p))
            nav.add_widget(b)
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

    def _section_title(self, text):
        return self._label(text, 14, DARK, 24, bold=True)

    def _home_page(self):
        sc = ScrollView(do_scroll_x=False)
        col = BoxLayout(orientation='vertical', spacing=dp(12),
                        size_hint_y=None, padding=[0, 0, 0, dp(12)])
        col.bind(minimum_height=col.setter('height'))

        col.add_widget(self._label('B站视频下载', 24, DARK, 38, True))
        col.add_widget(self._label(
            '粘贴 BV / AV / 链接 / 短链 → 解析 → 下载或应用内播放', 14, MUTED, 30))

        # 输入卡片
        input_card = CardBox(size_hint_y=None, height=dp(150), spacing=dp(8))
        self.url_input = TextInput(
            hint_text='BV 号、AV 号、视频链接或 b23.tv 短链接',
            multiline=True, size_hint_y=None, height=dp(72),
            padding=[dp(10), dp(10)], font_name=APP_FONT_BOLD, font_size=dp(16),
            foreground_color=DARK, background_normal='',
            background_active='', background_color=WHITE, cursor_color=PINK)
        input_card.add_widget(self.url_input)
        row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        self.parse_btn = RoundButton(text='解析视频')
        self.parse_btn.bind(on_release=lambda *_: self.parse_async())
        row.add_widget(self.parse_btn)
        self.cover = make_image(size_hint_x=None, width=dp(72))
        row.add_widget(self.cover)
        input_card.add_widget(row)
        col.add_widget(input_card)

        # 视频信息卡片
        info_card = CardBox(size_hint_y=None)
        info_card.bind(minimum_height=info_card.setter('height'))
        self.title_lbl = self._label('解析后显示标题', 18, DARK, None, True)
        self.title_lbl.size_hint_y = None
        self.title_lbl.height = dp(30)
        self.title_lbl.bind(texture_size=lambda l, s: setattr(
            l, 'height', max(dp(30), s[1] + dp(6))))
        info_card.add_widget(self.title_lbl)
        self.meta_lbl = self._label(
            '画质与分P将在解析后显示', 14, MUTED, None)
        self.meta_lbl.size_hint_y = None
        self.meta_lbl.height = dp(30)
        self.meta_lbl.bind(texture_size=lambda l, s: setattr(
            l, 'height', max(dp(30), s[1] + dp(6))))
        info_card.add_widget(self.meta_lbl)
        col.add_widget(info_card)

        # 选项
        col.add_widget(self._section_title('分P'))
        self.part_spinner = Spinner(
            text='请先解析', values=(), font_name=APP_FONT_BOLD,
            size_hint_y=None, height=dp(46), background_normal='',
            background_color=PALE, color=DARK, font_size=dp(15))
        col.add_widget(self.part_spinner)
        col.add_widget(self._section_title('清晰度（下载用）'))
        self.quality_spinner = Spinner(
            text='请先解析', values=(), font_name=APP_FONT_BOLD,
            size_hint_y=None, height=dp(46), background_normal='',
            background_color=PALE, color=DARK, font_size=dp(15))
        col.add_widget(self.quality_spinner)
        col.add_widget(self._section_title('下载内容'))
        self.mode_spinner = Spinner(
            text='视频 + 音频（MP4）', font_name=APP_FONT_BOLD,
            values=('视频 + 音频（MP4）', '封面图片', '弹幕 XML', '字幕 SRT'),
            size_hint_y=None, height=dp(46), background_normal='',
            background_color=PALE, color=DARK, font_size=dp(15))
        col.add_widget(self.mode_spinner)

        btn_row = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(10))
        self.dl_btn = RoundButton(text='开始下载')
        self.dl_btn.bind(on_release=lambda *_: self.start_download())
        btn_row.add_widget(self.dl_btn)
        self.play_btn = RoundButton(text='在线播放', bg=BLUE)
        self.play_btn.bind(on_release=lambda *_: self.start_online_play())
        btn_row.add_widget(self.play_btn)
        col.add_widget(btn_row)

        self.progress = ProgressBar(max=100, value=0,
                                    size_hint_y=None, height=dp(6))
        col.add_widget(self.progress)
        self.status = self._label(
            '下载保存到本机；在线播放在应用内打开官方播放器，无需整段缓存。',
            14, MUTED, 46)
        col.add_widget(self.status)
        self.log_lbl = self._label('', 13, MUTED, 42)
        col.add_widget(self.log_lbl)
        sc.add_widget(col)
        self.body.add_widget(sc)

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
                info.get('like', 0), (info.get('description') or '')[:300]))
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
        try:
            if url.startswith('//'):
                url = 'https:' + url
            headers = {
                'Referer': 'https://www.bilibili.com/',
                'User-Agent': 'Mozilla/5.0'}
            r = requests.get(url, timeout=15, headers=headers)
            r.raise_for_status()
            raw = r.content
            ctype = r.headers.get('content-type', '').lower()

            data, ext = raw, 'jpg'
            # 用 Pillow 解码：webp / gif 动图统一取第一帧转 PNG，
            # 避免把动图当 jpg 解码导致主线程异常闪退
            try:
                from PIL import Image as PILImage
                img = PILImage.open(io.BytesIO(raw))
                img.seek(0)  # 动图只取第一帧
                if img.mode not in ('RGB', 'RGBA'):
                    img = img.convert('RGB')
                img.thumbnail((640, 640))
                buf = io.BytesIO()
                img.save(buf, format='PNG')
                data, ext = buf.getvalue(), 'png'
            except Exception as e:
                print('[bilibilidl] Pillow 解码封面失败: %s (%s)' % (e, ctype))
                # Pillow 解不了 webp 时，让 B 站图片服务器转出 jpg 再试
                if 'webp' in ctype or 'gif' in ctype:
                    r2 = requests.get(url + '@.jpg', timeout=15,
                                      headers=headers)
                    r2.raise_for_status()
                    data, ext = r2.content, 'jpg'
                elif 'png' in ctype:
                    ext = 'png'

            def apply_cover():
                try:
                    cover = getattr(self, 'cover', None)
                    if cover is not None:
                        cover.texture = CoreImage(
                            io.BytesIO(data), ext=ext).texture
                except Exception as e:
                    # Clock 回调里的异常会直接闪退，必须兜住
                    print('[bilibilidl] 封面设置失败: %s' % e)
            ui(apply_cover)
        except Exception as e:
            print('[bilibilidl] 封面下载失败: %s' % e)

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
            if hasattr(self, 'play_btn'):
                self.play_btn.disabled = True
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
        if hasattr(self, 'play_btn'):
            self.play_btn.disabled = True
        self.parse_btn.disabled = True
        self.progress.value = 0
        threading.Thread(target=self._download_worker,
                         args=(quality, mode, pages), daemon=True).start()

    # ---------------------------- online play -----------------------------
    def start_online_play(self):
        """应用内嵌 B 站官方播放器（WebView），不缓存，画质由官方页决定。"""
        if not self.info:
            self._alert('请先解析视频')
            self.parse_async()
            return

        pages = self._selected_pages()
        if len(pages) != 1:
            self._alert('在线播放请先选择单个分P')
            return

        page = pages[0]
        page_num = int(page.get('page') or 1)
        bvid = (self.info.get('bvid') or '').strip()
        if not bvid:
            self._alert('无法获取 BV 号')
            return

        cookie = self.account.get('cookie', '') if self.account else ''
        self.progress.value = 100
        self.status.text = '正在打开官方播放器…'
        self._open_bili_webview(bvid, page_num, cookie)

    def _enable_play(self):
        if hasattr(self, 'play_btn'):
            self.play_btn.disabled = False
        self._enable()

    @run_on_ui_thread
    def _open_bili_webview(self, bvid, page_num, cookie):
        """WebView 打开 https://player.bilibili.com/player.html?bvid=..."""
        try:
            if platform != 'android':
                import webbrowser
                url = ('https://player.bilibili.com/player.html?bvid=%s&page=%d'
                       % (bvid, page_num))
                webbrowser.open(url)
                self.status.text = '已在浏览器打开官方播放器'
                return
            from jnius import autoclass
            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            Player = autoclass('org.bilibilidl.InAppPlayer')
            result = str(Player.playBvid(
                PythonActivity.mActivity, bvid, int(page_num), cookie or ''))
            if result != 'ok':
                self._alert('打开播放器失败：' + result)
                self.status.text = '播放失败'
            else:
                self.status.text = '官方播放器中（返回键关闭）'
        except Exception as e:
            self._alert('无法打开播放器：' + str(e))

    def _close_inapp_player(self):
        if platform == 'android':
            try:
                from jnius import autoclass
                Player = autoclass('org.bilibilidl.InAppPlayer')
                Player.dismiss()
            except Exception:
                pass
        try:
            self.status.text = '已关闭播放器'
        except Exception:
            pass

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
                response = session.get(url, timeout=60)
                response.raise_for_status()
                content = response.content
                # 按真实格式保存：动图封面常为 animated webp，不能硬写成 .jpg
                ctype = (response.headers.get('Content-Type') or '').lower()
                head = content[:16]
                if head.startswith(b'GIF8') or 'gif' in ctype:
                    ext, mime = 'gif', 'image/gif'
                elif head[0:4] == b'RIFF' and b'WEBP' in head[:16] or 'webp' in ctype:
                    ext, mime = 'webp', 'image/webp'
                elif head.startswith(b'\x89PNG'):
                    ext, mime = 'png', 'image/png'
                elif 'jpeg' in ctype or 'jpg' in ctype or head[:2] == b'\xff\xd8':
                    ext, mime = 'jpg', 'image/jpeg'
                else:
                    # URL 后缀兜底
                    low = (url or '').lower().split('?')[0]
                    if low.endswith('.webp'):
                        ext, mime = 'webp', 'image/webp'
                    elif low.endswith('.gif'):
                        ext, mime = 'gif', 'image/gif'
                    elif low.endswith('.png'):
                        ext, mime = 'png', 'image/png'
                    else:
                        ext, mime = 'jpg', 'image/jpeg'
                name = 'cover_%s.%s' % (stem, ext)
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
                # 下载完成后导出到 Download 一次，并把 content_uri 记入历史，避免再次「导出」重复复制
                content_uri = ''
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
                    content_uri = exported
                self._add_history(os.path.basename(path), path, 'video/mp4',
                                  content_uri=content_uri)
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
        if hasattr(self, 'play_btn'):
            self.play_btn.disabled = False
        if hasattr(self, 'parse_btn'):
            self.parse_btn.disabled = False

    # ---------------------------- history --------------------------------
    def _add_history(self, name, path, mime, content_uri=''):
        self.history = [h for h in self.history if h.get('path') != path]
        self.history.insert(0, {
            'name': name, 'path': path, 'mime': mime,
            'content_uri': content_uri or '',
            'created': int(time.time()),
            'title': (self.info or {}).get('title', name),
        })
        self.history = self.history[:200]
        self._save_json('history.json', self.history)

    def _history_page(self):
        sc = ScrollView(do_scroll_x=False)
        col = BoxLayout(orientation='vertical', spacing=dp(12),
                        size_hint_y=None, padding=[0, 0, 0, dp(12)])
        col.bind(minimum_height=col.setter('height'))
        col.add_widget(self._label('已下载', 24, DARK, 38, True))
        tip = CardBox(size_hint_y=None, height=dp(72))
        tip.add_widget(self._label(
            '下载完成已自动保存到 Download/bilibilidl，无需再导出。点「打开」直接观看。',
            14, MUTED, 48))
        col.add_widget(tip)
        if not self.history:
            empty = CardBox(size_hint_y=None, height=dp(120))
            empty.add_widget(self._label('还没有下载记录', 18, DARK, 44,
                                         bold=True, halign='center'))
            empty.add_widget(self._label('在主页解析并下载后会出现在这里', 14, MUTED, 32,
                                         halign='center'))
            col.add_widget(empty)
        for item in self.history:
            path = item.get('path') or ''
            exists = os.path.isfile(path)
            has_uri = bool(item.get('content_uri'))
            card = CardBox(size_hint_y=None, height=dp(140), spacing=dp(8))
            name = item.get('name', '文件')
            title = item.get('title', '') or name
            card.add_widget(self._label(name, 16, DARK, 28, True))
            if not exists and not has_uri:
                sub = '文件缺失，请重新下载'
                sub_color = PINK
            elif has_uri or exists:
                sub = (title if title != name else '已保存在 Download/bilibilidl')
                sub_color = MUTED
            else:
                sub = title
                sub_color = MUTED
            card.add_widget(self._label(sub, 13, sub_color, 24))
            row = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(8))
            open_btn = RoundButton(text='打开', bg=BLUE)
            open_btn.bind(on_release=lambda _, it=item: self.open_history_file(it))
            del_btn = RoundButton(text='删除记录', bg=WHITE, fg=PINK)
            del_btn.bind(on_release=lambda _, it=item: self.delete_history(it))
            if not exists and not has_uri:
                open_btn.disabled = True
            row.add_widget(open_btn)
            row.add_widget(del_btn)
            card.add_widget(row)
            col.add_widget(card)
        sc.add_widget(col)
        self.body.add_widget(sc)

    def open_history_file(self, item):
        """打开已下载文件：优先用下载时保存的 content_uri，绝不重复复制。"""
        mime = item.get('mime') or 'video/mp4'
        uri = (item.get('content_uri') or '').strip()
        path = item.get('path', '')
        try:
            if platform == 'android':
                if uri.startswith('content://'):
                    self._android_view_uri(uri, mime)
                    return
                # 旧记录没有 uri：仅打开一次并写回历史，之后不再复制
                if not os.path.isfile(path):
                    self._alert('找不到文件，请重新下载。')
                    return
                filename = item.get('name', os.path.basename(path))
                from jnius import autoclass
                PythonActivity = autoclass('org.kivy.android.PythonActivity')
                Helper = autoclass('org.bilibilidl.MediaMerger')
                result = str(Helper.exportToDownloads(
                    PythonActivity.mActivity, path, filename))
                if not result.startswith('content://'):
                    raise RuntimeError(result)
                item['content_uri'] = result
                self._save_json('history.json', self.history)
                self._android_view_uri(result, mime)
            else:
                if not os.path.isfile(path):
                    self._alert('找不到文件')
                    return
                import webbrowser
                webbrowser.open('file://' + path)
        except Exception as e:
            self._alert('打开失败：' + str(e))

    def export_file(self, item, open_after=False):
        """兼容旧调用：下载时已自动保存，这里只提示或打开。"""
        if item.get('content_uri'):
            if open_after:
                self.open_history_file(item)
            else:
                self._alert('文件已在 Download/bilibilidl，无需再次保存。')
            return
        self.open_history_file(item)

    @run_on_ui_thread
    def _android_view_uri(self, uri_str, mime='video/mp4'):
        from jnius import autoclass
        Intent = autoclass('android.content.Intent')
        Uri = autoclass('android.net.Uri')
        PythonActivity = autoclass('org.kivy.android.PythonActivity')
        intent = Intent(Intent.ACTION_VIEW)
        intent.setDataAndType(Uri.parse(uri_str), mime)
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        try:
            PythonActivity.mActivity.startActivity(intent)
        except Exception:
            # 无匹配应用时退回选择器
            chooser = Intent.createChooser(intent, '打开文件')
            chooser.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            PythonActivity.mActivity.startActivity(chooser)

    def delete_history(self, item):
        path = item.get('path')
        # 同时尝试删本地副本
        if path and os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass
        self.history = [h for h in self.history if h.get('path') != path]
        self._save_json('history.json', self.history)
        self.show_page('history')

    # ---------------------------- mine ------------------------------------
    def _mine_page(self):
        sc = ScrollView(do_scroll_x=False)
        col = BoxLayout(orientation='vertical', spacing=dp(14),
                        size_hint_y=None, padding=[0, 0, 0, dp(16)])
        col.bind(minimum_height=col.setter('height'))

        col.add_widget(self._label('我的', 24, DARK, 38, True))

        # 账号卡片
        acc = CardBox(size_hint_y=None, height=dp(150), spacing=dp(8))
        if self.account:
            acc.add_widget(self._label(
                self.account.get('uname', 'B站用户'), 18, DARK, 32, True))
            acc.add_widget(self._label(
                'UID  %s    VIP  %s' % (
                    self.account.get('mid', '未知'),
                    self.account.get('vipType', 0)), 14, MUTED, 28))
            out = RoundButton(text='退出登录', bg=WHITE, fg=PINK,
                              size_hint_y=None, height=dp(44))
            out.bind(on_release=lambda *_: self.logout())
            acc.add_widget(out)
        else:
            acc.add_widget(self._label('未登录', 18, DARK, 32, True))
            acc.add_widget(self._label(
                '登录后可解析更高画质（以接口返回为准）', 14, MUTED, 28))
            login = RoundButton(text='B站一键登录', size_hint_y=None, height=dp(46))
            login.bind(on_release=lambda *_: self.show_login())
            acc.add_widget(login)
        col.add_widget(acc)

        # 功能说明
        help_card = CardBox(size_hint_y=None, height=dp(150), spacing=dp(6))
        help_card.add_widget(self._label('使用说明', 16, DARK, 28, True))
        help_card.add_widget(self._label(
            '· 在线播放：应用内打开官方播放器，不整段缓存\n'
            '· 下载：保存到 Download/bilibilidl\n'
            '· 登录仅保存在本机，可随时退出清除',
            14, MUTED, 100))
        col.add_widget(help_card)

        note = CardBox(size_hint_y=None, height=dp(88))
        note.add_widget(self._label(
            '画质由 B 站接口实际返回结果决定；登录不代表拥有大会员专属权益。',
            14, MUTED, 64))
        col.add_widget(note)

        sc.add_widget(col)
        self.body.add_widget(sc)

    # ---------------------------- login -----------------------------------
    def show_login(self):
        """一键登录：调起 B 站 App 确认，无需扫码。非 Android 回退二维码。"""
        if self.account:
            self._mine_page_confirm()
            return
        if platform != 'android':
            self.show_login_qr()
            return
        if self.busy:
            return
        self.busy = True
        self.login_cancel.set()
        self.login_cancel = threading.Event()
        cancel = self.login_cancel
        threading.Thread(target=self._app_login_worker, args=(cancel,),
                         daemon=True).start()

    def _app_login_worker(self, cancel):
        try:
            session = new_session()
            url, key = qr_generate(session)  # 同一个登录链接，只是不用生成二维码
            self._open_bili_app(url)
            ui(lambda: setattr(self.status, 'text',
                               '已调起 B 站 App，请在 App 内确认登录…'))
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
                    ui(lambda: self._alert('登录已过期，请重试'))
                    return
                for _ in range(20):
                    if cancel.is_set():
                        return
                    time.sleep(0.1)
            if not cancel.is_set():
                ui(lambda: self._alert('登录超时，请重试'))
        except Exception as e:
            err = str(e)
            ui(lambda err=err: self._alert('登录失败：' + err[:90]))
        finally:
            self.busy = False
            ui(self._enable)

    @run_on_ui_thread
    def _open_bili_app(self, url):
        try:
            from jnius import autoclass
            Intent = autoclass('android.content.Intent')
            Uri = autoclass('android.net.Uri')
            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            intent = Intent(Intent.ACTION_VIEW, Uri.parse(url))
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            PythonActivity.mActivity.startActivity(intent)
        except Exception as e:
            print('[bilibilidl] 调起 B站 App 失败: %s' % e)
            ui(self.show_login_qr)  # 兜底：打不开就退回二维码登录

    def show_login_qr(self):
        """备用登录方式：显示二维码扫码登录。"""
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
        self.login_btn.text = 'B站一键登录'
        self.show_page('mine')
        self._alert('已退出登录并清除本地 Cookie。')

    def _mine_page_confirm(self):
        self.show_page('mine')

    def _make_qr_png(self, url):
        qr = qrcode.QRCode(box_size=8, border=3)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color='black', back_color='white').convert('RGB')
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return buf.getvalue()

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
