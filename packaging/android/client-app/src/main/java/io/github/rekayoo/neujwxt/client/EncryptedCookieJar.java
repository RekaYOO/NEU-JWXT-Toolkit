package io.github.rekayoo.neujwxt.client;

import android.content.SharedPreferences;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

import okhttp3.Cookie;
import okhttp3.CookieJar;
import okhttp3.HttpUrl;

final class EncryptedCookieJar implements CookieJar {
    private static final String KEY = "cookies";
    private final SharedPreferences preferences;
    private String origin;

    EncryptedCookieJar(SharedPreferences preferences) {
        this.preferences = preferences;
    }

    synchronized void setOrigin(HttpUrl url) {
        String next = originOf(url);
        if (!next.equals(preferences.getString("origin", ""))) {
            preferences.edit().remove(KEY).putString("origin", next).commit();
        }
        origin = next;
    }

    private static String originOf(HttpUrl url) {
        return url.scheme() + "://" + url.host() + ":" + url.port();
    }

    @Override
    public synchronized void saveFromResponse(HttpUrl url, List<Cookie> incoming) {
        if (!originOf(url).equals(origin)) return;
        List<Cookie> current = read(url);
        for (Cookie candidate : incoming) {
            current.removeIf(existing -> existing.name().equals(candidate.name())
                && existing.domain().equals(candidate.domain()) && existing.path().equals(candidate.path()));
            if (candidate.expiresAt() > System.currentTimeMillis()) current.add(candidate);
        }
        JSONArray values = new JSONArray();
        current.forEach(cookie -> values.put(cookie.toString()));
        JSONObject result = new JSONObject();
        try {
            result.put("host", url.host()).put("cookies", values);
        } catch (Exception impossible) {
            return;
        }
        preferences.edit().putString(KEY, result.toString()).apply();
    }

    @Override
    public synchronized List<Cookie> loadForRequest(HttpUrl url) {
        if (!originOf(url).equals(origin)) return Collections.emptyList();
        List<Cookie> all = read(url);
        List<Cookie> matching = new ArrayList<>();
        for (Cookie cookie : all) if (cookie.matches(url)) matching.add(cookie);
        return matching;
    }

    synchronized void clear() {
        origin = null;
        preferences.edit().remove(KEY).apply();
    }

    synchronized void invalidate() {
        origin = null;
    }

    private List<Cookie> read(HttpUrl url) {
        String raw = preferences.getString(KEY, "{}");
        try {
            JSONObject stored = new JSONObject(raw);
            if (!url.host().equalsIgnoreCase(stored.optString("host"))) return new ArrayList<>();
            JSONArray values = stored.optJSONArray("cookies");
            if (values == null) return new ArrayList<>();
            List<Cookie> result = new ArrayList<>();
            for (int index = 0; index < values.length(); index++) {
                Cookie cookie = Cookie.parse(url, values.getString(index));
                if (cookie != null && cookie.expiresAt() > System.currentTimeMillis()) result.add(cookie);
            }
            return result;
        } catch (Exception exception) {
            return new ArrayList<>();
        }
    }
}
