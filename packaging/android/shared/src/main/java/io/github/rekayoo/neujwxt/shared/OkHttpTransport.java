package io.github.rekayoo.neujwxt.shared;

import android.util.Base64;

import org.json.JSONObject;

import java.io.IOException;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.TimeUnit;

import okhttp3.Call;
import okhttp3.Headers;
import okhttp3.HttpUrl;
import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;

public class OkHttpTransport implements ApiTransport {
    private final OkHttpClient client;
    private final HttpUrl baseUrl;
    private final Map<String, String> fixedHeaders;
    private final NativeFileRegistry nativeFiles;
    private final ConcurrentHashMap<String, Call> calls = new ConcurrentHashMap<>();
    private volatile boolean closed;

    public OkHttpTransport(OkHttpClient client, HttpUrl baseUrl, Map<String, String> fixedHeaders,
                           NativeFileRegistry nativeFiles) {
        this.client = client.newBuilder().followRedirects(false).followSslRedirects(false)
            .retryOnConnectionFailure(true).build();
        this.baseUrl = baseUrl;
        this.fixedHeaders = fixedHeaders;
        this.nativeFiles = nativeFiles;
    }

    @Override
    public void request(String id, NativeRequest input, ApiTransport.Callback callback) {
        if (closed) {
            callback.complete(error("请求会话已关闭", "ERR_CANCELED"));
            return;
        }
        HttpUrl url = baseUrl.resolve(input.path.substring(1));
        if (url == null || !url.host().equals(baseUrl.host())
            || url.port() != baseUrl.port() || !url.scheme().equals(baseUrl.scheme())
            || !url.encodedPath().startsWith("/api/")) {
            callback.complete(error("请求地址无效", "ERR_INVALID_URL"));
            return;
        }
        Request.Builder builder = new Request.Builder().url(url);
        input.headers.forEach(builder::header);
        fixedHeaders.forEach(builder::header);
        RequestBody body = null;
        if (!input.method.equals("GET") && !input.method.equals("HEAD")) {
            String contentType = input.headers.entrySet().stream()
                .filter(row -> row.getKey().equalsIgnoreCase("content-type"))
                .map(Map.Entry::getValue).findFirst().orElse("application/json; charset=utf-8");
            RequestBody content = RequestBody.create(input.body, MediaType.parse(contentType));
            // Recover connections only before sending; never replay a dispatched
            // mutation, including HTTP 408/503 follow-ups or a lost response.
            body = new RequestBody() {
                @Override public MediaType contentType() { return content.contentType(); }
                @Override public long contentLength() throws IOException { return content.contentLength(); }
                @Override public void writeTo(okio.BufferedSink sink) throws IOException { content.writeTo(sink); }
                @Override public boolean isOneShot() { return true; }
            };
        }
        builder.method(input.method, body);
        // Axios owns the deadline. Inherited 10-second socket timeouts otherwise
        // discard slow backend results before its 30-second request can finish.
        OkHttpClient timedClient = client.newBuilder()
            .connectTimeout(input.timeoutMs, TimeUnit.MILLISECONDS)
            .readTimeout(input.timeoutMs, TimeUnit.MILLISECONDS)
            .writeTimeout(input.timeoutMs, TimeUnit.MILLISECONDS)
            .callTimeout(input.timeoutMs, TimeUnit.MILLISECONDS).build();
        Call call = timedClient.newCall(builder.build());
        calls.put(id, call);
        if (closed) call.cancel();
        call.enqueue(new okhttp3.Callback() {
            @Override
            public void onFailure(Call ignored, IOException exception) {
                calls.remove(id);
                boolean timedOut = exception instanceof java.io.InterruptedIOException;
                callback.complete(error(timedOut ? "请求超时" : call.isCanceled() ? "请求已取消" : "网络请求失败",
                    timedOut ? "ECONNABORTED" : call.isCanceled() ? "ERR_CANCELED" : "ERR_NETWORK"));
            }

            @Override
            public void onResponse(Call ignored, Response response) {
                try (response) {
                    JSONObject headers = new JSONObject();
                    Headers responseHeaders = response.headers();
                    for (String name : responseHeaders.names()) {
                        if (!name.equalsIgnoreCase("set-cookie") && !name.equalsIgnoreCase("set-cookie2")) {
                            headers.put(name.toLowerCase(java.util.Locale.ROOT), responseHeaders.get(name));
                        }
                    }
                    JSONObject payload = new JSONObject()
                        .put("status", response.code())
                        .put("headers", headers);
                    boolean streamToFile = response.isSuccessful()
                        && input.responseType.equals("blob")
                        && nativeFiles != null
                        && input.download;
                    if (streamToFile) {
                        String token = nativeFiles.store(response.body().byteStream());
                        payload.put("native_file_token", token).put("body_size", nativeFiles.size(token));
                    } else {
                        byte[] bytes = response.body() == null ? new byte[0] : readBounded(response.body().byteStream());
                        if (input.responseType.equals("blob") || input.responseType.equals("arraybuffer")) {
                        payload.put("body_base64", Base64.encodeToString(bytes, Base64.NO_WRAP));
                        } else {
                            payload.put("body", new String(bytes, java.nio.charset.StandardCharsets.UTF_8));
                        }
                    }
                    callback.complete(payload);
                } catch (Exception exception) {
                    boolean timedOut = exception instanceof java.io.InterruptedIOException;
                    callback.complete(error(timedOut ? "请求超时" : "响应读取失败",
                        timedOut ? "ECONNABORTED" : call.isCanceled() ? "ERR_CANCELED" : "ERR_BAD_RESPONSE"));
                } finally {
                    calls.remove(id);
                }
            }
        });
    }

    @Override
    public void cancel(String id) {
        Call call = calls.remove(id);
        if (call != null) call.cancel();
    }

    @Override
    public void close() {
        closed = true;
        for (Call call : calls.values()) call.cancel();
        calls.clear();
    }

    private static byte[] readBounded(java.io.InputStream input) throws IOException {
        java.io.ByteArrayOutputStream output = new java.io.ByteArrayOutputStream();
        byte[] buffer = new byte[32768];
        int count;
        while ((count = input.read(buffer)) != -1) {
            if (output.size() + count > 32 * 1024 * 1024) {
                throw new IOException("Response exceeds the in-memory bridge limit");
            }
            output.write(buffer, 0, count);
        }
        return output.toByteArray();
    }

    private static JSONObject error(String message, String code) {
        try {
            return new JSONObject().put("status", 0).put("error", message).put("code", code);
        } catch (Exception impossible) {
            return new JSONObject();
        }
    }
}
