# -*- coding: utf-8 -*-
"""bilibilidl: native Android-oriented Bilibili downloader UI. 圆润UI + GIF动图下载修复
改造点：移除弹窗、底部行内提示、分P勾选列表、全粉色、新增任务列表页、分类标签，业务完全保留
"""
import os
import io
import json
import time
import threading
import re
from pathlib import Path
import requests
import qrcode
from kivy.app import App
from kivy.clock import Clock, mainthread
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
from kivy.uix.widget import Widget
from kivy.graphics import Color, RoundedRectangle, Rectangle, Ellipse, Line

from bili_api import (new_session, get_video_info, get_play_info,
                      format_duration, get_stream_sources)
from login_manager import qr_generate, qr_poll, validate_login
from downloader import download_video, preload_merger

def _safe_name(name):
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

# ========== 配色&圆角全局常量【全粉色，移除蓝色】 ==========
PINK = (0.94, 0.29, 0.52, 1)
PALE = (1, 0.94, 0.97, 1)
DARK = (0.14, 0.13, 0.15, 1)
MUTED = (0.42, 0.41, 0.44, 1)
WHITE = (1, 1, 1, 1)
RED_ERR = (0.88, 0.22, 0.32, 1)
GRAY_BORDER = (0.90,0.87,0.89,1)

RADIUS_BIG = dp(32)
RADIUS_MID = dp(26)
RADIUS_SMALL = dp(18)

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
        return Image(fit_mode='cover', **kw)
    except Exception:
        return Image(allow_stretch=True, keep_ratio=True, **kw)

# ========== 基础控件 ==========
class RoundButton(Button):
    def __init__(self, bg=PINK, fg=WHITE, radius=RADIUS_MID,**kw):
        kw.setdefault('font_name', APP_FONT_BOLD)
        super().__init__(**kw)
        self.background_normal = ''
        self.background_down = ''
        self.background_color = (0, 0, 0, 0)
        self.color = fg
        self._bg = bg
        self._radius = radius
        with self.canvas.before:
            Color(*self._bg)
            self._shape = RoundedRectangle(pos=self.pos, size=self.size,
                                           radius=[self._radius])
        self.bind(pos=self._sync, size=self._sync)
    def _sync(self, *_):
        self._shape.pos = self.pos
        self._shape.size = self.size

class CardBox(BoxLayout):
    """圆润圆角卡片容器，大圆角"""
    def __init__(self, radius=RADIUS_BIG, bg=None, **kw):
        kw.setdefault('orientation', 'vertical')
        kw.setdefault('padding', dp(20))
        kw.setdefault('spacing', dp(14))
        super().__init__(**kw)
        self._bg_color = bg or PALE
        with self.canvas.before:
            Color(*self._bg_color)
            self._shape = RoundedRectangle(pos=self.pos, size=self.size,
                                           radius=[radius])
        self.bind(pos=self._sync, size=self._sync)
    def _sync(self, *_):
        self._shape.pos = self.pos
        self._shape.size = self.size

def _label(text, size=15, color=DARK, height=None, bold=False, halign='left', max_lines=None):
    font = APP_FONT_BOLD if bold else APP_FONT
    kw = dict(text=str(text), font_name=font, font_size=dp(size),
              color=color, halign=halign, valign='middle')
    if height:
        kw['size_hint_y'] = None
        kw['height'] = dp(height)
    w = Label(**kw)
    w.bind(size=lambda obj, val: setattr(
        obj, 'text_size', (max(1, val[0]), None)))
    if max_lines is not None:
        w.max_lines = max_lines
        w.shorten = True
        w.shorten_from = "right"
    return w

