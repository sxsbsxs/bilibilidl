[app]

title = bilibilidl
package.name = bilibilidl
package.domain = org.bilibilidl

source.dir = .
source.include_exts = py,png,jpg,jpeg,ttf,ttc,otf,kv,atlas,json,xml,java,txt,md
source.include_patterns = *.png,*.jpg,*.jpeg,*.ttf,*.ttc,*.otf,*.kv,*.atlas,src/*,assets/*

version = 2.5.2

orientation = portrait
fullscreen = 0

icon.filename = %(source.dir)s/bilibilidl图标.png
presplash.filename = %(source.dir)s/bilibilidl加载图.png

# Python 和界面依赖
requirements = python3,kivy==2.2.1,kivymd==1.1.1,pillow,pyjnius,certifi,requests,qrcode

# Python-for-Android
p4a.python_version = 3.10

# Android 兼容设置
android.api = 33
android.minapi = 24
android.ndk = 25c
android.archs = arm64-v8a

# Android 源码目录
android.add_src = src

# 权限
android.permissions = INTERNET,ACCESS_NETWORK_STATE

# 调试设置
android.debuggable = True
android.logcat_filters = *:S python:D

[buildozer]

log_level = 2
warn_on_root = 1
