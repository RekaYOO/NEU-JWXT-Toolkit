package io.github.rekayoo.neujwxt.client;

import android.content.Context;
import android.content.SharedPreferences;

import androidx.security.crypto.EncryptedSharedPreferences;
import androidx.security.crypto.MasterKey;

import okhttp3.HttpUrl;
import java.net.URI;

final class ServerConfigStore {
    private static final String SERVER_URL = "server_url";
    private final SharedPreferences preferences;

    ServerConfigStore(Context context) {
        try {
            MasterKey key = new MasterKey.Builder(context)
                .setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build();
            preferences = EncryptedSharedPreferences.create(
                context,
                "client_secrets",
                key,
                EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
            );
        } catch (Exception exception) {
            throw new IllegalStateException("无法打开加密配置存储", exception);
        }
    }

    String getServerUrl() {
        return preferences.getString(SERVER_URL, "");
    }

    void setServerUrl(String value) {
        preferences.edit().putString(SERVER_URL, value).apply();
    }

    static HttpUrl validate(String value) {
        if (value == null || value.trim().isEmpty()) throw new IllegalArgumentException("请输入服务端地址");
        final URI uri;
        try {
            uri = URI.create(value.trim());
        } catch (IllegalArgumentException exception) {
            throw new IllegalArgumentException("服务端地址无效");
        }
        if (!"https".equalsIgnoreCase(uri.getScheme())) throw new IllegalArgumentException("服务端必须使用 HTTPS");
        if (uri.getHost() == null || uri.getHost().trim().isEmpty()) throw new IllegalArgumentException("服务端主机无效");
        if (uri.getRawUserInfo() != null || uri.getRawQuery() != null || uri.getRawFragment() != null) {
            throw new IllegalArgumentException("地址不能包含账号、查询参数或片段");
        }
        if (!uri.getRawPath().isEmpty() && !uri.getRawPath().equals("/")) {
            throw new IllegalArgumentException("服务端地址不能包含子路径");
        }
        HttpUrl url = HttpUrl.parse(value.trim());
        if (url == null) throw new IllegalArgumentException("服务端地址无效");
        return url.newBuilder().encodedPath("/").query(null).fragment(null).build();
    }
}
