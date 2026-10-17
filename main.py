# -*- coding: utf-8 -*-
"""bilibilidl: native Android-oriented Bilibili downloader UI."""
import os, io, json, time, threading, traceback
from pathlib import Path
import requests, qrcode
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
from bili_api import new_session, extract_video_id, get_video_info, get_play_info, get_nav, format_duration, BiliAPIError
from login_manager import qr_generate, qr_poll, validate_login
from downloader import download_video, DownloadError

APP='bilibilidl'
# Bundle a Simplified Chinese font so the UI does not depend on the device's fallback font.
FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts')
CJK_FONT = os.path.join(FONT_DIR, 'NotoSansCJKsc-Regular.ttf')
if os.path.isfile(CJK_FONT):
    LabelBase.register(name='BiliPinkCJK', fn_regular=CJK_FONT)
else:
    CJK_FONT = 'Roboto'
APP_FONT = 'BiliPinkCJK' if CJK_FONT != 'Roboto' else 'Roboto'
APP_FONT_BOLD = APP_FONT
PINK=(0.94,.29,.52,1); PALE=(1,.94,.97,1); DARK=(.14,.13,.15,1); MUTED=(.48,.47,.5,1); WHITE=(1,1,1,1); BLUE=(.35,.58,.78,1)

def ui(fn): Clock.schedule_once(lambda _dt: fn(), 0)

class RoundButton(Button):
    def __init__(self, bg=PINK, fg=WHITE, **kw):
        kw.setdefault('font_name', APP_FONT_BOLD); super().__init__(**kw); self.background_normal=''; self.background_down=''; self.background_color=(0,0,0,0); self.color=fg; self.bold=True; self._bg=bg
        with self.canvas.before:
            Color(*self._bg); self._shape=RoundedRectangle(pos=self.pos,size=self.size,radius=[dp(18)])
        self.bind(pos=self._sync,size=self._sync)
    def _sync(self,*_): self._shape.pos=self.pos; self._shape.size=self.size

