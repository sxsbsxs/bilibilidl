package org.bilibilidl;

import android.app.Dialog;
import android.content.Context;
import android.graphics.Color;
import android.graphics.drawable.ColorDrawable;
import android.view.ViewGroup;
import android.view.Window;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/**
 * WebView bridge for bilibilidl's local HTML UI.
 * JavaScript calls NativeBridge.call(JSON string); the request is forwarded to
 * the Python loopback API on 127.0.0.1. No remote backend is used.
 */
public final class WebAppBridge {
    private final String apiUrl;
    private static Dialog currentDialog;
    private static WebView currentWebView;

    public WebAppBridge(String apiUrl) {
        this.apiUrl = apiUrl == null ? "http://127.0.0.1:18765/api" : apiUrl;
    }

    @JavascriptInterface
    public String call(String jsonRequest) {
        HttpURLConnection connection = null;
        try {
            URL url = new URL(apiUrl);
            connection = (HttpURLConnection) url.openConnection();
            connection.setRequestMethod("POST");
            connection.setConnectTimeout(8000);
            connection.setReadTimeout(30000);
            connection.setDoOutput(true);
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            byte[] bytes = (jsonRequest == null ? "{}" : jsonRequest).getBytes(StandardCharsets.UTF_8);
            connection.setFixedLengthStreamingMode(bytes.length);
            try (OutputStream output = connection.getOutputStream()) {
                output.write(bytes);
            }
            int status = connection.getResponseCode();
            InputStream stream = status >= 200 && status < 400
                    ? connection.getInputStream() : connection.getErrorStream();
            if (stream == null) return "{\"ok\":false,\"error\":\"Python API 无响应\"}";
            StringBuilder result = new StringBuilder();
            try (BufferedReader reader = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8))) {
                String line;
                while ((line = reader.readLine()) != null) result.append(line);
            }
            String value = result.toString();
            return value.isEmpty() ? "{\"ok\":false,\"error\":\"空响应\"}" : value;
        } catch (Exception error) {
            try {
                JSONObject object = new JSONObject();
                object.put("ok", false);
                object.put("error", "桥接失败：" + error.getMessage());
                return object.toString();
            } catch (Exception ignored) {
                return "{\"ok\":false,\"error\":\"桥接失败\"}";
            }
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    /** Opens the bundled local HTML file in a modal WebView. */
    public static void open(Context context, String localHtmlUrl, String apiUrl) {
        if (context == null || localHtmlUrl == null || localHtmlUrl.isEmpty()) return;
        final Dialog dialog = new Dialog(context);
        dialog.requestWindowFeature(Window.FEATURE_NO_TITLE);
        WebView webView = new WebView(context);
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(true);
        settings.setAllowContentAccess(true);
        settings.setJavaScriptCanOpenWindowsAutomatically(false);
        settings.setSupportMultipleWindows(false);
        webView.setBackgroundColor(Color.WHITE);
        webView.setWebViewClient(new WebViewClient());
        webView.setWebChromeClient(new WebChromeClient());
        webView.addJavascriptInterface(new WebAppBridge(apiUrl), "NativeBridge");
        dialog.setContentView(webView);
        Window window = dialog.getWindow();
        if (window != null) {
            window.setBackgroundDrawable(new ColorDrawable(Color.WHITE));
            window.setLayout(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT);
        }
        dialog.setOnDismissListener(d -> {
            try { webView.removeJavascriptInterface("NativeBridge"); webView.stopLoading(); webView.destroy(); }
            catch (Exception ignored) {}
            if (currentDialog == dialog) { currentDialog = null; currentWebView = null; }
        });
        currentDialog = dialog;
        currentWebView = webView;
        dialog.show();
        if (window != null) window.setLayout(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT);
        webView.loadUrl(localHtmlUrl);
    }

    private WebAppBridge() { this.apiUrl = "http://127.0.0.1:18765/api"; }
}
