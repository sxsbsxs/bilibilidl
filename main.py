# -*- coding: utf-8 -*-
"""
bilibilidl main.py
完整整合版：全部下载逻辑 + 双字体（常规/粗体）
功能：视频MP4 / GIF‑WebP封面原样保存 / 弹幕XML / 字幕SRT
自定义弹窗选择器，无原生Spinner；GIF封面防闪退；右上角头像昵称登录；无底部Tab
字体：NotoSansCJKsc-Regular.ttf（常规）、NotoSansCJKsc-Bold.ttf（粗体）
依赖：同目录必须有 bili_api.py、login_manager.py、downloader.py
"""
import os
import re
import io
import json
import time
import threading
import qrcode
import requests
from kivy.app import App
from kivy.clock import Clock, mainthread
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.progressbar import ProgressBar
from kivy.uix.widget import Widget
from kivy.uix.image import AsyncImage, Image
from kivy.graphics import Color, RoundedRectangle, Ellipse, Line
from kivy.core.text import LabelBase
from kivy.core.image import Image as CoreImage
from kivy.utils import platform

from bili_api import new_session, get_video_info, get_play_info
from login_manager import qr_generate, qr_poll, validate_login
from downloader import download_video, preload_merger

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIE_FILE = os.path.join(BASE_DIR, "cookie_store.json")

# ==========字体：双字体 常规+粗体 ==========
FONT_REG = os.path.join(BASE_DIR, "NotoSansCJKsc-Regular.ttf")
FONT_BOLD = os.path.join(BASE_DIR, "NotoSansCJKsc-Bold.ttf")
APP_FONT = "Roboto"
APP_FONT_BOLD = "Roboto"

if os.path.isfile(FONT_REG):
    try:
        LabelBase.register(name="AppCJKFont", fn_regular=FONT_REG)
        APP_FONT = "AppCJKFont"
    except Exception:
        pass

if os.path.isfile(FONT_BOLD):
    try:
        LabelBase.register(name="AppCJKFontBold", fn_regular=FONT_BOLD)
        APP_FONT_BOLD = "AppCJKFontBold"
    except Exception:
        pass

# ==========配色 全粉色无蓝色 ==========
MAIN_PINK = (0.97, 0.55, 0.68, 1)
LIGHT_PINK = (1, 0.93, 0.95, 1)
TEXT_GRAY = (0.45, 0.45, 0.45, 1)
TEXT_BLACK = (0.1, 0.1, 0.1, 1)
WHITE = (1.0, 1.0, 1.0, 1)
RED_ERR = (0.88, 0.22, 0.32, 1)
GREEN_OK = (0.28, 0.72, 0.46, 1)

# ==========基础控件 ==========
class RoundedCard(BoxLayout):
    def __init__(self, radius=dp(24), bg=WHITE, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.padding = dp(16)
        self.spacing = dp(10)
        self.radius = radius
        self.bg = bg
        with self.canvas.before:
            Color(*self.bg)
            self.rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[self.radius])
        self.bind(pos=self.update_rect, size=self.update_rect)
    def update_rect(self, *_):
        self.rect.pos = self.pos
        self.rect.size = self.size

class PinkBtn(Button):
    def __init__(self, bg=MAIN_PINK, fg=WHITE, radius=dp(22),**kwargs):
        super().__init__(**kwargs)
        self.background_normal = ""
        self.background_down = ""
        self.background_color = (0,0,0,0)
        self.color = fg
        self.font_name = APP_FONT
        self.font_size = dp(15)
        self._bg = bg
        self._radius = radius
        with self.canvas.before:
            Color(*self._bg)
            self.rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[self._radius])
        self.bind(pos=self.update_rect, size=self.update_rect)
    def update_rect(self, *_):
        self.rect.pos = self.pos
        self.rect.size = self.size

class PinkGhostBtn(Button):
    def __init__(self,**kwargs):
        super().__init__(**kwargs)
        self.background_normal = ""
        self.background_down = ""
        self.background_color = (0,0,0,0)
        self.color = MAIN_PINK
        self.font_name = APP_FONT
        self.font_size = dp(13)
        with self.canvas.before:
            Color(*LIGHT_PINK)
            self.rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(18)])
        self.bind(pos=self.update_rect, size=self.update_rect)
    def update_rect(self, *_):
        self.rect.pos = self.pos
        self.rect.size = self.size

