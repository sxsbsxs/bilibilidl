bilibilidl HTML UI integration files

Included:
- main.py (Python bridge/API plus existing Kivy fallback UI)
- index.html (HTML UI that calls the API actions currently present in main.py)
- buildozer.spec (includes html/css/js assets and src Java)
- src/org/bilibilidl/WebAppBridge.java

Important:
This is an integration bundle, not a complete standalone source archive. It relies on the existing repository modules and assets, including bili_api.py, downloader.py, login_manager.py, fonts/images, and the existing src/org/bilibilidl/InAppPlayer.java and MediaMerger.java. Keep those existing files in the repository; do not delete them.

HTML interface actions currently wired to main.py:
ping, account, parse, start_download, task_status, cancel_download, history.
The existing bridge API does not yet implement login/logout, in-app preview, cover export, danmaku export, or subtitle export through HTML. Those are not claimed as complete here. Python syntax and HTML JavaScript syntax were checked; this package has not been built/tested as an Android APK.

Install the files at the matching repository paths, then build via GitHub Actions. Keep a copy of the current files before replacing them.
