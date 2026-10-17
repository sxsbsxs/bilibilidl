# bilibilidl — B 站 Android 下载器源码

这是一个面向 Android 的 Kivy 项目，采用白色底色和粉色点缀。源码按模块拆分，包含 B 站视频信息解析、扫码登录、真实画质列表、下载任务、下载历史、封面下载、Android DASH 音视频合并辅助类、导出到系统 Downloads，以及 GitHub Actions APK 构建工作流。

> **重要状态说明：** 当前代码已经通过 Python 语法检查，但尚未在真实 Android 设备完成端到端构建、扫码登录、画质解析、下载和合并测试。B 站网页接口可能变化；不同视频/账号返回的媒体结构也不同。因此不能把这份源码描述成已经实测可用的成品 APK。首次构建需要在 GitHub Actions 中验证，如果 Java helper、Buildozer 或 B 站接口有兼容问题，需要根据构建日志继续修复。

## 文件结构

```text
bilibilidl/
├── main.py                         # Kivy UI、解析/下载交互、我的、已下载、扫码登录
├── bili_api.py                     # BV/AV/短链接识别、视频信息、播放信息、账号接口
├── login_manager.py                # 二维码生成/轮询、登录态验证
├── downloader.py                   # 媒体流下载、断点续传临时文件、DASH 音视频合并调用
├── src/org/bilibilidl/MediaMerger.java # Android MediaMuxer 合并与 MediaStore 导出
├── buildozer.spec                  # Android 打包依赖和权限
├── .github/workflows/build-apk.yml # GitHub Actions 构建 APK
└── tests/                           # 测试目录
```

## 目前包含的功能

- BV、AV、B 站视频 URL、常见 b23.tv 短链接识别。
- 通过 B 站网页 API 读取标题、UP 主、时长、播放量、弹幕、点赞、投币、收藏、简介和封面。
- 支持 BV 号、AV 号、B 站视频链接和 b23.tv 短链接解析。
- 请求播放信息接口，根据返回的 `accept_quality` / `accept_description` 构建真实画质列表；未登录时隐藏 1080P 及以上画质，右上角使用“B站授权登录”扫码登录后再显示账号实际可用画质。
- B 站二维码登录：生成二维码、轮询扫码状态、验证登录态、显示 UID/昵称、退出登录。
- 通过 HTTP 流下载，临时 `.part` 文件支持在再次启动下载时尝试 Range 续传。
- 直接下载封面、弹幕 XML；若视频提供公开字幕，则尝试下载并转换为 SRT。
- DASH 视频/音频轨道下载后调用 Android `MediaExtractor` / `MediaMuxer` 合并为 MP4。
- 视频下载完成后自动保存到手机 `Download/bilibilidl`，Android 10 及以上通过 MediaStore 写入，不申请存储权限；应用私有目录保留一份供历史记录使用。
- GitHub Actions 自动构建 APK 并上传 Artifact；构建日志也会作为 Artifact 上传。

## 构建方法（可在手机上操作）

1. 将 ZIP 中 `bilibilidl` 目录里的全部文件上传到 GitHub 仓库根目录。不要把账号 Cookie 上传到仓库。
2. 打开 GitHub 仓库的 **Actions**。
3. 选择 **Build bilibilidl APK**，点击 **Run workflow**，或推送到 `main` 分支触发构建。
4. 等待工作流完成，在本次运行页面底部的 **Artifacts** 下载 `bilibilidl-debug-apk`，解压获得 APK。
5. 若构建失败，下载 `bilibilidl-build-log`，按日志定位问题；仅通过语法检查不代表 Android 构建已成功。

## 重要技术限制

- B 站二维码登录接口是网页端接口，不保证长期稳定。扫码登录只保存本机应用私有目录中的 Cookie；当前还没有 Android Keystore 加密，建议在个人设备使用，分享设备时退出登录。
- 登录不等于获得大会员权限；接口返回的画质会受视频本身、账号权限、地区、风控和接口策略影响。
- B 站 DASH 资源通常是分开的音视频轨道。项目使用 Android MediaMuxer 尝试合并，但不同编码、容器或媒体分段可能导致合并失败；此时会显示错误，不应宣称所有视频必定成功。
- “仅视频轨道”选项当前明确提示暂未开放；“视频 + 音频（MP4）”和封面下载是主流程。分 P 选择、收藏夹/UP 主批量下载、任务暂停按钮、后台下载通知尚未实现。字幕和弹幕接口是否有数据取决于视频与 B 站接口返回。
- 下载过程中会先写入应用私有目录；完成后自动复制到手机 `Download/bilibilidl`。Android 10 及以上无需申请存储权限。Android 9 及以下系统的公共下载目录行为不同，不能保证完全无权限写入。
- 不会在 GitHub Actions 中下载所谓的 `bdl-android-aarch64`。上游 BDL 项目目前以桌面端为主，不能假设存在可用的 Android ARM64 二进制或兼容的 CLI 参数；本项目使用自身的 Python 下载逻辑。

## 许可与合规

本项目是独立实现，不是 BVD 的 Kotlin 源码直接移植。BVD 参考仓库：<https://github.com/KafuuNeko/BiliDownload>。B 站与本项目无隶属或授权关系。仅下载你有权保存的内容，并遵守平台条款和适用法律。

### 中文字体与编码

项目已将 **Noto Sans CJK SC（简体中文）** 字体打包到 `fonts/NotoSansCJKsc-Regular.ttf`，并在 `main.py` 注册为应用字体，应用标签、按钮、输入框、清晰度选择器和提示弹窗均显式使用该字体。`buildozer.spec` 已将 `.ttf` 纳入 APK。Python 源文件和 JSON 数据统一使用 UTF-8；写入 JSON 时保留中文字符。这样可减少依赖手机系统字体造成的方框/缺字问题。

字体文件较大（约 16 MB），APK 体积会相应增加。仍需在真实 Android 设备上验证字体渲染与 APK 打包结果。
