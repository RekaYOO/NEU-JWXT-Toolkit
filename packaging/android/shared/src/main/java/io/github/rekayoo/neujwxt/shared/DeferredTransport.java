package io.github.rekayoo.neujwxt.shared;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;

/** Load the page immediately, while API calls wait within their original deadlines. */
public final class DeferredTransport implements ApiTransport {
    private static final int MAX_PENDING = 128;
    private final Map<String, Pending> pending = new LinkedHashMap<>();
    private final ScheduledExecutorService deadlines = Executors.newSingleThreadScheduledExecutor();
    private ApiTransport delegate;
    private JSONObject terminalError;

    private static final class Pending {
        final NativeRequest request;
        final Callback callback;
        final long deadline;
        ScheduledFuture<?> timer;
        boolean dispatched;

        Pending(NativeRequest request, Callback callback) {
            this.request = request;
            this.callback = callback;
            deadline = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(request.timeoutMs);
        }
    }

    @Override
    public synchronized void request(String id, NativeRequest request, Callback callback) {
        if (terminalError != null) {
            callback.complete(terminalError);
            return;
        }
        if (pending.size() >= MAX_PENDING || pending.containsKey(id)) {
            callback.complete(error("等待本地服务的请求过多", "ERR_NETWORK"));
            return;
        }
        Pending entry = new Pending(request, callback);
        pending.put(id, entry);
        entry.timer = deadlines.schedule(
            () -> expire(id, entry), request.timeoutMs, TimeUnit.MILLISECONDS);
        if (delegate != null) dispatch(id, entry);
    }

    public synchronized void ready(ApiTransport transport) {
        if (terminalError != null || delegate != null) {
            transport.close();
            return;
        }
        delegate = transport;
        for (Map.Entry<String, Pending> row : new ArrayList<>(pending.entrySet())) {
            dispatch(row.getKey(), row.getValue());
        }
    }

    private void dispatch(String id, Pending entry) {
        if (pending.get(id) != entry || entry.dispatched) return;
        long remaining = TimeUnit.NANOSECONDS.toMillis(entry.deadline - System.nanoTime());
        if (remaining <= 0) {
            expire(id, entry);
            return;
        }
        entry.dispatched = true;
        try {
            delegate.request(id, entry.request.withTimeout(remaining), payload -> complete(id, entry, payload));
        } catch (RuntimeException exception) {
            complete(id, entry, error("本地请求无法发送", "ERR_NETWORK"));
        }
    }

    private synchronized void complete(String id, Pending entry, JSONObject payload) {
        if (pending.get(id) != entry) return;
        pending.remove(id);
        entry.timer.cancel(false);
        entry.callback.complete(payload);
    }

    private synchronized void expire(String id, Pending entry) {
        if (pending.get(id) != entry) return;
        // Remove before cancel: a synchronous transport callback must not win the timeout race.
        pending.remove(id);
        entry.timer.cancel(false);
        if (entry.dispatched) delegate.cancel(id);
        entry.callback.complete(error("请求超时", "ECONNABORTED"));
    }

    @Override
    public synchronized void cancel(String id) {
        Pending entry = pending.remove(id);
        if (entry == null) return;
        entry.timer.cancel(false);
        if (entry.dispatched) delegate.cancel(id);
        entry.callback.complete(error("请求已取消", "ERR_CANCELED"));
    }

    public synchronized void fail(String message) {
        terminate(error(message, "ERR_NETWORK"));
    }

    @Override
    public synchronized void close() {
        terminate(error("请求会话已关闭", "ERR_CANCELED"));
    }

    private void terminate(JSONObject error) {
        if (terminalError != null) return;
        terminalError = error;
        ArrayList<Pending> entries = new ArrayList<>(pending.values());
        pending.clear();
        for (Pending entry : entries) entry.timer.cancel(false);
        deadlines.shutdownNow();
        if (delegate != null) delegate.close();
        for (Pending entry : entries) entry.callback.complete(error);
    }

    private static JSONObject error(String message, String code) {
        try {
            return new JSONObject().put("status", 0).put("error", message).put("code", code);
        } catch (org.json.JSONException impossible) {
            throw new IllegalStateException(impossible);
        }
    }
}