# ========== 主根布局 ==========
class AppRoot(BoxLayout):
    def __init__(self, app, **kw):
        super().__init__(orientation='vertical',
                         padding=[dp(22), dp(18), dp(22), dp(12)],
                         spacing=dp(16), **kw)
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
        self.settings = self._load_json('settings.json', {"default_quality":720})

        self.cover_cache_dir = os.path.join(self.app.user_data_dir,"cover_cache")
        os.makedirs(self.cover_cache_dir,exist_ok=True)

        # UI状态
        self.current_page = 'home'
        self.selected_part_index = 0
        self.part_mode = "single"
        self.download_task_state = dict()

        self._build()
        self.show_page('home')

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

    # -------------------- 行内底部提示，替代弹窗alert --------------------
    @mainthread
    def set_bottom_tip(self, text):
        if hasattr(self, "bottom_tip_widget"):
            self.bottom_tip_widget.text = str(text)[:140]
    def clear_bottom_tip(self):
        if hasattr(self, "bottom_tip_widget"):
            self.bottom_tip_widget.text = ""

    def _build(self):
        head = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(12))
        head.add_widget(_label('bilibilidl', 24, DARK, bold=True))
        head.add_widget(Label(font_name=APP_FONT))
        self.login_btn = RoundButton(
            text=(self.account.get('uname', '已登录')
                  if self.account else 'B站一键登录'),
            size_hint_x=None, width=dp(150), bg=PALE, fg=PINK, radius=RADIUS_BIG)
        self.login_btn.bind(on_release=lambda *_: self.show_login())
        head.add_widget(self.login_btn)
        self.add_widget(head)

        self.body = BoxLayout(orientation='vertical', spacing=dp(16))
        self.add_widget(self.body)

        # 底部导航：主页 / 下载列表 / 已下载 / 我的
        nav = BoxLayout(size_hint_y=None, height=dp(62), spacing=dp(8))
        self.nav_buttons = {}
        for text, page in [('主页', 'home'), ('下载列表','tasklist'), ('已下载', 'history'), ('我的', 'mine')]:
            b = RoundButton(text=text, bg=PALE, fg=PINK, radius=RADIUS_BIG)
            b.bind(on_release=lambda _, p=page: self.show_page(p))
            nav.add_widget(b)
            self.nav_buttons[page] = b
        self.add_widget(nav)

    def _update_nav_highlight(self):
        for pg,btn in self.nav_buttons.items():
            if pg == self.current_page:
                btn.color = WHITE
                btn._bg = PINK
            else:
                btn.color = PINK
                btn._bg = PALE
            btn.canvas.before.clear()
            with btn.canvas.before:
                Color(*btn._bg)
                RoundedRectangle(pos=btn.pos, size=btn.size, radius=[RADIUS_MID])

    def show_page(self, page):
        self.current_page = page
        self.body.clear_widgets()
        if page == 'home':
            self._home_page()
        elif page == 'tasklist':
            self._tasklist_page()
        elif page == 'history':
            self._history_page()
        else:
            self._mine_page()
        ui(self._update_nav_highlight)

    def _section_title(self, text):
        return _label(text, 15, DARK, 28, bold=True)

    # ========== 主页 ==========
    def _home_page(self):
        sc = ScrollView(do_scroll_x=False)
        col = BoxLayout(orientation='vertical', spacing=dp(16),
                        size_hint_y=None, padding=[0, 0, 0, dp(20)])
        col.bind(minimum_height=col.setter('height'))

        col.add_widget(_label('B站视频下载', 26, DARK, 44, True))
        col.add_widget(_label('粘贴 BV / AV / 链接 / 短链 → 解析 → 下载或应用内播放', 14, MUTED, 34))

        input_card = CardBox(size_hint_y=None)
        self.url_input = TextInput(
            hint_text='BV 号、AV 号、视频链接或 b23.tv 短链接',
            multiline=True, size_hint_y=None, height=dp(84),
            padding=[dp(16), dp(16)], font_name=APP_FONT_BOLD, font_size=dp(16),
            foreground_color=DARK, background_normal='',
            background_active='', background_color=WHITE, cursor_color=PINK)
        input_card.add_widget(self.url_input)
        row = BoxLayout(size_hint_y=None, height=dp(56), spacing=dp(12))
        self.parse_btn = RoundButton(text='解析视频', radius=RADIUS_BIG)
        self.parse_btn.bind(on_release=lambda *_: self.parse_async())
        row.add_widget(self.parse_btn)
        self.cover = make_image(size_hint_x=None, width=dp(84))
        row.add_widget(self.cover)
        input_card.add_widget(row)
        col.add_widget(input_card)

        info_card = CardBox(size_hint_y=None)
        info_card.bind(minimum_height=info_card.setter('height'))
        self.title_lbl = _label('解析后显示标题', 19, DARK, None, True)
        self.title_lbl.size_hint_y = None
        self.title_lbl.height = dp(36)
        self.title_lbl.bind(texture_size=lambda l, s: setattr(
            l, 'height', max(dp(36), s[1] + dp(10))))
        info_card.add_widget(self.title_lbl)
        self.meta_lbl = _label('画质与分P将在解析后显示', 14, MUTED, None)
        self.meta_lbl.size_hint_y = None
        self.meta_lbl.height = dp(36)
        self.meta_lbl.bind(texture_size=lambda l, s: setattr(
            l, 'height', max(dp(36), s[1] + dp(10))))
        info_card.add_widget(self.meta_lbl)
        col.add_widget(info_card)

        # 清晰度
        col.add_widget(self._section_title('清晰度（下载用）'))
        self.quality_spinner = Spinner(
            text='请先解析', values=(), font_name=APP_FONT_BOLD,
            size_hint_y=None, height=dp(54), background_normal='',
            background_color=PALE, color=DARK, font_size=dp(15))
        col.add_widget(self.quality_spinner)

        # 下载内容
        col.add_widget(self._section_title('下载内容'))
        self.mode_spinner = Spinner(
            text='视频 + 音频（MP4）', font_name=APP_FONT_BOLD,
            values=('视频 + 音频（MP4）', '封面图片', '弹幕 XML', '字幕 SRT'),
            size_hint_y=None, height=dp(54), background_normal='',
            background_color=PALE, color=DARK, font_size=dp(15))
        col.add_widget(self.mode_spinner)

        # -------- 分P管理：移除Spinner，改为按钮+列表 --------
        part_head = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(12))
        part_head.add_widget(self._section_title("分P管理"))
        part_head.add_widget(Widget())
        self.part_mode_btn = RoundButton(text="单P", bg=PALE, fg=PINK, size_hint_x=None, width=dp(72), height=dp(34), radius=RADIUS_SMALL)
        self.part_mode_btn.bind(on_release=lambda *_: self.toggle_part_mode())
        part_head.add_widget(self.part_mode_btn)
        col.add_widget(part_head)

        self.part_list = BoxLayout(orientation='vertical', spacing=dp(8), size_hint_y=None)
        self.part_list.bind(minimum_height=self.part_list.setter("height"))
        col.add_widget(self.part_list)

        # 操作按钮，在线播放改为粉色，删除蓝色
        btn_row = BoxLayout(size_hint_y=None, height=dp(58), spacing=dp(14))
        self.dl_btn = RoundButton(text='开始下载', radius=RADIUS_BIG)
        self.dl_btn.bind(on_release=lambda *_: self.start_download())
        btn_row.add_widget(self.dl_btn)
        self.play_btn = RoundButton(text='在线播放', bg=PINK, radius=RADIUS_BIG)
        self.play_btn.bind(on_release=lambda *_: self.start_online_play())
        btn_row.add_widget(self.play_btn)
        col.add_widget(btn_row)

        self.progress = ProgressBar(max=100, value=0, size_hint_y=None, height=dp(12))
        col.add_widget(self.progress)
        self.status = _label('下载保存到本机；在线播放在应用内打开官方播放器。', 14, MUTED, 52)
        col.add_widget(self.status)
        self.log_lbl = _label('', 13, MUTED, 48)
        col.add_widget(self.log_lbl)

        # 底部行内提示条（替代弹窗）
        self.bottom_tip_widget = _label("", size=12, color=MUTED, halign="center")
        self.bottom_tip_widget.size_hint_y = None
        self.bottom_tip_widget.height = dp(28)
        col.add_widget(self.bottom_tip_widget)

        sc.add_widget(col)
        self.body.add_widget(sc)
        self.refresh_part_list()

    def refresh_part_list(self):
        self.part_list.clear_widgets()
        pages = getattr(self, "_pages", None) or []
        task_state = self.download_task_state
        if not pages:
            empty = CardBox(size_hint_y=None, height=dp(60))
            empty.add_widget(_label("暂无分P，请先解析视频",13,MUTED,32,halign="center"))
            self.part_list.add_widget(empty)
            return
        for i,p in enumerate(pages):
            item_layout = BoxLayout(orientation="vertical", spacing=dp(6), size_hint_y=None)
            row = BoxLayout(spacing=dp(12), padding=[dp(12), dp(8)], size_hint_y=None, height=dp(66))
            check_wid = Widget(size_hint_x=None, width=dp(24))
            with check_wid.canvas.before:
                if i == self.selected_part_index and self.part_mode == "single":
                    Color(*PINK)
                    Ellipse(pos=check_wid.pos, size=check_wid.size)
                    Color(1,1,1,1)
                    Line(points=[dp(5), dp(12), dp(10), dp(17), dp(18), dp(7)], width=2)
                else:
                    Color(*MUTED)
                    Line(circle=(dp(12), dp(12), dp(10)), width=1.5)
            txt_col = BoxLayout(orientation="vertical", spacing=dp(3))
            txt_col.add_widget(_label(f"P{i+1} · {p.get('part','')}",14,DARK,bold=True))
            txt_col.add_widget(_label(format_duration(p.get("duration",0)),12,MUTED))
            status_text = "已选择" if (i == self.selected_part_index and self.part_mode=="single") else "待选择"
            status_lab = _label(status_text, 12, PINK if (i == self.selected_part_index and self.part_mode=="single") else MUTED)
            row.add_widget(check_wid)
            row.add_widget(txt_col)
            row.add_widget(Widget())
            row.add_widget(status_lab)
            item_layout.add_widget(row)

            st = task_state.get(i)
            if st is not None:
                pb = ProgressBar(max=100, value=st["progress"], size_hint_y=None, height=dp(8))
                item_layout.add_widget(pb)
                if st["status"] == "wait":
                    stat_txt = _label("排队中",11,MUTED)
                elif st["status"] == "downloading":
                    stat_txt = _label(f"{st['progress']}% · {st['speed']}",11,DARK)
                elif st["status"] == "done":
                    stat_txt = _label("✅完成",11,(0.29,0.74,0.45,1))
                else:
                    stat_txt = _label(f"❌{st['error_msg']}",11,RED_ERR,max_lines=1)
                stat_txt.size_hint_y = None
                stat_txt.height = dp(20)
                item_layout.add_widget(stat_txt)

            cardbg = PALE_PINK if (i == self.selected_part_index and self.part_mode=="single") else WHITE
            card = CardBox(bg=cardbg, size_hint_y=None)
            card.add_widget(item_layout)
            card.bind(on_release=lambda x,idx=i:self.select_part(idx))
            self.part_list.add_widget(card)

    def select_part(self,idx):
        self.selected_part_index = idx
        self.part_mode = "single"
        self.part_mode_btn.text = "单P"
        self.refresh_part_list()

    def toggle_part_mode(self):
        if self.part_mode == "single":
            self.part_mode = "all"
            self.part_mode_btn.text = "全部"
            self.set_bottom_tip("已切换为全部分P模式")
        else:
            self.part_mode = "single"
            self.part_mode_btn.text = "单P"
            self.set_bottom_tip("已切换为单P模式")
        self.refresh_part_list()

    def _get_selected_pages(self):
        pages = getattr(self,"_pages",None) or []
        if not pages:
            return []
        if self.part_mode == "all":
            return [dict(p) for p in pages]
        idx = self.selected_part_index
        if idx >= len(pages):
            idx = 0
        return [dict(pages[idx])]

    def parse_async(self):
        if self.busy:
            return
        text = self.url_input.text.strip()
        if not text:
            self.set_bottom_tip('请先粘贴 BV / AV 号或 B 站链接')
            return
        self.clear_bottom_tip()
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
            ui(lambda err=err: self.set_bottom_tip('解析失败：' + err))
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
        self.selected_part_index = 0
        self.part_mode = "single"
        self.part_mode_btn.text = "单P"

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
            for spinner in (self.quality_spinner, self.mode_spinner):
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
        self.refresh_part_list()
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
            try:
                from PIL import Image as PILImage
                img = PILImage.open(io.BytesIO(raw))
                img.seek(0)
                if img.mode not in ('RGB', 'RGBA'):
                    img = img.convert('RGB')
                img.thumbnail((640, 640))
                buf = io.BytesIO()
                img.save(buf, format='PNG')
                data, ext = buf.getvalue(), 'png'
            except Exception as e:
                print('[bilibilidl] Pillow 解码封面失败: %s (%s)' % (e, ctype))
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
                    print('[bilibilidl] 封面设置失败: %s' % e)
            ui(apply_cover)
        except Exception as e:
            print('[bilibilidl] 封面下载失败: %s' % e)

    def _downloads_dir(self):
        path = os.path.join(self.app.user_data_dir, 'downloads')
        os.makedirs(path, exist_ok=True)
        return path

    def _selected_quality(self):
        selected = self.quality_spinner.text
        return next((q for q, d in self.qualities
                     if ('%s（%s）' % (d, q)) == selected), 0)

    def start_download(self):
        if self.busy:
            return
        if not self.info:
            self.set_bottom_tip('请先解析视频，确认信息和可用画质后再下载。')
            self.parse_async()
            return
        mode = self.mode_spinner.text
        pages = self._get_selected_pages()
        if mode in ('封面图片', '弹幕 XML', '字幕 SRT'):
            if len(pages) != 1:
                self.set_bottom_tip('封面、弹幕和字幕请先选择单个分P。')
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
            self.set_bottom_tip('1080P 及以上画质需要先点击右上角“登录”，使用 B 站授权扫码登录。')
            self.show_login()
            return
        self.busy = True
        self.cancel.clear()
        self.dl_btn.disabled = True
        if hasattr(self, 'play_btn'):
            self.play_btn.disabled = True
        self.parse_btn.disabled = True
        self.progress.value = 0
        self.download_task_state.clear()
        for idx,_p in enumerate(pages):
            self.download_task_state[idx] = {"status":"wait","progress":0,"speed":"-- MB/s","error_msg":""}
        ui(self.refresh_part_list)
        threading.Thread(target=self._download_worker,
                         args=(quality, mode, pages), daemon=True).start()

    def start_online_play(self):
        if not self.info:
            self.set_bottom_tip('请先解析视频')
            self.parse_async()
            return
        pages = self._get_selected_pages()
        if len(pages) != 1:
            self.set_bottom_tip('在线播放请先选择单个分P')
            return
        page = pages[0]
        page_num = int(page.get('page') or 1)
        bvid = (self.info.get('bvid') or '').strip()
        if not bvid:
            self.set_bottom_tip('无法获取 BV 号')
            return
        cookie = self.account.get('cookie', '') if self.account else ''
        self.progress.value = 100
        self.status.text = '正在打开官方播放器…'
        self._open_bili_webview(bvid, page_num, cookie)

    @run_on_ui_thread
    def _open_bili_webview(self, bvid, page_num, cookie):
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
                self.set_bottom_tip('打开播放器失败：' + result)
                self.status.text = '播放失败'
            else:
                self.status.text = '官方播放器中（返回键关闭）'
        except Exception as e:
            self.set_bottom_tip('无法打开播放器：' + str(e))

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
                path = os.path.join(self._downloads_dir(), name)
                with open(path, 'wb') as f:
                    f.write(content)
                self._add_history(name, path, mime, cover_url=(self.info or {}).get('pic',''))
                ui(lambda: setattr(self.status, 'text', mode + '下载完成：' + name))
                return
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
            self._add_history(name, path, mime, cover_url=(self.info or {}).get('pic',''))
            ui(lambda: setattr(self.status, 'text', mode + '下载完成：' + name))
        except Exception as e:
            err = str(e)
            ui(lambda err=err: self.set_bottom_tip(mode + '下载失败：' + err))
        finally:
            self.busy = False
            ui(self._enable)

    def _download_worker(self, quality, mode, pages):
        try:
            out = self._downloads_dir()
            cookie = self.account.get('cookie', '') if self.account else ''
            count = len(pages)
            completed = 0
            for index, page in enumerate(pages, 1):
                if self.cancel.is_set():
                    raise RuntimeError('下载已取消')
                self.download_task_state[index-1]["status"] = "downloading"
                self.download_task_state[index-1]["progress"] = 0
                ui(self.refresh_part_list)
                part_info = dict(self.info)
                part_info.update(page)
                part_info['cid'] = page.get('cid') or self.info.get('cid')
                page_num = int(page.get('page') or index)
                part_title = str(page.get('part') or ('P%d' % page_num))
                part_info['title'] = '%s - P%d %s' % (
                    self.info.get('title', 'B站视频'), page_num, part_title)

                last_time = time.time()
                last_bytes = 0
                def prog(done, total, idx=index):
                    nonlocal last_time, last_bytes
                    now = time.time()
                    pct = int(done / total *100) if total>0 else 0
                    delta_t = now - last_time
                    delta_b = done - last_bytes
                    spd_mb = round(delta_b/1048576/delta_t,1) if delta_t>0 else 0
                    last_time = now
                    last_bytes = done
                    @mainthread
                    def upd():
                        self.download_task_state[idx-1]["progress"] = pct
                        self.download_task_state[idx-1]["speed"] = f"{spd_mb} MB/s"
                        self.progress.value = int(((idx-1)+(pct/100))*100 / max(1,count))
                        self.status.text = f"第 {idx}/{count} P 下载中 {done//1048576}MB"
                        self.refresh_part_list()
                    upd()

                def log(line):
                    value = str(line)[-250:]
                    ui(lambda v=value: setattr(self.log_lbl, 'text', v))

                path = download_video(part_info, cookie, quality, out,
                                      progress=prog, cancel=self.cancel, log=log)
                self.download_task_state[index-1]["status"] = "done"
                ui(self.refresh_part_list)
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
                                  content_uri=content_uri, cover_url=(self.info or {}).get('pic',''))
                completed +=1
            ui(lambda c=completed,n=count: setattr(self.status,'text',f"下载完成：成功{c}/{n}个分P"))
            ui(lambda: setattr(self.progress,"value",100))
        except Exception as e:
            err = str(e)
            for k in self.download_task_state:
                if self.download_task_state[k]["status"] == "downloading":
                    self.download_task_state[k]["status"] = "error"
                    self.download_task_state[k]["error_msg"] = err
            ui(self.refresh_part_list)
            ui(lambda err=err: self.set_bottom_tip('下载失败：' + err))
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

    def _add_history(self, name, path, mime, content_uri='', cover_url=''):
        self.history = [h for h in self.history if h.get('path') != path]
        self.history.insert(0, {
            'name': name, 'path': path, 'mime': mime,
            'content_uri': content_uri or '',
            'created': int(time.time()),
            'title': (self.info or {}).get('title', name),
            'cover_url': cover_url or '',
        })
        self.history = self.history[:200]
        self._save_json('history.json', self.history)

    # ========== 下载列表任务页 ==========
    def _tasklist_page(self):
        sc = ScrollView(do_scroll_x=False)
        col = BoxLayout(orientation='vertical', spacing=dp(16), size_hint_y=None, padding=[dp(16),dp(12),dp(16),dp(24)])
        col.bind(minimum_height=col.setter("height"))
        col.add_widget(_label("下载列表", 26, DARK,44,bold=True))
        col.add_widget(_label("统一管理所有下载任务，切换页面不影响任务状态",12,MUTED,24))
        filter_box = BoxLayout(spacing=dp(8), size_hint_y=None, height=dp(36))
        for lab in ["全部","下载中","排队中","失败"]:
            btn = RoundButton(text=lab, bg=PALE, fg=DARK, size_hint_x=None, width=dp(80), height=dp(36), radius=RADIUS_SMALL)
            filter_box.add_widget(btn)
        col.add_widget(filter_box)
        empty_card = CardBox(size_hint_y=None, height=dp(180))
        empty_card.add_widget(_label("暂无下载任务",18,DARK,40,bold=True,halign="center"))
        empty_card.add_widget(_label("回到主页解析视频开始下载",13,MUTED,30,halign="center"))
        col.add_widget(empty_card)
        sc.add_widget(col)
        self.body.add_widget(sc)

    # ========== 已下载页面，增加分类标签 ==========
    def _history_page(self):
        sc = ScrollView(do_scroll_x=False)
        col = BoxLayout(orientation='vertical', spacing=dp(16),
                        size_hint_y=None, padding=[0, 0, 0, dp(20)])
        col.bind(minimum_height=col.setter('height'))
        col.add_widget(_label('已下载', 26, DARK, 44, True))

        tab_box = BoxLayout(spacing=dp(8), size_hint_y=None, height=dp(36))
        for t in ["全部","视频","图片","弹幕","字幕"]:
            b = RoundButton(text=t, bg=PALE, fg=DARK, size_hint_x=None, width=dp(70), height=dp(36), radius=RADIUS_SMALL)
            tab_box.add_widget(b)
        col.add_widget(tab_box)

        tip = CardBox(size_hint_y=None)
        tip.add_widget(_label(
            '下载完成已自动保存到 Download/bilibilidl，无需再导出。点「打开」直接观看。',
            14, MUTED, 54))
        col.add_widget(tip)
        if not self.history:
            empty = CardBox(size_hint_y=None, height=dp(140))
            empty.add_widget(_label('还没有下载记录', 19, DARK, 50,
                                         bold=True, halign='center'))
            empty.add_widget(_label('在主页解析并下载后会出现在这里', 14, MUTED, 38,
                                         halign='center'))
            col.add_widget(empty)
        for item in self.history:
            path = item.get('path') or ''
            exists = os.path.isfile(path)
            has_uri = bool(item.get('content_uri'))
            cover_url = item.get("cover_url","")
            card = CardBox(size_hint_y=None)
            row_top = BoxLayout(spacing=dp(14),size_hint_y=None,height=dp(100))
            img_widget = make_image(size_hint_x=None,width=dp(92))
            if cover_url:
                threading.Thread(target=self._load_history_cover,args=(cover_url,img_widget),daemon=True).start()
            row_top.add_widget(img_widget)
            text_layout = BoxLayout(orientation="vertical",spacing=dp(6))
            name = item.get('name', '文件')
            title = item.get('title', '') or name
            text_layout.add_widget(_label(title,16,DARK,None,bold=True))
            if not exists and not has_uri:
                sub = '文件缺失，请重新下载'
                sub_color = PINK
            elif has_uri or exists:
                sub = (title if title != name else '已保存在 Download/bilibilidl')
                sub_color = MUTED
            else:
                sub = title
                sub_color = MUTED
            text_layout.add_widget(_label(sub,13,sub_color,26))
            row_top.add_widget(text_layout)
            card.add_widget(row_top)
            btn_row = BoxLayout(size_hint_y=None, height=dp(54), spacing=dp(12))
            open_btn = RoundButton(text='打开', radius=RADIUS_BIG)
            open_btn.bind(on_release=lambda _, it=item: self.open_history_file(it))
            del_btn = RoundButton(text='删除记录', bg=WHITE, fg=PINK, radius=RADIUS_BIG)
            del_btn.bind(on_release=lambda _, it=item: self.delete_history(it))
            if not exists and not has_uri:
                open_btn.disabled = True
            btn_row.add_widget(open_btn)
            btn_row.add_widget(del_btn)
            card.add_widget(btn_row)
            col.add_widget(card)
        sc.add_widget(col)
        self.body.add_widget(sc)

    def _load_history_cover(self,url,img_widget):
        try:
            if url.startswith("//"):
                url="https:"+url
            headers={"Referer":"https://www.bilibili.com/","User-Agent":"Mozilla/5.0"}
            r = requests.get(url,timeout=12,headers=headers)
            r.raise_for_status()
            raw = r.content
            from PIL import Image as PILImage
            img = PILImage.open(io.BytesIO(raw))
            img.seek(0)
            img.thumbnail((320,180))
            buf = io.BytesIO()
            img.save(buf,format="PNG")
            data = buf.getvalue()
            def apply():
                try:
                    img_widget.texture = CoreImage(io.BytesIO(data),ext="png").texture
                except Exception:
                    pass
            ui(apply)
        except Exception:
            pass

    def open_history_file(self, item):
        mime = item.get('mime') or 'video/mp4'
        uri = (item.get('content_uri') or '').strip()
        path = item.get('path', '')
        try:
            if platform == 'android':
                if uri.startswith('content://'):
                    self._android_view_uri(uri, mime)
                    return
                if not os.path.isfile(path):
                    self.set_bottom_tip('找不到文件，请重新下载。')
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
                    self.set_bottom_tip('找不到文件')
                    return
                import webbrowser
                webbrowser.open('file://' + path)
        except Exception as e:
            self.set_bottom_tip('打开失败：' + str(e))

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
            chooser = Intent.createChooser(intent, '打开文件')
            chooser.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            PythonActivity.mActivity.startActivity(chooser)

    def delete_history(self, item):
        path = item.get('path')
        if path and os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass
        self.history = [h for h in self.history if h.get('path') != path]
        self._save_json('history.json', self.history)
        self.show_page('history')

    # ========== 我的页面，分组列表条目样式 ==========
    def _mine_page(self):
        sc = ScrollView(do_scroll_x=False)
        col = BoxLayout(orientation='vertical', spacing=dp(18),
                        size_hint_y=None, padding=[0, 0, 0, dp(22)])
        col.bind(minimum_height=col.setter('height'))
        col.add_widget(_label('我的', 26, DARK, 44, True))

        acc = CardBox(size_hint_y=None)
        acc_row = BoxLayout(spacing=dp(16),size_hint_y=None,height=dp(96))
        avatar_img = make_image(size_hint_x=None,width=dp(80))
        if self.account and self.account.get("face"):
            face_url = self.account["face"]
            if face_url.startswith("//"):
                face_url = "https:" + face_url
            threading.Thread(target=self._load_avatar,args=(face_url,avatar_img),daemon=True).start()
        acc_row.add_widget(avatar_img)
        acc_text = BoxLayout(orientation="vertical",spacing=dp(8))
        if self.account:
            uname = self.account.get('uname', 'B站用户')
            mid = self.account.get('mid', '未知')
            vip = self.account.get('vipType', 0)
            acc_text.add_widget(_label(uname,20,DARK,None,bold=True))
            acc_text.add_widget(_label(f"UID: {mid}    VIP:{vip}",14,MUTED,30))
        else:
            acc_text.add_widget(_label('未登录',20,DARK,None,bold=True))
            acc_text.add_widget(_label('登录解锁更高画质',14,MUTED,30))
        acc_row.add_widget(acc_text)
        acc.add_widget(acc_row)
        if self.account:
            logout_btn = RoundButton(text="退出登录",bg=WHITE,fg=PINK,radius=RADIUS_BIG,size_hint_y=None,height=dp(52))
            logout_btn.bind(on_release=lambda *_:self.logout())
            acc.add_widget(logout_btn)
        else:
            login_btn = RoundButton(text="B站一键登录",radius=RADIUS_BIG,size_hint_y=None,height=dp(52))
            login_btn.bind(on_release=lambda *_:self.show_login())
            acc.add_widget(login_btn)
        col.add_widget(acc)

        col.add_widget(_label("下载管理",16,DARK,30,bold=True))
        dl_group = CardBox(size_hint_y=None)
        def make_list_item(title,subtext,onclick):
            row = BoxLayout(size_hint_y=None,height=dp(60),spacing=dp(12),padding=[dp(8),0])
            left_spacer = Widget(size_hint_x=None,width=dp(32))
            txt_col = BoxLayout(orientation="vertical",spacing=dp(2))
            txt_col.add_widget(_label(title,14,DARK,26))
            if subtext:
                txt_col.add_widget(_label(subtext,12,MUTED,22))
            arrow = _label(">",16,MUTED,28)
            row.add_widget(left_spacer)
            row.add_widget(txt_col)
            row.add_widget(Widget())
            row.add_widget(arrow)
            item_card = CardBox(bg=PALE,size_hint_y=None,height=dp(60),radius=RADIUS_SMALL)
            item_card.add_widget(row)
            item_card.bind(on_release=onclick)
            return item_card
        dl_group.add_widget(make_list_item("下载保存位置","查看文件目录与导出位置",lambda x:None))
        dl_group.add_widget(make_list_item("下载历史","查看已完成、失败和进行中的任务",lambda x:self.show_page("tasklist")))
        dl_group.add_widget(make_list_item("默认下载设置","默认画质、文件命名与下载偏好",lambda x:None))
        col.add_widget(dl_group)

        col.add_widget(_label("账号与隐私",16,DARK,30,bold=True))
        acc_group = CardBox(size_hint_y=None)
        acc_group.add_widget(make_list_item("授权状态","查看当前登录与授权情况",lambda x:None))
        acc_group.add_widget(make_list_item("退出登录","结束当前账号授权",lambda x:self.logout()))
        acc_group.add_widget(make_list_item("清除本地登录信息","清除本地Cookie和授权凭据",lambda x:self.logout()))
        col.add_widget(acc_group)

        col.add_widget(_label("关于 bilibilidl",16,DARK,30,bold=True))
        about_group = CardBox(size_hint_y=None)
        about_group.add_widget(make_list_item("版本信息","当前安装版本",lambda x:None))
        about_group.add_widget(make_list_item("开源项目","GitHub项目与源码",lambda x:None))
        about_group.add_widget(make_list_item("使用说明与问题反馈","",lambda x:None))
        col.add_widget(about_group)

        sc.add_widget(col)
        self.body.add_widget(sc)

    def _load_avatar(self,url,img_widget):
        try:
            headers={"Referer":"https://www.bilibili.com/","User-Agent":"Mozilla/5.0"}
            r = requests.get(url,timeout=12,headers=headers)
            r.raise_for_status()
            raw = r.content
            from PIL import Image as PILImage
            img = PILImage.open(io.BytesIO(raw))
            img.seek(0)
            img.thumbnail((256,256))
            buf = io.BytesIO()
            img.save(buf,format="PNG")
            data = buf.getvalue()
            def apply():
                try:
                    img_widget.texture = CoreImage(io.BytesIO(data),ext="png").texture
                except Exception:
                    pass
            ui(apply)
        except Exception:
            pass

    def show_login(self):
        if self.account:
            self.show_page('mine')
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
            url, key = qr_generate(session)
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
                    ui(lambda: self.set_bottom_tip('登录已过期，请重试'))
                    return
                for _ in range(20):
                    if cancel.is_set():
                        return
                    time.sleep(0.1)
            if not cancel.is_set():
                ui(lambda: self.set_bottom_tip('登录超时，请重试'))
        except Exception as e:
            err = str(e)
            ui(lambda err=err: self.set_bottom_tip('登录失败：' + err[:90]))
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
            ui(self.show_login_qr)

    def show_login_qr(self):
        if self.account:
            self.show_page('mine')
            return
        self.login_cancel.set()
        self.login_cancel = threading.Event()
        cancel = self.login_cancel
        content = BoxLayout(orientation='vertical', padding=dp(20), spacing=dp(14))
        content.add_widget(_label(
            '使用哔哩哔哩 App 扫描二维码并确认登录。',
            14, MUTED, 54, halign='center'))
        self.qr_image = make_image(size_hint=(1, 1))
        content.add_widget(self.qr_image)
        self.qr_status = _label('正在获取二维码…', 14, PINK, 38, halign='center')
        content.add_widget(self.qr_status)
        row = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(14))
        refresh = RoundButton(text='刷新二维码', bg=PALE, fg=PINK, radius=RADIUS_MID)
        close = RoundButton(text='取消', bg=(0.9, 0.89, 0.91, 1), fg=DARK, radius=RADIUS_MID)
        row.add_widget(refresh)
        row.add_widget(close)
        content.add_widget(row)
        pop = Popup(title='B站扫码登录', title_font=APP_FONT, content=content,
                    size_hint=(0.92, 0.84), auto_dismiss=False)
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
        self.set_bottom_tip('登录成功：' + profile.get('uname', 'B站用户'))
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
        self.set_bottom_tip('已退出登录并清除本地 Cookie。')

    def _make_qr_png(self, url):
        qr = qrcode.QRCode(box_size=8, border=3)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color='black', back_color='white').convert('RGB')
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return buf.getvalue()

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
