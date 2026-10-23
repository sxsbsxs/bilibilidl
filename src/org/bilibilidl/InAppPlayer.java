package org.bilibilidl;

import android.app.Dialog;
import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;

/**
 * 应用内 WebView 打开 B 站官方播放器。
 * 强制所有导航留在 WebView：拦截 bilibili://、intent://、market:// 等，不跳系统浏览器/B站App。
 */
public final class InAppPlayer {
    private static Dialog currentDialog;
    private static WebView currentWeb;

    private InAppPlayer() {}

    public static String playBvid(Context context, String bvid, int page, String cookie) {
        if (bvid == null || bvid.isEmpty()) {
            return "empty_bvid";
        }
        if (page < 1) page = 1;
        String url = "https://player.bilibili.com/player.html?bvid="
                + bvid + "&page=" + page + "&high_quality=1&danmaku=0";
        return openWebPlayer(context, url, cookie);
    }

    public static String playUrl(Context context, String url) {
        return openWebPlayer(context, url, null);
    }

    public static String playDash(Context context, String videoUrl, String audioUrl) {
        return "use_playBvid";
    }

    public static String playFile(Context context, String path) {
        return "use_playBvid";
    }

    private static boolean isAllowedHttpUrl(String url) {
        if (url == null || url.isEmpty()) return false;
        String u = url.toLowerCase();
        if (u.startsWith("about:") || u.startsWith("javascript:") || u.startsWith("data:")) {
            return true;
        }
        if (!(u.startsWith("http://") || u.startsWith("https://"))) {
            return false;
        }
        // 只允许 B 站相关域名，其它 http(s) 也拦在 WebView 外（直接忽略，不跳浏览器）
        try {
            Uri uri = Uri.parse(url);
            String host = uri.getHost();
            if (host == null) return false;
            host = host.toLowerCase();
            return host.equals("bilibili.com")
                    || host.endsWith(".bilibili.com")
                    || host.equals("b23.tv")
                    || host.endsWith(".b23.tv")
                    || host.equals("bilivideo.com")
                    || host.endsWith(".bilivideo.com")
                    || host.equals("bilivideo.cn")
                    || host.endsWith(".bilivideo.cn")
                    || host.equals("hdslb.com")
                    || host.endsWith(".hdslb.com")
                    || host.equals("akamaized.net")
                    || host.endsWith(".akamaized.net");
        } catch (Exception e) {
            return false;
        }
    }

    private static boolean shouldBlockScheme(String url) {
        if (url == null) return true;
        String u = url.toLowerCase();
        // App 深链 / 商店 / 意图：一律拦截，留在 WebView
        return u.startsWith("bilibili://")
                || u.startsWith("bilibili://")
                || u.startsWith("intent://")
                || u.startsWith("market://")
                || u.startsWith("https://play.google.com")
                || u.startsWith("http://play.google.com")
                || u.startsWith("tbopen://")
                || u.startsWith("openapp.")
                || u.startsWith("weixin://")
                || u.startsWith("mqqapi://")
                || u.startsWith("mqq://")
                || u.startsWith("tel:")
                || u.startsWith("mailto:")
                || u.startsWith("sms:");
    }

