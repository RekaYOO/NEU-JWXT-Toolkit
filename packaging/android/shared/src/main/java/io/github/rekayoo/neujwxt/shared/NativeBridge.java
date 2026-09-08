package io.github.rekayoo.neujwxt.shared;

import android.webkit.JavascriptInterface;
import android.webkit.WebView;

import org.json.JSONObject;

import java.util.UUID;
import java.util.function.Supplier;

public final class NativeBridge {
    private final BaseShellActivity activity;
    private final WebView webView;
    private final Supplier<ApiTransport> transport;
    private final Supplier<JSONObject> shellInfo;

    public NativeBridge(BaseShellActivity activity, WebView webView,
                        Supplier<ApiTransport> transport, Supplier<JSONObject> shellInfo) {
        this.activity = activity;
        this.webView = webView;
        this.transport = transport;
        this.shellInfo = shellInfo;
    }

    @JavascriptInterface
    public String getShellInfo() {
        return shellInfo.get().toString();
    }

    @JavascriptInterface
    public String request(String rawRequest) {
        String id = UUID.randomUUID().toString();
        try {
            NativeRequest request = NativeRequest.parse(rawRequest);
            transport.get().request(id, request, payload -> deliver(id, payload));
        } catch (Exception exception) {
            deliver(id, error("原生请求参数无效", "ERR_INVALID_REQUEST"));
        }
        return id;
    }

    @JavascriptInterface
    public void cancel(String id) {
        transport.get().cancel(id);
    }

    @JavascriptInterface
    public String saveFile(String filename, String mediaType, String base64) {
        try {
            NativeFileRegistry files = activity.nativeFiles();
            if ("@begin".equals(base64)) return files.beginUpload();
            if (base64 != null && base64.startsWith("@chunk:")) {
                if (base64.length() > 90000) throw new IllegalArgumentException("Oversized chunk");
                files.appendUpload(filename, android.util.Base64.decode(
                    base64.substring(7), android.util.Base64.DEFAULT));
                return "ok";
            }
            if (base64 != null && base64.startsWith("@abort:")) {
                files.discard(base64.substring(7));
                return "ok";
            }
            if (base64 != null && base64.startsWith("@native:")) {
                files.finishUpload(base64.substring(8));
            }
        } catch (Exception exception) {
            activity.showError("文件准备失败，请重试");
            return "";
        }
        activity.runOnUiThread(() -> activity.saveFile(filename, mediaType, base64));
        return "ok";
    }

    @JavascriptInterface
    public void openServerSettings() {
        activity.runOnUiThread(activity::openServerSettings);
    }

    private void deliver(String id, JSONObject payload) {
        String script = "window.__neuNativeDeliver(" + JSONObject.quote(id) + "," + payload + ");";
        webView.post(() -> {
            if (!activity.isDestroyed() && !activity.isFinishing()) {
                webView.evaluateJavascript(script, null);
            }
        });
    }

    private static JSONObject error(String message, String code) {
        try {
            return new JSONObject().put("status", 0).put("error", message).put("code", code);
        } catch (Exception impossible) {
            return new JSONObject();
        }
    }
}
