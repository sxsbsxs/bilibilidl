[app]

title = bilibilidl
package.name = bilibilidl
package.domain = org.bilibilidl

source.dir = .
source.include_exts = py,png,jpg,jpeg,ttf,ttc,otf,kv,atlas,json,xml,java,txt,md
source.include_patterns = *.png,*.jpg,*.jpeg,*.ttf,*.ttc,*.otf,*.kv,*.atlas,src/*,assets/*
source.exclude_dirs = bin,.buildozer,__pycache__,.git

version = 2.7.0

orientation = portrait
fullscreen = 0

icon.filename = %(source.dir)s/bilibilidl图标.png
presplash.filename = %(source.dir)s/bilibilidl加载图.png
android.presplash_color = #FFFFFF

requirements = python3,kivy==2.2.1,pillow,pyjnius,certifi,requests,urllib3,idna,charset-normalizer,qrcode,pypng,typing_extensions

android.api = 33
android.minapi = 24
android.ndk = 25c
android.archs = arm64-v8a
android.accept_sdk_license = True
android.enable_androidx = True

android.add_src = src

# 应用内播放用系统 WebView 打开官方播放器，无需 ExoPlayer / ffpyplayer
android.permissions = INTERNET,ACCESS_NETWORK_STATE,WRITE_EXTERNAL_STORAGE

android.debuggable = True
android.logcat_filters = *:S python:D

[buildozer]

log_level = 2
warn_on_root = 1
