[app]

# 应用信息
title = bilibilidl
package.name = bilibilidl
package.domain = org.bilibilidl

# 项目根目录
source.dir = .

# 需要打包的文件类型
source.include_exts = py,png,jpg,jpeg,ttf,ttc,otf,kv,atlas,json,xml,java,txt,md

# 根目录图片和字体，以及 Java 源码
source.include_patterns = *.png,*.jpg,*.jpeg,*.ttf,*.ttc,*.otf,*.kv,*.atlas,src/*,assets/*

version = 2.5.2
orientation = portrait
fullscreen = 0

# 应用图标：使用仓库中的实际文件名
icon.filename = %(source.dir)s/bilibilidl图标.png

# 自定义启动图：使用仓库中的实际文件名
presplash.filename = %(source.dir)s/bilibilidl加载图.png

# Python 与 UI 依赖
requirements = python3,kivy==2.2.1,kivymd==1.1.1,pillow,pyjnius,certifi
p4a.python_version = 3.10

# Android 构建设置
android.api = 33
android.minapi = 24
android.ndk = 25c
android.archs = arm64-v8a

# Java 源码目录
android.add_src = src

# 网络权限
android.permissions = INTERNET,ACCESS_NETWORK_STATE

# 调试构建
android.debuggable = True

[buildozer]
log_level = 2
warn_on_root = 1