class PinkInput(TextInput):
    def __init__(self,**kwargs):
        super().__init__(**kwargs)
        self.background_normal = ""
        self.background_active = ""
        self.cursor_color = MAIN_PINK
        self.font_name = APP_FONT
        self.font_size = dp(15)
        with self.canvas.before:
            Color(*LIGHT_PINK)
            self.rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(18)])
        self.bind(pos=self.update_rect, size=self.update_rect)
    def update_rect(self, *_):
        self.rect.pos = self.pos
        self.rect.size = self.size

def make_label(text, size=14, color=TEXT_BLACK, height=None, bold=False, halign="left"):
    if bold:
        font_name = APP_FONT_BOLD
    else:
        font_name = APP_FONT
    kw = dict(text=str(text), font_name=font_name, font_size=dp(size), color=color, halign=halign, valign="middle")
    if height:
        kw["size_hint_y"] = None
        kw["height"] = dp(height)
    lab = Label(**kw)
    lab.bind(size=lambda obj,val: setattr(obj,"text_size",(max(1,val[0]),None)))
    return lab

# 安全头像
class SafeAvatarBox(BoxLayout):
    def __init__(self, size=dp(40),**kwargs):
        super().__init__(**kwargs)
        self.size_hint_x = None
        self.size_hint_y = None
        self.width = size
        self.height = size
        self.orientation = "vertical"
        self.avatar_img = AsyncImage(size_hint=(1,1), allow_stretch=True)
        with self.canvas.before:
            Color(*MAIN_PINK)
            self.bg_ellipse = Ellipse(pos=self.pos, size=self.size)
        self.bind(pos=self._sync, size=self._sync)
        self.add_widget(self.avatar_img)
    def _sync(self,*_):
        self.bg_ellipse.pos = self.pos
        self.bg_ellipse.size = self.size
    @mainthread
    def set_avatar_url(self, url):
        if not url:
            self.avatar_img.source = ""
            return
        if url.startswith("//"):
            url = "https:" + url
        self.avatar_img.source = url

# ==========通用自定义弹窗选择器（替代Spinner） ==========
class OptionSelectPopup(Popup):
    def __init__(self, title, option_list, current_idx, callback,**kwargs):
        super().__init__(title=title, size_hint=(0.85,0.75), auto_dismiss=False,**kwargs)
        self.callback = callback
        self.opt_list = option_list
        self.sel_idx = current_idx
        content = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(10))
        from kivy.uix.scrollview import ScrollView
        sv = ScrollView(do_scroll_x=False)
        col = BoxLayout(orientation="vertical", spacing=dp(8), size_hint_y=None)
        col.bind(minimum_height=col.setter("height"))
        for i,txt in enumerate(option_list):
            bgc = MAIN_PINK if i == self.sel_idx else LIGHT_PINK
            fgcol = WHITE if i==self.sel_idx else TEXT_BLACK
            btn = PinkBtn(text=txt, bg=bgc, fg=fgcol, size_hint_y=None, height=dp(48))
            btn.bind(on_press=lambda x,idx=i:self._on_select(idx))
            col.add_widget(btn)
        sv.add_widget(col)
        content.add_widget(sv)
        close_btn = PinkBtn(text="取消", bg=RED_ERR, size_hint_y=None, height=dp(50))
        close_btn.bind(on_press=self.dismiss)
        content.add_widget(close_btn)
        self.content = content
    def _on_select(self, idx):
        self.sel_idx = idx
        self.callback(idx)
        self.dismiss()

