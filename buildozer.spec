[app]

title = bilibilidl
package.name = bilibilidl
package.domain = org.bilibilidl

source.dir = .
source.include_exts = py,png,jpg,jpeg,ttf,ttc,otf,kv,atlas,json,xml,java,txt,md
source.include_patterns = *.png,*.jpg,*.jpeg,*.ttf,*.ttc,*.otf,*.kv,*.atlas,src/*,assets/*
source.exclude_dirs = bin,.buildozer,__pycache__,.git

version = 2.5.3

orientation = portrait
fullscreen = 0

icon.filename = %(source.dir)s/bilibilidl图标.png
presplash.filename = %(source.dir)s/bilibilidl加载图.png
# 启动画面背景改为纯白；应用启动后 main.py 的 style_system_bars() 负责状态栏/导航栏白底深色图标
android.presplash_color = #FFFFFF

# Python 和界面依赖（p4a 不会自动装依赖的依赖，所以这里全部列出）
requirements = python3,kivy==2.2.1,pillow,pyjnius,certifi,requests,urllib3,idna,charset-normalizer,qrcode,pypng,typing_extensions

# Android 兼容设置
android.api = 33
android.minapi = 24
android.ndk = 25c
android.archs = arm64-v8a
android.accept_sdk_license = True

# Android 源码目录（Java 文件放在 src/org/bilibilidl/MediaMerger.java）
android.add_src = src

# 权限
android.permissions = INTERNET,ACCESS_NETWORK_STATE,WRITE_EXTERNAL_STORAGE

# 调试设置
android.debuggable = True
android.logcat_filters = *:S python:D

[buildozer]

log_level = 2
warn_on_root = 1