class AppRoot(BoxLayout):
    def __init__(self, app, **kw):
        super().__init__(orientation='vertical',padding=[dp(16),dp(12),dp(16),dp(8)],spacing=dp(8),**kw)
        self.app=app; self.info=None; self.play=None; self.qualities=[]; self.busy=False; self.cancel=threading.Event(); self.login_cancel=threading.Event(); self.login_popup=None
        self.account=self._load_json('account.json',None); self.history=self._load_json('history.json',[]); self.current_page='home'; self._build(); self.show_page('home')

    def _label(self,text,size=14,color=DARK,height=None,bold=False,halign='left'):
        w=Label(text=str(text),font_name=APP_FONT,font_size=dp(size),color=color,bold=bold,halign=halign,valign='middle',size_hint_y=None if height else 1,height=dp(height) if height else None)
        w.bind(size=lambda obj,val:setattr(obj,'text_size',(max(1,val[0]),None)))
        return w
    def _load_json(self,name,default):
        try:
            with open(os.path.join(self.app.user_data_dir,name),'r',encoding='utf-8') as f: return json.load(f)
        except Exception: return default
    def _save_json(self,name,data):
        os.makedirs(self.app.user_data_dir,exist_ok=True); path=os.path.join(self.app.user_data_dir,name); tmp=path+'.tmp'
        with open(tmp,'w',encoding='utf-8') as f: json.dump(data,f,ensure_ascii=False,indent=2)
        os.replace(tmp,path)
    def _build(self):
        head=BoxLayout(size_hint_y=None,height=dp(44),spacing=dp(8)); head.add_widget(self._label('bilibilidl',22,DARK,bold=True)); head.add_widget(Label(font_name=APP_FONT))
        self.login_btn=RoundButton(text=self.account.get('uname','已登录') if self.account else 'B站授权登录',size_hint_x=None,width=dp(130),bg=PALE,fg=PINK); self.login_btn.bind(on_release=lambda *_:self.show_login()); head.add_widget(self.login_btn); self.add_widget(head)
        self.body=BoxLayout(orientation='vertical',spacing=dp(8)); self.add_widget(self.body)
        nav=BoxLayout(size_hint_y=None,height=dp(54),spacing=dp(8))
        for text,page in [('主页','home'),('已下载','history'),('我的','mine')]:
            b=RoundButton(text=text,bg=PALE,fg=PINK); b.bind(on_release=lambda _,p=page:self.show_page(p)); nav.add_widget(b)
        self.add_widget(nav)

    def show_page(self,page):
        self.current_page=page; self.body.clear_widgets()
        if page=='home': self._home_page()
        elif page=='history': self._history_page()
        else: self._mine_page()

    def _home_page(self):
        sc=ScrollView(); col=BoxLayout(orientation='vertical',spacing=dp(10),size_hint_y=None); col.bind(minimum_height=col.setter('height'))
        col.add_widget(self._label('B站视频下载',20,DARK,34,True)); col.add_widget(self._label('支持 BV / AV / 视频链接 / b23.tv 短链接',12,MUTED,26))
        self.url_input=TextInput(hint_text='粘贴 BV 号、AV 号、B站视频链接或 b23.tv 短链接',multiline=True,size_hint_y=None,height=dp(84),padding=dp(12),font_name=APP_FONT,font_size=dp(15),foreground_color=DARK,background_normal='',background_active='',background_color=PALE,cursor_color=PINK)
        col.add_widget(self.url_input)
        row=BoxLayout(size_hint_y=None,height=dp(48),spacing=dp(8)); self.parse_btn=RoundButton(text='解析视频'); self.parse_btn.bind(on_release=lambda *_:self.parse_async()); row.add_widget(self.parse_btn)
        self.cover=Image(size_hint_x=None,width=dp(80),allow_stretch=True,keep_ratio=True); row.add_widget(self.cover); col.add_widget(row)
        self.title_lbl=self._label('解析后显示标题和视频信息',15,DARK,48,True); col.add_widget(self.title_lbl)
        self.meta_lbl=self._label('画质会按接口返回的实际可用列表显示。',12,MUTED,56); col.add_widget(self.meta_lbl)
        col.add_widget(self._label('清晰度',13,MUTED,24)); self.quality_spinner=Spinner(text='请先解析视频',values=(),font_name=APP_FONT,size_hint_y=None,height=dp(46),background_normal='',background_color=PALE,color=DARK); col.add_widget(self.quality_spinner)
        col.add_widget(self._label('下载内容',13,MUTED,24)); self.mode_spinner=Spinner(text='视频 + 音频（MP4）',font_name=APP_FONT,values=('视频 + 音频（MP4）','封面图片','弹幕 XML','字幕 SRT'),size_hint_y=None,height=dp(46),background_normal='',background_color=PALE,color=DARK); col.add_widget(self.mode_spinner)
        self.dl_btn=RoundButton(text='开始下载',size_hint_y=None,height=dp(52)); self.dl_btn.bind(on_release=lambda *_:self.start_download()); col.add_widget(self.dl_btn)
        self.progress=ProgressBar(max=100,value=0,size_hint_y=None,height=dp(8)); col.add_widget(self.progress)
        self.status=self._label('下载文件先保存在应用私有目录，可在“已下载”中导出。',12,MUTED,52); col.add_widget(self.status)
        self.log_lbl=self._label('',11,MUTED,68); col.add_widget(self.log_lbl)
        sc.add_widget(col); self.body.add_widget(sc)

    def parse_async(self):
        if self.busy:return
        text=self.url_input.text.strip()
        if not text:self._alert('请先粘贴 BV / AV 号或 B 站链接');return
        self.busy=True; self.parse_btn.disabled=True; self.status.text='正在请求 B 站视频信息…'
        threading.Thread(target=self._parse_worker,args=(text,),daemon=True).start()
    def _parse_worker(self,text):
        try:
            cookie=self.account.get('cookie','') if self.account else ''; session=new_session(cookie)
            info=get_video_info(session,text); play=get_play_info(session,info,cookie)
            data=play['data']; accepted=data.get('accept_quality') or []; descriptions=data.get('accept_description') or []
            qlist=[]
            logged_in=bool(self.account and self.account.get('cookie'))
            for q,d in zip(accepted,descriptions):
                q=int(q)
                # 80=1080P; all higher tiers require a Bilibili login in this app.
                if q >= 80 and not logged_in:
                    continue
                qlist.append((q,str(d)))
            if not qlist:
                for item in (data.get('dash') or {}).get('video',[]):
                    q=int(item.get('id',0))
                    if q >= 80 and not logged_in:
                        continue
                    qlist.append((q,str(q)+'P'))
            if not qlist and data.get('durl'): qlist=[(0,'自动 / 当前可用画质')]
            self.info=info; self.play=play; self.qualities=qlist
            ui(lambda:self._parse_done(info,qlist))
        except Exception as e: ui(lambda:self._alert('解析失败：'+str(e)))
        finally:
            self.busy=False; ui(lambda:setattr(self.parse_btn,'disabled',False) if hasattr(self,'parse_btn') else None)
    def _parse_done(self,info,qlist):
        self.title_lbl.text=info['title']; self.meta_lbl.text='UP主：%s  |  时长：%s\n播放：%s  弹幕：%s  点赞：%s\n%s'%(info['owner'],format_duration(info['duration']),info['view'],info['danmaku'],info['like'],info['description'][:120])
        labels=[('%s（%s）'%(desc,q)) for q,desc in qlist]
        self.quality_spinner.values=labels; self.quality_spinner.text=labels[0] if labels else '无可用画质'
        self.status.text=('解析成功：%d 个当前可用画质。'%len(qlist)) + (' 未登录仅显示 720P 及以下；登录后可查看 1080P 及以上。' if not (self.account and self.account.get('cookie')) else ' 登录状态已启用。'); self.progress.value=0
        if info.get('pic'):
            threading.Thread(target=self._load_cover,args=(info['pic'],),daemon=True).start()
    def _load_cover(self,url):
        try:
            r=requests.get(url,timeout=15); r.raise_for_status(); tex=CoreImage(io.BytesIO(r.content),ext='png' if r.headers.get('content-type','').endswith('png') else 'jpg').texture
            ui(lambda:setattr(self.cover,'texture',tex) if hasattr(self,'cover') else None)
        except Exception: pass

    def _downloads_dir(self):
        path=os.path.join(self.app.user_data_dir,'downloads'); os.makedirs(path,exist_ok=True); return path
    def start_download(self):
        if self.busy:return
        if not self.info:
            self.parse_async(); self._alert('请先解析视频，确认信息和可用画质后再下载。'); return
        mode=self.mode_spinner.text
        if mode in ('封面图片','弹幕 XML','字幕 SRT'):
            threading.Thread(target=self._download_attachment,args=(mode,),daemon=True).start(); return
        try: selected=self.quality_spinner.text; quality=next((q for q,d in self.qualities if ('%s（%s）'%(d,q))==selected),0)
        except Exception: quality=0
        if quality >= 80 and not (self.account and self.account.get('cookie')):
            self._alert('1080P 及以上画质需要先点击右上角“登录”，使用 B 站授权扫码登录。')
            self.show_login()
            return
        self.busy=True; self.cancel.clear(); self.dl_btn.disabled=True; self.parse_btn.disabled=True; self.progress.value=0
        threading.Thread(target=self._download_worker,args=(quality,mode),daemon=True).start()
    def _download_attachment(self,mode):
        try:
            if not self.info: raise RuntimeError('请先解析视频')
            session=new_session(self.account.get('cookie','') if self.account else '')
            stem=(self.info.get('bvid') or 'av'+str(self.info.get('aid') or 'video'))
            if mode=='封面图片':
                url=self.info.get('pic')
                if not url: raise RuntimeError('没有封面地址')
                name='cover_'+stem+'.jpg'; response=session.get(url,timeout=25); response.raise_for_status(); content=response.content; mime='image/jpeg'
            elif mode=='弹幕 XML':
                url='https://comment.bilibili.com/%s.xml' % self.info.get('cid')
                response=session.get(url,timeout=25); response.raise_for_status(); content=response.content
                if not content or b'<i' not in content[:500]: raise RuntimeError('B站未返回有效弹幕 XML')
                name=stem+'_danmaku.xml'; mime='application/xml'
            else:
                params={'cid':self.info.get('cid')}
                if self.info.get('bvid'): params['bvid']=self.info['bvid']
                response=session.get('https://api.bilibili.com/x/player/v2',params=params,timeout=25); response.raise_for_status(); payload=response.json()
                data=payload.get('data') or {}; subs=((data.get('subtitle') or {}).get('subtitles') or [])
                if not subs: raise RuntimeError('该视频没有公开字幕')
                sub=subs[0]; suburl=sub.get('subtitle_url') or ''
                if suburl.startswith('//'): suburl='https:'+suburl
                sr=session.get(suburl,timeout=25); sr.raise_for_status(); subdata=sr.json()
                def stamp(value):
                    value=max(0,float(value or 0)); h=int(value//3600); m=int((value%3600)//60); sec=int(value%60); ms=int((value-int(value))*1000)
                    return '%02d:%02d:%02d,%03d' % (h,m,sec,ms)
                lines=[]
                for i,item in enumerate(subdata.get('body') or [],1):
                    lines.extend([str(i),stamp(item.get('from'))+' --> '+stamp(item.get('to')),str(item.get('content','')), ''])
                if not lines: raise RuntimeError('字幕内容为空')
                content=('\ufeff'+'\n'.join(lines)).encode('utf-8'); name=stem+'_subtitle.srt'; mime='application/x-subrip'
            path=os.path.join(self._downloads_dir(),name)
            with open(path,'wb') as f:f.write(content)
            self._add_history(name,path,mime)
            ui(lambda:setattr(self.status,'text',mode+'下载完成：'+name))
        except Exception as e:
            ui(lambda:self._alert(mode+'下载失败：'+str(e)))

    def _download_cover(self):
        return self._download_attachment('封面图片')
    def _download_worker(self,quality,mode):
        try:
            out=self._downloads_dir(); cookie=self.account.get('cookie','') if self.account else ''
            def prog(done,total):
                pct=int(done*100/total) if total else 0
                ui(lambda:setattr(self.progress,'value',pct)); ui(lambda:setattr(self.status,'text','下载中：%s / %s MB'%(round(done/1048576,1),round(total/1048576,1) if total else '?')))
            def log(line): ui(lambda:setattr(self.log_lbl,'text',line[-250:]))
            path=download_video(self.info,cookie,quality,out,progress=prog,cancel=self.cancel,log=log)
            saved_location=''
            if platform == 'android':
                from jnius import autoclass
                PythonActivity=autoclass('org.kivy.android.PythonActivity')
                Helper=autoclass('org.bilibilidl.MediaMerger')
                exported=Helper.exportToDownloads(PythonActivity.mActivity,path,os.path.basename(path))
                if not str(exported).startswith('content://'):
                    raise RuntimeError('视频已下载到应用内部，但保存到手机 Download 失败：'+str(exported))
                saved_location='手机 Download/bilibilidl'
            else:
                saved_location=path
            self._add_history(os.path.basename(path),path,'video/mp4')
            ui(lambda:setattr(self.status,'text','下载完成，已保存到 %s：%s'%(saved_location,os.path.basename(path)))); ui(lambda:setattr(self.progress,'value',100))
        except Exception as e: ui(lambda:self._alert('下载失败：'+str(e))); ui(lambda:setattr(self.status,'text','下载失败。查看提示并重新尝试。'))
        finally:
            self.busy=False; ui(lambda:self._enable())
    def _enable(self):
        if hasattr(self,'dl_btn'): self.dl_btn.disabled=False
        if hasattr(self,'parse_btn'): self.parse_btn.disabled=False
    def _add_history(self,name,path,mime):
        self.history=[h for h in self.history if h.get('path')!=path]
        self.history.insert(0,{'name':name,'path':path,'mime':mime,'created':int(time.time()),'title':(self.info or {}).get('title',name)})
        self.history=self.history[:200]; self._save_json('history.json',self.history)
    def _history_page(self):
        sc=ScrollView(); col=BoxLayout(orientation='vertical',spacing=dp(8),size_hint_y=None); col.bind(minimum_height=col.setter('height'))
        col.add_widget(self._label('已下载',20,DARK,38,True)); col.add_widget(self._label('视频下载完成后会自动保存到手机 Download/bilibilidl，无需申请存储权限。应用内保留副本用于历史记录。',12,MUTED,48))
        if not self.history: col.add_widget(self._label('还没有下载记录。',14,MUTED,60))
        for item in self.history:
            card=BoxLayout(orientation='vertical',padding=dp(10),spacing=dp(5),size_hint_y=None,height=dp(112))
            card.add_widget(self._label(item.get('name','文件'),14,DARK,28,True)); card.add_widget(self._label(item.get('title',''),11,MUTED,24))
            row=BoxLayout(size_hint_y=None,height=dp(38),spacing=dp(8)); exp=RoundButton(text='导出到手机',bg=PINK); exp.bind(on_release=lambda _,it=item:self.export_file(it)); delete=RoundButton(text='删除记录',bg=PALE,fg=PINK); delete.bind(on_release=lambda _,it=item:self.delete_history(it)); row.add_widget(exp); row.add_widget(delete); card.add_widget(row); col.add_widget(card)
        sc.add_widget(col); self.body.add_widget(sc)
    def export_file(self,item):
        path=item.get('path','')
        if not os.path.isfile(path): self._alert('找不到文件，可能已被系统清理。'); return
        try:
            if platform=='android':
                from jnius import autoclass
                PythonActivity=autoclass('org.kivy.android.PythonActivity'); Helper=autoclass('org.bilibilidl.MediaMerger')
                result=Helper.exportToDownloads(PythonActivity.mActivity,path,item.get('name',os.path.basename(path)))
                if not result.startswith('content://'): raise RuntimeError(result)
            else:
                import shutil
                target=os.path.join(str(Path.home()/'Downloads'),item.get('name',os.path.basename(path))); shutil.copy2(path,target); result=target
            self._alert('导出成功：\n'+result)
        except Exception as e:self._alert('导出失败：'+str(e))
    def delete_history(self,item):
        self.history=[h for h in self.history if h.get('path')!=item.get('path')]; self._save_json('history.json',self.history); self.show_page('history')
    def _mine_page(self):
        self.body.add_widget(self._label('我的',22,DARK,44,True))
        self.body.add_widget(self._label('bilibilidl · 仅在本机处理登录信息',13,MUTED,32))
        if self.account:
            self.body.add_widget(self._label('账号：%s\nUID：%s\nVIP 类型：%s'%(self.account.get('uname','B站用户'),self.account.get('mid','未知'),self.account.get('vipType',0)),15,DARK,80))
            b=RoundButton(text='退出登录并清除 Cookie',bg=PALE,fg=PINK,size_hint_y=None,height=dp(48)); b.bind(on_release=lambda *_:self.logout()); self.body.add_widget(b)
        else:
            b=RoundButton(text='B站扫码登录',size_hint_y=None,height=dp(48)); b.bind(on_release=lambda *_:self.show_login()); self.body.add_widget(b)
        self.body.add_widget(self._label('说明：画质由 B 站接口实际返回结果决定；登录不代表拥有大会员专属权益。',12,MUTED,60))

    def _make_qr_texture(self,url):
        qr=qrcode.QRCode(box_size=8,border=3); qr.add_data(url); qr.make(fit=True); img=qr.make_image(fill_color='black',back_color='white').convert('RGB'); buf=io.BytesIO(); img.save(buf,format='PNG'); buf.seek(0); return CoreImage(buf,ext='png').texture
    def show_login(self):
        if self.account:
            self._mine_page_confirm(); return
        self.login_cancel.set(); self.login_cancel=threading.Event(); cancel=self.login_cancel
        content=BoxLayout(orientation='vertical',padding=dp(14),spacing=dp(8)); content.add_widget(self._label('使用哔哩哔哩 App 扫描二维码并确认登录。',13,MUTED,48,halign='center'))
        self.qr_image=Image(size_hint=(1,1),allow_stretch=True,keep_ratio=True); content.add_widget(self.qr_image)
        self.qr_status=self._label('正在获取二维码…',13,PINK,32,halign='center'); content.add_widget(self.qr_status)
        row=BoxLayout(size_hint_y=None,height=dp(44),spacing=dp(8)); refresh=RoundButton(text='刷新二维码',bg=PALE,fg=PINK); close=RoundButton(text='取消',bg=(.9,.89,.91,1),fg=DARK); row.add_widget(refresh);row.add_widget(close);content.add_widget(row)
        pop=Popup(title='B站扫码登录',content=content,size_hint=(.9,.78),auto_dismiss=False);self.login_popup=pop
        refresh.bind(on_release=lambda *_:self._refresh_qr());close.bind(on_release=lambda *_:self._close_login());pop.open();threading.Thread(target=self._qr_worker,args=(cancel,),daemon=True).start()
    def _refresh_qr(self):
        self.login_cancel.set(); self.login_cancel=threading.Event(); self.qr_status.text='正在刷新二维码…'; threading.Thread(target=self._qr_worker,args=(self.login_cancel,),daemon=True).start()
    def _qr_worker(self,cancel):
        session=new_session()
        try:
            url,key=qr_generate(session); texture=self._make_qr_texture(url); ui(lambda:setattr(self.qr_image,'texture',texture) if self.login_popup else None); ui(lambda:setattr(self.qr_status,'text','请使用 B 站 App 扫码'))
            end=time.time()+175
            while time.time()<end and not cancel.is_set():
                code,msg=qr_poll(session,key)
                if code==0:
                    cookie,profile=validate_login(session); self._save_account(cookie,profile); ui(lambda:self._login_done(profile)); return
                if code==86038: ui(lambda:setattr(self.qr_status,'text','二维码已过期，请刷新')); return
                ui(lambda:setattr(self.qr_status,'text','已扫码，请在手机上确认' if code==86090 else '等待扫码…'))
                for _ in range(20):
                    if cancel.is_set():return
                    time.sleep(.1)
            if not cancel.is_set():ui(lambda:setattr(self.qr_status,'text','二维码超时，请刷新'))
        except Exception as e: ui(lambda:setattr(self.qr_status,'text','登录失败：'+str(e)[:90]) if self.login_popup else None)
    def _save_account(self,cookie,profile):
        self.account={'cookie':cookie,'uname':profile.get('uname','B站用户'),'mid':profile.get('mid',0),'vipType':profile.get('vipType',0),'face':profile.get('face',''),'saved_at':int(time.time())};self._save_json('account.json',self.account)
    def _login_done(self,profile):
        if self.login_popup:self.login_popup.dismiss();self.login_popup=None
        self.login_btn.text=profile.get('uname','已登录');self._alert('登录成功：'+profile.get('uname','B站用户'))
        if self.current_page=='mine':self.show_page('mine')
    def _close_login(self): self.login_cancel.set(); self.login_popup.dismiss(); self.login_popup=None
    def logout(self):
        self.login_cancel.set();self.account=None
        try:os.remove(os.path.join(self.app.user_data_dir,'account.json'))
        except OSError:pass
        self.login_btn.text='B站授权登录';self.show_page('mine');self._alert('已退出登录并清除本地 Cookie。')
    def _mine_page_confirm(self):
        self.show_page('mine')
    def _alert(self,text):
        def show(): Popup(title='提示',content=Label(text=str(text),font_name=APP_FONT,color=DARK,halign='center',valign='middle'),size_hint=(.88,.36)).open()
        ui(show)

class BiliBilidlApp(App):
    title='bilibilidl'
    def build(self): Window.clearcolor=WHITE; return AppRoot(self)
if __name__=='__main__': BiliBilidlApp().run()