# ==========登录弹窗 ==========
class LoginPopup(Popup):
    def __init__(self, root_app, **kwargs):
        super().__init__(title="登录B站", size_hint=(0.88,0.82), auto_dismiss=False,**kwargs)
        self.root_app = root_app
        self.qr_key = None
        self.qr_url = None
        self.poll_running = False
        content = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(14))
        self.qr_display = Image(size_hint_y=None, height=dp(220))
        self.status_lab = make_label("选择登录方式", size=14, color=TEXT_GRAY, height=30, halign="center")
        btn_box = BoxLayout(spacing=dp(10), size_hint_y=None, height=dp(50))
        self.btn_jump = PinkGhostBtn(text="跳转B站App登录")
        self.btn_jump.bind(on_press=self.jump_bilibili_app)
        self.btn_qr = PinkGhostBtn(text="生成登录二维码")
        self.btn_qr.bind(on_press=self.gen_qr_code)
        self.btn_close = PinkBtn(text="关闭", bg=RED_ERR, size_hint_y=None, height=dp(48))
        self.btn_close.bind(on_press=self.dismiss)
        btn_box.add_widget(self.btn_jump)
        btn_box.add_widget(self.btn_qr)
        content.add_widget(self.status_lab)
        content.add_widget(self.qr_display)
        content.add_widget(btn_box)
        content.add_widget(self.btn_close)
        self.content = content
    def jump_bilibili_app(self,*args):
        self.status_lab.text = "正在唤起B站App..."
        if platform == "android":
            try:
                from jnius import autoclass
                Intent = autoclass('android.content.Intent')
                Uri = autoclass('android.net.Uri')
                PythonActivity = autoclass('org.kivy.android.PythonActivity')
                intent = Intent(Intent.ACTION_VIEW, Uri.parse("bilibili://login"))
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                PythonActivity.mActivity.startActivity(intent)
            except Exception as e:
                self.status_lab.text = f"唤起失败:{str(e)}"
        else:
            self.status_lab.text = "仅安卓支持跳转B站App"
    def gen_qr_code(self,*args):
        self.status_lab.text = "获取二维码中..."
        def thr():
            try:
                ses = new_session()
                url, key = qr_generate(ses)
                self.qr_key = key
                self.qr_url = url
                qr = qrcode.QRCode(box_size=7,border=2)
                qr.add_data(self.qr_url)
                qr.make(fit=True)
                img = qr.make_image(fill_color="black",back_color="white").convert("RGB")
                buf = io.BytesIO()
                img.save(buf,format="PNG")
                buf.seek(0)
                tex = CoreImage(buf,ext="png").texture
                @mainthread
                def ui_up():
                    self.qr_display.texture = tex
                    self.status_lab.text = "请用B站App扫描二维码登录"
                    self.start_poll()
                ui_up()
            except Exception as e:
                @mainthread
                def err_ui():
                    self.status_lab.text = f"二维码获取失败 {str(e)}"
                err_ui()
        threading.Thread(target=thr,daemon=True).start()
    def start_poll(self):
        self.poll_running = True
        def poll_loop():
            while self.poll_running:
                try:
                    ses = new_session()
                    code, msg = qr_poll(ses, self.qr_key)
                    if code ==0:
                        cookie, profile = validate_login(ses)
                        @mainthread
                        def succ():
                            self.root_app.save_cookie_local(cookie,profile)
                            self.status_lab.text = "✅登录成功"
                            Clock.schedule_once(lambda x:self.dismiss(),1.2)
                        succ()
                        return
                    elif code ==86038:
                        @mainthread
                        def expire():
                            self.status_lab.text = "❌二维码已过期，请重新生成"
                        expire()
                        return
                except Exception:
                    pass
                time.sleep(1.8)
        threading.Thread(target=poll_loop,daemon=True).start()
    def dismiss(self):
        self.poll_running = False
        super().dismiss()