    private static String openWebPlayer(Context context, String url, String cookie) {
        try {
            dismiss();

            final WebView web = new WebView(context);
            WebSettings s = web.getSettings();
            s.setJavaScriptEnabled(true);
            s.setDomStorageEnabled(true);
            s.setDatabaseEnabled(true);
            s.setMediaPlaybackRequiresUserGesture(false);
            s.setLoadWithOverviewMode(true);
            s.setUseWideViewPort(true);
            s.setAllowFileAccess(false);
            s.setAllowContentAccess(false);
            s.setSupportMultipleWindows(false);
            s.setJavaScriptCanOpenWindowsAutomatically(false);
            if (Build.VERSION.SDK_INT >= 21) {
                s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
            }
            s.setUserAgentString(
                    "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
                            + "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36");

            if (cookie != null && !cookie.isEmpty()) {
                CookieManager cm = CookieManager.getInstance();
                cm.setAcceptCookie(true);
                if (Build.VERSION.SDK_INT >= 21) {
                    cm.setAcceptThirdPartyCookies(web, true);
                }
                String[] parts = cookie.split(";");
                for (String part : parts) {
                    String p = part.trim();
                    if (p.isEmpty() || !p.contains("=")) continue;
                    cm.setCookie("https://www.bilibili.com", p);
                    cm.setCookie("https://bilibili.com", p);
                    cm.setCookie("https://player.bilibili.com", p);
                    cm.setCookie(".bilibili.com", p);
                }
                if (Build.VERSION.SDK_INT >= 21) {
                    cm.flush();
                }
            }

            web.setWebViewClient(new WebViewClient() {
                private boolean handleUrl(WebView view, String target) {
                    if (target == null || target.isEmpty()) {
                        return true; // 吞掉
                    }
                    if (shouldBlockScheme(target)) {
                        // 深链：不跳 App，尝试把 bilibili://video/xxx 转成 https 播放页
                        try {
                            if (target.toLowerCase().startsWith("bilibili://")) {
                                // 常见：bilibili://video/BVxxx 或 video/数字
                                String rest = target.substring("bilibili://".length());
                                if (rest.startsWith("video/")) {
                                    String id = rest.substring("video/".length());
                                    int q = id.indexOf('?');
                                    if (q >= 0) id = id.substring(0, q);
                                    if (id.toUpperCase().startsWith("BV")) {
                                        view.loadUrl(
                                                "https://player.bilibili.com/player.html?bvid="
                                                        + id + "&high_quality=1&danmaku=0");
                                    } else if (id.matches("\\d+")) {
                                        view.loadUrl(
                                                "https://player.bilibili.com/player.html?aid="
                                                        + id + "&high_quality=1&danmaku=0");
                                    }
                                }
                            }
                        } catch (Exception ignored) {
                        }
                        return true; // 已处理，不交给系统
                    }
                    if (isAllowedHttpUrl(target)) {
                        // 允许的域名：留在 WebView 内加载
                        return false;
                    }
                    // 其它链接：不跳浏览器，直接忽略
                    return true;
                }

                @Override
                public boolean shouldOverrideUrlLoading(WebView view, String url) {
                    return handleUrl(view, url);
                }

                @Override
                public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                    if (request == null || request.getUrl() == null) {
                        return true;
                    }
                    return handleUrl(view, request.getUrl().toString());
                }

                @Override
                public void onPageStarted(WebView view, String url, Bitmap favicon) {
                    // 若被重定向到非允许域名，停掉
                    if (url != null && !isAllowedHttpUrl(url) && !url.startsWith("about:")) {
                        view.stopLoading();
                    }
                }
            });

            web.setWebChromeClient(new WebChromeClient() {
                // 禁止 window.open 弹出新窗口跳转
                @Override
                public boolean onCreateWindow(WebView view, boolean isDialog,
                                              boolean isUserGesture, android.os.Message resultMsg) {
                    return false;
                }
            });

            web.setBackgroundColor(Color.BLACK);
            web.loadUrl(url);

            final Dialog dialog = new Dialog(
                    context, android.R.style.Theme_Black_NoTitleBar_Fullscreen);
            FrameLayout root = new FrameLayout(context);
            root.setBackgroundColor(Color.BLACK);
            root.addView(web, new FrameLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.MATCH_PARENT));
            dialog.setContentView(root);
            dialog.setCancelable(true);
            dialog.setOnDismissListener(d -> {
                try {
                    web.stopLoading();
                    web.loadUrl("about:blank");
                    web.destroy();
                } catch (Exception ignored) {
                }
                currentWeb = null;
                currentDialog = null;
            });

            Window w = dialog.getWindow();
            if (w != null) {
                w.setLayout(ViewGroup.LayoutParams.MATCH_PARENT,
                        ViewGroup.LayoutParams.MATCH_PARENT);
                if (Build.VERSION.SDK_INT >= 21) {
                    w.addFlags(WindowManager.LayoutParams.FLAG_DRAWS_SYSTEM_BAR_BACKGROUNDS);
                    w.setStatusBarColor(Color.BLACK);
                    w.setNavigationBarColor(Color.BLACK);
                }
                w.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
            }

            currentWeb = web;
            currentDialog = dialog;
            dialog.show();
            return "ok";
        } catch (Exception e) {
            dismiss();
            return e.getClass().getSimpleName() + ": " + String.valueOf(e.getMessage());
        }
    }

    public static void dismiss() {
        Dialog d = currentDialog;
        currentDialog = null;
        WebView w = currentWeb;
        currentWeb = null;
        if (w != null) {
            try {
                w.stopLoading();
                w.loadUrl("about:blank");
                w.destroy();
            } catch (Exception ignored) {
            }
        }
        if (d != null) {
            try {
                d.dismiss();
            } catch (Exception ignored) {
            }
        }
    }
}
