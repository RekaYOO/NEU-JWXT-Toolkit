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
    private final AppUpdateManager updates;

    public NativeBridge(BaseShellActivity activity, WebView webView,
                        Supplier<ApiTransport> transport, Supplier<JSONObject> shellInfo) {
        this.activity = activity;
        this.webView = webView;
        this.transport = transport;
        this.shellInfo = shellInfo;
        this.updates = new AppUpdateManager(activity);
    }

    @JavascriptInterface
    public String getShellInfo() {
        try {
            JSONObject value = new JSONObject(shellInfo.get().toString());
            android.content.pm.PackageInfo info = activity.getPackageManager().getPackageInfo(activity.getPackageName(), 0);
            value.put("application_id", activity.getPackageName())
                .put("version", info.versionName)
                .put("desktop_companion", true)
                .put("version_code", android.os.Build.VERSION.SDK_INT >= 28 ? info.getLongVersionCode() : info.versionCode)
                .put("update_supported", activity.getPackageName().equals("io.github.rekayoo.neujwxt.client")
                    || activity.getPackageName().equals("io.github.rekayoo.neujwxt.local"));
            return value.toString();
        } catch (Exception ignored) {
            return shellInfo.get().toString();
        }
    }

    @JavascriptInterface
    public String request(String rawRequest) {
        String id = UUID.randomUUID().toString();
        try {
            NativeRequest request = NativeRequest.parse(rawRequest);
            long epoch = Companion.prefs(activity).getLong("epoch", 0);
            transport.get().request(id, request, payload -> {
                int status = payload.optInt("status");
                if (status >= 200 && status < 300) observeCompanion(request, payload, epoch);
                deliver(id, payload);
            });
        } catch (Exception exception) {
            deliver(id, error("原生请求参数无效", "ERR_INVALID_REQUEST"));
        }
        return id;
    }

    private void observeCompanion(NativeRequest request, JSONObject payload, long epoch) {
        synchronized (Companion.STATE) {
            if (epoch != Companion.prefs(activity).getLong("epoch", 0)) return;
            if (request.path.equals("/api/logout") && request.method.equals("POST")) {
                Companion.clearSession(activity);
            } else if (request.path.equals("/api/status") || request.path.equals("/api/client/bootstrap")) {
                try {
                    JSONObject data = new JSONObject(payload.optString("body"));
                    if (request.path.equals("/api/client/bootstrap")) data = data.optJSONObject("auth");
                    if (data != null && data.optBoolean("is_logged_in") && !data.isNull("current_user")) {
                        String account = data.optString("current_user").trim();
                        if (!account.isEmpty()) Companion.onAuthenticated(activity, account);
                    }
                } catch (org.json.JSONException ignored) { }
            } else if (request.path.startsWith("/api/timetable/personal?")) {
                Companion.schedule(activity, true);
            }
        }
    }

    @JavascriptInterface
    public void cancel(String id) {
        transport.get().cancel(id);
    }

    @JavascriptInterface
    public void commitCredentialAutofill(int requestId) {
        activity.runOnUiThread(() -> {
            if (activity.isFinishing() || activity.isDestroyed()) return;
            try {
                if (android.os.Build.VERSION.SDK_INT >= 26) {
                    android.view.autofill.AutofillManager manager = activity.getSystemService(android.view.autofill.AutofillManager.class);
                    if (manager != null && manager.isEnabled()) manager.commit();
                }
            } catch (RuntimeException ignored) {
            } finally {
                webView.evaluateJavascript("window.__neuAutofillCommitted?.(" + requestId + ")", null);
            }
        });
    }

    @JavascriptInterface
    public void openCompanionSettings() {
        activity.runOnUiThread(() -> {
            if (!activity.isFinishing() && !activity.isDestroyed()) {
                Companion.showSettings(activity, "client".equals(shellInfo.get().optString("kind")));
            }
        });
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

    @JavascriptInterface
    public String checkAppUpdate() {
        return runUpdate(false);
    }

    @JavascriptInterface
    public String installAppUpdate() {
        return runUpdate(true);
    }

    private String runUpdate(boolean install) {
        String id = UUID.randomUUID().toString();
        new Thread(() -> {
            JSONObject payload;
            try { payload = install ? updates.downloadAndInstall() : updates.check(); }
            catch (Exception exception) {
                payload = error(exception.getMessage() == null ? "更新检查失败" : exception.getMessage(), "ERR_UPDATE");
            }
            deliverUpdate(id, payload);
        }, "neu-app-update").start();
        return id;
    }

    private void deliver(String id, JSONObject payload) {
        String script = "window.__neuNativeDeliver(" + JSONObject.quote(id) + "," + payload + ");";
        webView.post(() -> {
            if (!activity.isDestroyed() && !activity.isFinishing()) {
                webView.evaluateJavascript(script, null);
            }
        });
    }

    private void deliverUpdate(String id, JSONObject payload) {
        String script = "window.__neuNativeUpdateDeliver(" + JSONObject.quote(id) + "," + payload + ");";
        webView.post(() -> {
            if (!activity.isDestroyed() && !activity.isFinishing()) webView.evaluateJavascript(script, null);
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