# ==========主页 ==========
class HomeScreen(Screen):
    def __init__(self, root_app,**kwargs):
        super().__init__(**kwargs)
        self.root_app = root_app
        self.info = None
        self.pages = []
        self.qual_options = []
        self.dl_mode_options = [
            "视频 + 音频（MP4）",
            "封面图片(保留GIF/WebP)",
            "弹幕XML",
            "字幕SRT"
        ]
        self.sel_qual_idx = 0
        self.sel_dl_mode_idx =0
        self.selected_part_idx =0
        self.part_mode = "single"
        self.cover_raw_bytes = None
        self.cover_url = ""

        root = BoxLayout(orientation="vertical")
        top_bar = BoxLayout(size_hint_y=None, height=dp(52), padding=[dp(8),0],spacing=dp(10))
        top_bar.add_widget(make_label("bilibilidl",22,TEXT_BLACK,44,bold=True))
        top_bar.add_widget(Widget())
        self.user_area = BoxLayout(size_hint_x=None,spacing=dp(8),size_hint_y=None,height=dp(42))
        self.avatar_widget = SafeAvatarBox(size=dp(40))
        self.uname_label = make_label("",14,TEXT_BLACK,38)
        self.login_top_btn = PinkGhostBtn(text="登录",size_hint_x=None,width=dp(90),height=dp(42))
        self.login_top_btn.bind(on_press=self.open_login_popup)
        self.user_area.add_widget(self.login_top_btn)
        top_bar.add_widget(self.user_area)
        root.add_widget(top_bar)

        from kivy.uix.scrollview import ScrollView
        scroll = ScrollView(do_scroll_x=False)
        col = BoxLayout(orientation="vertical",spacing=dp(14),size_hint_y=None,padding=[dp(20),0,dp(20),dp(24)])
        col.bind(minimum_height=col.setter("height"))

        self.link_input = PinkInput(hint_text="粘贴BV / AV / B站分享链接",size_hint_y=None,height=dp(56),multiline=False)
        col.add_widget(self.link_input)
        col.add_widget(make_label("粘贴视频链接后点击解析",13,TEXT_GRAY,28,halign="center"))
        self.parse_btn = PinkBtn(text="解析",size_hint_y=None,height=dp(58))
        self.parse_btn.bind(on_press=self.on_parse)
        col.add_widget(self.parse_btn)
        self.result_text = make_label("",13,TEXT_GRAY,80,halign="center")
        col.add_widget(self.result_text)

        self.qual_select_btn = PinkGhostBtn(text="请先解析", size_hint_y=None, height=dp(48))
        self.qual_select_btn.bind(on_press=self.open_qual_popup)
        self.qual_select_btn.opacity =0
        col.add_widget(self.qual_select_btn)

        self.mode_select_btn = PinkGhostBtn(text=self.dl_mode_options[0], size_hint_y=None, height=dp(48))
        self.mode_select_btn.bind(on_press=self.open_dlmode_popup)
        col.add_widget(self.mode_select_btn)

        part_head = BoxLayout(size_hint_y=None,height=dp(38),spacing=dp(10))
        part_head.add_widget(make_label("分P选择",14,TEXT_BLACK,30,bold=True))
        part_head.add_widget(Widget())
        self.part_mode_btn = PinkGhostBtn(text="单P",size_hint_x=None,width=dp(70),height=dp(34))
        self.part_mode_btn.bind(on_press=self.toggle_part_mode)
        part_head.add_widget(self.part_mode_btn)
        col.add_widget(part_head)
        self.part_container = BoxLayout(orientation="vertical",spacing=dp(8),size_hint_y=None)
        self.part_container.bind(minimum_height=self.part_container.setter("height"))
        col.add_widget(self.part_container)

        self.download_btn = PinkBtn(text="开始下载",size_hint_y=None,height=dp(58))
        self.download_btn.bind(on_press=self.start_download)
        col.add_widget(self.download_btn)

        progress_card = RoundedCard(size_hint_y=None)
        self.prog_bar = ProgressBar(max=100,value=0,size_hint_y=None,height=dp(10))
        progress_card.add_widget(self.prog_bar)
        self.task_status_lab = make_label("准备就绪",12,TEXT_GRAY,26)
        progress_card.add_widget(self.task_status_lab)
        col.add_widget(progress_card)

        scroll.add_widget(col)
        root.add_widget(scroll)
        self.add_widget(root)
        self.refresh_part_ui()

    @mainthread
    def update_login_top_ui(self,profile):
        self.user_area.clear_widgets()
        if profile and isinstance(profile,dict) and profile.get("uname"):
            self.user_area.add_widget(self.avatar_widget)
            self.uname_label.text = profile.get("uname","")
            self.user_area.add_widget(self.uname_label)
            face_url = profile.get("face","")
            self.avatar_widget.set_avatar_url(face_url)
        else:
            self.user_area.add_widget(self.login_top_btn)

    def open_login_popup(self,*args):
        pop = LoginPopup(self.root_app)
        pop.open()

    def open_qual_popup(self,*args):
        if not self.qual_options:
            return
        def cb(idx):
            self.sel_qual_idx = idx
            self.qual_select_btn.text = self.qual_options[idx]
        p = OptionSelectPopup("选择清晰度", self.qual_options, self.sel_qual_idx, cb)
        p.open()

    def open_dlmode_popup(self,*args):
        def cb(idx):
            self.sel_dl_mode_idx = idx
            self.mode_select_btn.text = self.dl_mode_options[idx]
        p = OptionSelectPopup("选择下载类型", self.dl_mode_options, self.sel_dl_mode_idx, cb)
        p.open()

    def on_parse(self,*args):
        txt = self.link_input.text.strip()
        if not txt:
            self.result_text.text = "请输入AV/BV或者B站链接"
            return
        self.result_text.text = "正在解析视频..."
        threading.Thread(target=self._parse_work,args=(txt,),daemon=True).start()

    def _parse_work(self,text):
        try:
            cookie = self.root_app.get_cookie()
            sess = new_session(cookie)
            info = get_video_info(sess,text)
            pages = info.get("pages",[])
            if not pages:
                pages = [{"page":1,"cid":info.get("cid"),"part":info.get("title","P1"),"duration":info.get("duration",0)}]
            play_info = get_play_info(sess,info,cookie)
            accept_q = play_info["data"].get("accept_quality",[])
            accept_desc = play_info["data"].get("accept_description",[])
            q_list = []
            logged = bool(cookie)
            for q,d in zip(accept_q,accept_desc):
                q = int(q)
                if q >=80 and not logged:
                    continue
                q_list.append(f"{d} ({q}P)")
            pic_url = info.get("pic","")
            raw_bytes = None
            if pic_url:
                try:
                    if pic_url.startswith("//"):
                        pic_url = "https:" + pic_url
                    hd = {"User-Agent":"Mozilla/5.0","Referer":"https://www.bilibili.com"}
                    resp = requests.get(pic_url, timeout=15, headers=hd)
                    resp.raise_for_status()
                    raw_bytes = resp.content
                except Exception:
                    raw_bytes = None
            self.cover_url = pic_url
            self.cover_raw_bytes = raw_bytes

            @mainthread
            def ok_ui():
                self.info = info
                self.pages = pages
                self.qual_options = q_list
                if q_list:
                    self.sel_qual_idx =0
                    self.qual_select_btn.text = q_list[0]
                    self.qual_select_btn.opacity =1
                self.result_text.text = f"解析成功：{info.get('title')}\n分P数量 {len(pages)}"
                self.refresh_part_ui()
            ok_ui()
        except Exception as err:
            err_msg = str(err)[:120]
            @mainthread
            def err_ui():
                self.result_text.text = f"解析失败：{err_msg}"
            err_ui()

    def toggle_part_mode(self,*args):
        if self.part_mode == "single":
            self.part_mode = "all"
            self.part_mode_btn.text = "全部"
        else:
            self.part_mode = "single"
            self.part_mode_btn.text = "单P"
        self.refresh_part_ui()

    def select_one_part(self,idx):
        self.selected_part_idx = idx
        self.part_mode = "single"
        self.part_mode_btn.text = "单P"
        self.refresh_part_ui()

    def refresh_part_ui(self):
        self.part_container.clear_widgets()
        if not self.pages:
            empty_card = RoundedCard(bg=LIGHT_PINK,size_hint_y=None,height=dp(60))
            empty_card.add_widget(make_label("解析视频后显示分P列表",13,TEXT_GRAY,30,halign="center"))
            self.part_container.add_widget(empty_card)
            return
        for i,p in enumerate(self.pages):
            selected = (i == self.selected_part_idx and self.part_mode=="single") or self.part_mode == "all"
            item_layout = BoxLayout(spacing=dp(12),padding=[dp(12),dp(8)],size_hint_y=None,height=dp(64))
            check_w = Widget(size_hint_x=None,width=dp(24))
            with check_w.canvas.before:
                if selected:
                    Color(*MAIN_PINK)
                    Ellipse(pos=check_w.pos,size=check_w.size)
                    Color(1,1,1,1)
                    Line(points=[dp(5),dp(12),dp(10),dp(17),dp(18),dp(7)],width=2)
                else:
                    Color(*TEXT_GRAY)
                    Line(circle=(dp(12),dp(12),dp(10)),width=1.4)
            txt_col = BoxLayout(orientation="vertical",spacing=dp(2))
            txt_col.add_widget(make_label(f"P{i+1} · {p.get('part','')}",14,TEXT_BLACK,24,bold=True))
            txt_col.add_widget(make_label(f"{p.get('duration',0)} s",12,TEXT_GRAY,22))
            stat_txt = "已选择" if selected else "待选择"
            stat_lab = make_label(stat_txt,12,MAIN_PINK if selected else TEXT_GRAY,24)
            item_layout.add_widget(check_w)
            item_layout.add_widget(txt_col)
            item_layout.add_widget(Widget())
            item_layout.add_widget(stat_lab)
            card = RoundedCard(bg=LIGHT_PINK if selected else WHITE,size_hint_y=None)
            card.add_widget(item_layout)
            card.bind(on_release=lambda x,idx=i:self.select_one_part(idx))
            self.part_container.add_widget(card)

    def start_download(self,*args):
        if not self.info:
            self.result_text.text = "⚠请先解析视频"
            return
        if not self.pages:
            self.result_text.text = "⚠没有可用分P"
            return
        dl_mode = self.dl_mode_options[self.sel_dl_mode_idx]
        select_pages = []
        if self.part_mode == "all":
            select_pages = [dict(p) for p in self.pages]
        else:
            select_pages.append(dict(self.pages[self.selected_part_idx]))
        cookie = self.root_app.get_cookie()

        if platform == "android":
            out_dir = os.path.join(os.environ.get("ANDROID_APP_PATH","."),"downloads")
        else:
            out_dir = "./downloads"
        os.makedirs(out_dir,exist_ok=True)

        self.prog_bar.value = 0
        self.task_status_lab.text = "处理中…"

        def worker():
            try:
                bvid = self.info.get("bvid","video")
                for page in select_pages:
                    cid = page.get("cid")
                    page_num = page.get("page",1)
                    if dl_mode == "封面图片(保留GIF/WebP)":
                        if self.cover_raw_bytes is None:
                            @mainthread
                            def ui_err():
                                self.result_text.text = "⚠封面原始数据为空，请重新解析"
                            ui_err()
                            return
                        raw = self.cover_raw_bytes
                        if raw.startswith(b"GIF8"):
                            ext = "gif"
                        elif raw[:4]==b"RIFF" and b"WEBP" in raw[:16]:
                            ext = "webp"
                        elif raw.startswith(b"\x89PNG"):
                            ext = "png"
                        else:
                            ext = "jpg"
                        fn = f"{bvid}_cover.{ext}"
                        fullpath = os.path.join(out_dir, fn)
                        with open(fullpath,"wb") as f:
                            f.write(raw)
                        @mainthread
                        def ok():
                            self.task_status_lab.text = f"✅封面保存 {fn}"
                        ok()

                    elif dl_mode == "弹幕XML":
                        sess = new_session(cookie)
                        danmu_url = f"https://comment.bilibili.com/{cid}.xml"
                        resp = sess.get(danmu_url,timeout=20)
                        resp.raise_for_status()
                        xml_data = resp.content
                        if not xml_data or b"<i" not in xml_data[:600]:
                            @mainthread
                            def ui_e():
                                self.result_text.text = "⚠该分P无有效弹幕"
                            ui_e()
                            continue
                        fn = f"{bvid}_P{page_num}_danmaku.xml"
                        fullpath = os.path.join(out_dir,fn)
                        with open(fullpath,"wb") as f:
                            f.write(xml_data)
                        @mainthread
                        def ok():
                            self.task_status_lab.text = f"✅弹幕保存 {fn}"
                        ok()

                    elif dl_mode == "字幕SRT":
                        sess = new_session(cookie)
                        params = {"cid":cid}
                        if bvid:
                            params["bvid"] = bvid
                        resp = sess.get("https://api.bilibili.com/x/player/v2",params=params,timeout=20)
                        resp.raise_for_status()
                        jdata = resp.json()
                        subs = ((jdata.get("data") or {}).get("subtitle") or {}).get("subtitles") or []
                        if not subs:
                            @mainthread
                            def ui_e():
                                self.result_text.text = "⚠该分P没有字幕"
                            ui_e()
                            continue
                        sub_url = subs[0].get("subtitle_url","")
                        if sub_url.startswith("//"):
                            sub_url = "https:"+sub_url
                        sr = sess.get(sub_url,timeout=20)
                        sr.raise_for_status()
                        sub_json = sr.json()
                        def stamp(sec):
                            sec = max(0.0, float(sec or 0))
                            h=int(sec//3600)
                            m=int((sec%3600)//60)
                            s=int(sec%60)
                            ms=int((sec-int(sec))*1000)
                            return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
                        lines = []
                        for idx,item in enumerate((sub_json.get("body") or []),1):
                            lines.append(str(idx))
                            lines.append(f"{stamp(item.get('from'))} --> {stamp(item.get('to'))}")
                            lines.append(str(item.get("content","")))
                            lines.append("")
                        srt_text = "\n".join(lines)
                        fn = f"{bvid}_P{page_num}.srt"
                        fullpath = os.path.join(out_dir,fn)
                        with open(fullpath,"w",encoding="utf-8") as f:
                            f.write(srt_text)
                        @mainthread
                        def ok():
                            self.task_status_lab.text = f"✅字幕保存 {fn}"
                        ok()

                    elif dl_mode == "视频 + 音频（MP4）":
                        @mainthread
                        def ui_dl_start():
                            self.task_status_lab.text = f"开始下载 P{page_num}"
                        ui_dl_start()
                        def progress_cb(pct):
                            @mainthread
                            def set_p():
                                self.prog_bar.value = pct
                                self.task_status_lab.text = f"下载进度：{pct}%"
                            set_p()
                        try:
                            download_video(
                                sess,
                                self.info,
                                page,
                                self.qual_options[self.sel_qual_idx],
                                out_dir,
                                bvid,
                                progress_callback=progress_cb
                            )
                            @mainthread
                            def ok():
                                self.task_status_lab.text = f"✅P{page_num} 视频合并完成"
                                self.prog_bar.value = 100
                            ok()
                        except Exception as e:
                            @mainthread
                            def err():
                                self.task_status_lab.text = f"❌P{page_num}下载失败 {str(e)}"
                            err()
            except Exception as e:
                @mainthread
                def top_err():
                    self.task_status_lab.text = f"任务异常：{str(e)}"
                top_err()
        threading.Thread(target=worker,daemon=True).start()

# ==========App主入口 ==========
class BiliDlApp(App):
    def build(self):
        self.title = "bilibilidl"
        self.sm = ScreenManager()
        self.home = HomeScreen(self, name="home")
        self.sm.add_widget(self.home)
        self.cookie_data = None
        self.profile_data = None
        self.load_cookie_local()
        return self.sm

    def save_cookie_local(self, cookie, profile):
        self.cookie_data = cookie
        self.profile_data = profile
        with open(COOKIE_FILE,"w",encoding="utf-8") as f:
            json.dump({"cookie":cookie,"profile":profile},f,ensure_ascii=False,indent=2)
        self.home.update_login_top_ui(profile)

    def load_cookie_local(self):
        if os.path.exists(COOKIE_FILE):
            try:
                with open(COOKIE_FILE,"r",encoding="utf-8") as f:
                    d = json.load(f)
                self.cookie_data = d.get("cookie")
                self.profile_data = d.get("profile")
                self.home.update_login_top_ui(self.profile_data)
            except Exception:
                pass

    def get_cookie(self):
        return self.cookie_data

if __name__ == "__main__":
    BiliDlApp().run()
