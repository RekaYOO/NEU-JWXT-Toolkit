package io.github.rekayoo.neujwxt.client;

import android.app.AlertDialog;
import android.content.SharedPreferences;
import android.text.InputType;
import android.view.ViewGroup;
import android.widget.EditText;
import android.widget.LinearLayout;

import androidx.security.crypto.EncryptedSharedPreferences;
import androidx.security.crypto.MasterKey;

import org.json.JSONObject;

import java.io.IOException;
import java.util.Collections;

import io.github.rekayoo.neujwxt.shared.ApiTransport;
import io.github.rekayoo.neujwxt.shared.BaseShellActivity;
import io.github.rekayoo.neujwxt.shared.OkHttpTransport;
import okhttp3.Call;
import okhttp3.Callback;
import okhttp3.HttpUrl;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;

public final class MainActivity extends BaseShellActivity {
    private static final int MOBILE_API_VERSION = 1;
    private ServerConfigStore config;
    private EncryptedCookieJar cookies;
    private OkHttpClient client;
    private SharedPreferences secretPreferences;
    private Runnable readyCallback;

    @Override
    protected void prepareShell(Runnable ready) {
        try {
            config = new ServerConfigStore(this);
            MasterKey key = new MasterKey.Builder(this).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build();
            secretPreferences = EncryptedSharedPreferences.create(
                this, "client_cookies", key,
                EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
            );
            cookies = new EncryptedCookieJar(secretPreferences);
            if (!config.getServerUrl().trim().isEmpty()) {
                cookies.setOrigin(ServerConfigStore.validate(config.getServerUrl()));
            }
        } catch (Exception exception) {
            showStartupError("无法打开加密 Cookie 存储");
            return;
        }
        client = new OkHttpClient.Builder().cookieJar(cookies)
            .followRedirects(false).followSslRedirects(false).retryOnConnectionFailure(false).build();
        readyCallback = ready;
        if (config.getServerUrl().trim().isEmpty()) showServerDialog(false);
        else ready.run();
    }

    @Override
    protected ApiTransport createTransport() {
        return new OkHttpTransport(
            client, ServerConfigStore.validate(config.getServerUrl()), Collections.emptyMap(), nativeFiles()
        );
    }

    @Override
    protected JSONObject createShellInfo() {
        try {
            return new JSONObject().put("kind", "client").put("server_url", config.getServerUrl());
        } catch (Exception impossible) {
            return new JSONObject();
        }
    }

    @Override
    public void openServerSettings() {
        showServerDialog(true);
    }

    private void showServerDialog(boolean cancelable) {
        EditText input = new EditText(this);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        input.setSingleLine(true);
        input.setText(config.getServerUrl());
        input.setHint("https://example.com");
        int padding = (int) (24 * getResources().getDisplayMetrics().density);
        LinearLayout container = new LinearLayout(this);
        container.setPadding(padding, 0, padding, 0);
        container.addView(input, new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        AlertDialog dialog = new AlertDialog.Builder(this)
            .setTitle("服务端设置")
            .setMessage("请输入使用系统可信证书的 HTTPS 根地址")
            .setView(container)
            .setCancelable(cancelable)
            .setNegativeButton(cancelable ? "取消" : null, null)
            .setNeutralButton(cancelable ? "清除登录" : null, null)
            .setPositiveButton("检测并保存", null)
            .create();
        dialog.setOnShowListener(ignored -> {
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(view -> {
                String candidate = input.getText().toString().trim();
                probe(candidate, () -> {
                    boolean firstConfiguration = config.getServerUrl().trim().isEmpty();
                    String normalized = ServerConfigStore.validate(candidate).toString();
                    resetCookies(ServerConfigStore.validate(normalized), false);
                    config.setServerUrl(normalized);
                    dialog.dismiss();
                    if (firstConfiguration && readyCallback != null) readyCallback.run();
                    else clearSessionAndReload();
                }, false);
            });
            if (cancelable) dialog.getButton(AlertDialog.BUTTON_NEUTRAL).setOnClickListener(view -> {
                resetCookies(ServerConfigStore.validate(config.getServerUrl()), true);
                android.webkit.CookieManager.getInstance().removeAllCookies(null);
                clearSessionAndReload();
                showError("登录 Cookie 已清除");
            });
        });
        dialog.show();
    }

    private void resetCookies(HttpUrl root, boolean clear) {
        // In-flight responses retain the old jar and cannot restore a cleared session.
        if (clear) cookies.clear();
        else cookies.invalidate();
        client.dispatcher().cancelAll();
        cookies = new EncryptedCookieJar(secretPreferences);
        cookies.setOrigin(root);
        client = client.newBuilder().cookieJar(cookies).build();
    }

    private void probe(String value, Runnable success, boolean offerSettingsOnFailure) {
        final HttpUrl root;
        try {
            root = ServerConfigStore.validate(value);
        } catch (IllegalArgumentException exception) {
            showError(exception.getMessage());
            return;
        }
        Request request = new Request.Builder().url(root.resolve("api/health")).get().build();
        client.newBuilder().cookieJar(okhttp3.CookieJar.NO_COOKIES)
            .callTimeout(15, java.util.concurrent.TimeUnit.SECONDS).build()
            .newCall(request).enqueue(new Callback() {
            @Override
            public void onFailure(Call call, IOException exception) {
                runOnUiThread(() -> {
                    showError("无法连接服务端，请检查地址和 HTTPS 证书");
                    if (offerSettingsOnFailure) showServerDialog(false);
                });
            }

            @Override
            public void onResponse(Call call, Response response) {
                try (response) {
                    JSONObject health = new JSONObject(response.body() == null ? "{}" : response.body().string());
                    if (!response.isSuccessful() || health.optInt("mobile_api_version", 0) != MOBILE_API_VERSION) {
                        throw new IllegalStateException("服务端移动 API 版本不兼容");
                    }
                    runOnUiThread(() -> {
                        if (!isDestroyed() && !isFinishing()) success.run();
                    });
                } catch (Exception exception) {
                    runOnUiThread(() -> {
                        showError(exception.getMessage());
                        if (offerSettingsOnFailure) showServerDialog(false);
                    });
                }
            }
        });
    }

    @Override
    protected void onDestroy() {
        if (cookies != null) cookies.invalidate();
        if (client != null) client.dispatcher().cancelAll();
        super.onDestroy();
    }
}
