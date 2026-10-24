# -*- coding: utf-8 -*-
"""
bilibilidl
【无底部任何tab/导航栏】｜右上角登录弹窗(App跳转+二维码扫码)
解析后显示清晰度、分P勾选列表｜异常捕获不闪退｜GIF封面原始字节保存
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
from kivy.uix.spinner import Spinner
from kivy.uix.progressbar import ProgressBar
from kivy.uix.widget import Widget
from kivy.uix.image import Image
from kivy.graphics import Color, RoundedRectangle, Ellipse, Line
from kivy.core.text import LabelBase
from kivy.core.image import Image as CoreImage
from kivy.utils import platform

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ========== 字体 ==========
FONT_PATH = os.path.join(BASE_DIR, "NotoSansCJKsc-Regular.ttf")
APP_FONT = "Roboto"
if os.path.exists(FONT_PATH):
    try:
        LabelBase.register(name="scfont", fn_regular=FONT_PATH)
        APP_FONT = "scfont"
    except Exception:
        pass

# ========== 主题配色 全粉色 ==========
MAIN_PINK = (0.97, 0.55, 0.68, 1)
LIGHT_PINK = (1, 0.93, 0.95, 1)
TEXT_GRAY = (0.45, 0.45, 0.45, 1)
TEXT_BLACK = (0.1, 0.1, 0.1, 1)
WHITE = (1, 1, 1, 1)
RED_ERR = (0.88, 0.22, 0.32, 1)
GREEN_OK = (0.28, 0.72, 0.46, 1)

# ========== 基础控件 ==========
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
    def update_rect(self, *args):
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
    def update_rect(self, *args):
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
    def update_rect(self, *args):
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
    def update_rect(self, *args):
        self.rect.pos = self.pos
        self.rect.size = self.size

def make_label(text, size=14, color=TEXT_BLACK, height=None, bold=False, halign="left"):
    kw = dict(text=str(text), font_name=APP_FONT, font_size=dp(size), color=color, halign=halign, valign="middle")
    if height:
        kw["size_hint_y"] = None
        kw["height"] = dp(height)
    lab = Label(**kw)
    lab.bind(size=lambda obj,val: setattr(obj,"text_size",(max(1,val[0]),None)))
    return lab

# ========== 登录弹窗：跳转B站App / 二维码扫码登录 ==========
class LoginPopup(Popup):
    def __init__(self, root_app, **kwargs):
        super().__init__(title="登录B站", size_hint=(0.88,0.82), auto_dismiss=False,**kwargs)
        self.root_app = root_app
        self.qr_key = None
        self.qr_url = None
        self.poll_running = False

        content = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(14))
        self.qr_img = Image(size_hint_y=None, height=dp(220))
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
        content.add_widget(self.qr_img)
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
                ses = requests.Session()
                res = ses.get("https://passport.bilibili.com/x/passport-login/web/qrcode/generate",timeout=15)
                j = res.json()
                self.qr_key = j["data"]["qrcode_key"]
                self.qr_url = j["data"]["url"]
                qr = qrcode.QRCode(box_size=7,border=2)
                qr.add_data(self.qr_url)
                qr.make(fit=True)
                img = qr.make_image(fill_color="black",back_color="white").convert("RGB")
                buf = io.BytesIO()
                img.save(buf,format="PNG")
                buf.seek(0)
                @mainthread
                def ui_up():
                    self.qr_img.texture = CoreImage(buf,ext="png").texture
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
                    ses = requests.Session()
                    resp = ses.get(f"https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key={self.qr_key}",timeout=12)
                    jd = resp.json()
                    code = jd["data"]["code"]
                    if code ==0:
                        cookie_dict = resp.cookies.get_dict()
                        @mainthread
                        def succ():
                            self.root_app.save_login_cookie(cookie_dict)
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

# ========== 唯一主页面（无底部任何tab！！） ==========
class HomeScreen(Screen):
    def __init__(self, root_app,**kwargs):
        super().__init__(**kwargs)
        self.root_app = root_app
        self.info = None
        self.pages = []
        self.qual_list = []
        self.selected_part_idx =0
        self.part_mode = "single"
        self.download_task_state = dict()

        root = BoxLayout(orientation="vertical")
        # --------顶部栏：标题 + 右上角登录按钮--------
        top_bar = BoxLayout(size_hint_y=None, height=dp(52), padding=[dp(8),0])
        top_bar.add_widget(make_label("bilibilidl",22,TEXT_BLACK,44,bold=True))
        top_bar.add_widget(Widget())
        self.login_top_btn = PinkGhostBtn(text="登录",size_hint_x=None,width=dp(90),height=dp(42))
        self.login_top_btn.bind(on_press=self.open_login_popup)
        top_bar.add_widget(self.login_top_btn)
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

        # 清晰度下拉，解析完成才显示
        self.quality_sp = Spinner(text="--",values=[],font_name=APP_FONT,size_hint_y=None,height=dp(48),background_normal="",background_color=LIGHT_PINK,color=TEXT_BLACK)
        self.quality_sp.opacity = 0
        col.add_widget(self.quality_sp)

        # 分P头部：单P / 全部
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

        # 进度卡片
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

    def open_login_popup(self,*args):
        pop = LoginPopup(self.root_app)
        pop.open()

    def on_parse(self,*args):
        txt = self.link_input.text.strip()
        if not txt:
            self.result_text.text = "请输入AV/BV或者B站链接"
            return
        self.result_text.text = "正在解析视频..."
        threading.Thread(target=self._parse_work,args=(txt,),daemon=True).start()

    def _parse_work(self,text):
        try:
            from bili_api import new_session,get_video_info,get_play_info
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

            @mainthread
            def ok_ui():
                self.info = info
                self.pages = pages
                self.qual_list = q_list
                self.quality_sp.values = q_list
                if q_list:
                    self.quality_sp.text = q_list[0]
                    self.quality_sp.opacity =1
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
        self.result_text.text = "开始下载任务（对接downloader）"

# ========== 主应用：没有底部导航！只有一个Screen主页 ==========
class MainAppContainer(BoxLayout):
    def __init__(self,app_inst,**kwargs):
        super().__init__(**kwargs)
        self.app_inst = app_inst
        self.orientation = "vertical"
        self.sm = ScreenManager()
        self.home_scr = HomeScreen(self.app_inst,name="home")
        self.sm.add_widget(self.home_scr)
        self.add_widget(self.sm)

class BiliDlMainApp(App):
    def build(self):
        self.cookie_data = dict()
        return MainAppContainer(self)

    def save_login_cookie(self,cookie_dict):
        self.cookie_data = cookie_dict

    def get_cookie(self):
        return self.cookie_data.copy()

if __name__ == "__main__":
    BiliDlMainApp().run()
