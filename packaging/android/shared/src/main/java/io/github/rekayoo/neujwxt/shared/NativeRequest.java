package io.github.rekayoo.neujwxt.shared;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;

public final class NativeRequest {
    public final String method;
    public final String path;
    public final Map<String, String> headers;
    public final String body;
    public final long timeoutMs;
    public final String responseType;
    public final boolean download;

    private NativeRequest(String method, String path, Map<String, String> headers,
                          String body, long timeoutMs, String responseType, boolean download) {
        this.method = method;
        this.path = path;
        this.headers = headers;
        this.body = body;
        this.timeoutMs = timeoutMs;
        this.responseType = responseType;
        this.download = download;
    }

    public NativeRequest withTimeout(long remainingMs) {
        return new NativeRequest(method, path, headers, body,
            Math.max(1, Math.min(timeoutMs, remainingMs)), responseType, download);
    }

    public static NativeRequest parse(String raw) throws JSONException {
        JSONObject value = new JSONObject(raw);
        String path = value.optString("path", "");
        if (!RequestPolicy.isApiPath(path)) {
            throw new JSONException("Only relative /api/ paths are allowed");
        }
        String method = value.optString("method", "GET").toUpperCase(Locale.ROOT);
        if (!method.matches("GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS")) {
            throw new JSONException("Unsupported HTTP method");
        }
        Map<String, String> headers = new LinkedHashMap<>();
        JSONObject sourceHeaders = value.optJSONObject("headers");
        if (sourceHeaders != null) {
            Iterator<String> names = sourceHeaders.keys();
            while (names.hasNext()) {
                String name = names.next();
                if (RequestPolicy.allowsHeader(name)) {
                    headers.put(name, sourceHeaders.optString(name, ""));
                }
            }
        }
        long timeout = Math.max(1000, Math.min(300000, value.optLong("timeout_ms", 30000)));
        return new NativeRequest(
            method, path, headers, value.optString("body", ""), timeout,
            value.optString("response_type", "json"), value.optBoolean("download", false)
        );
    }
}
