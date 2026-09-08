package io.github.rekayoo.neujwxt.shared;

import org.json.JSONObject;
import org.junit.Test;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.Assert.*;

public class DeferredTransportTest {
    private static NativeRequest input(long timeout) throws Exception {
        return NativeRequest.parse(new JSONObject().put("method", "POST")
            .put("path", "/api/auth/login").put("body", "{\"fixture\":true}")
            .put("timeout_ms", timeout).toString());
    }

    private static final class RecordingTransport implements ApiTransport {
        int sent;
        int canceled;
        boolean closed;
        NativeRequest request;
        Callback callback;
        @Override public void request(String id, NativeRequest value, Callback completion) {
            sent++;
            request = value;
            callback = completion;
        }
        @Override public void cancel(String id) { canceled++; }
        @Override public void close() { closed = true; }
    }

    @Test public void queuesOnceAndPreservesRequestWithRemainingDeadline() throws Exception {
        DeferredTransport deferred = new DeferredTransport();
        RecordingTransport actual = new RecordingTransport();
        AtomicInteger completed = new AtomicInteger();
        try {
            NativeRequest request = input(30000);
            deferred.request("login", request, payload -> completed.incrementAndGet());
            assertEquals(0, actual.sent);
            Thread.sleep(20);
            deferred.ready(actual);
            assertEquals(1, actual.sent);
            assertEquals(request.path, actual.request.path);
            assertEquals(request.body, actual.request.body);
            assertEquals(request.method, actual.request.method);
            assertSame(request.headers, actual.request.headers);
            assertTrue(actual.request.timeoutMs < request.timeoutMs);
            actual.callback.complete(new JSONObject());
            actual.callback.complete(new JSONObject());
            assertEquals(1, completed.get());
        } finally { deferred.close(); }
    }

    @Test public void canceledQueuedMutationIsNeverSentWhenBackendStarts() throws Exception {
        DeferredTransport deferred = new DeferredTransport();
        RecordingTransport actual = new RecordingTransport();
        AtomicReference<String> code = new AtomicReference<>();
        try {
            deferred.request("login", input(30000), payload -> code.set(payload.optString("code")));
            deferred.cancel("login");
            deferred.ready(actual);
            assertEquals(0, actual.sent);
            assertEquals("ERR_CANCELED", code.get());
        } finally { deferred.close(); }
    }

    @Test public void timeoutIncludesStartupAndNeverReplaysExpiredMutation() throws Exception {
        DeferredTransport deferred = new DeferredTransport();
        RecordingTransport actual = new RecordingTransport();
        CountDownLatch complete = new CountDownLatch(1);
        AtomicReference<String> code = new AtomicReference<>();
        try {
            deferred.request("login", input(1000), payload -> {
                code.set(payload.optString("code"));
                complete.countDown();
            });
            assertTrue(complete.await(3, TimeUnit.SECONDS));
            deferred.ready(actual);
            assertEquals("ECONNABORTED", code.get());
            assertEquals(0, actual.sent);
        } finally { deferred.close(); }
    }

    @Test public void failureCompletesQueuedAndFutureRequestsWithoutDispatch() throws Exception {
        DeferredTransport deferred = new DeferredTransport();
        RecordingTransport actual = new RecordingTransport();
        AtomicInteger completed = new AtomicInteger();
        ApiTransport.Callback callback = payload -> {
            assertEquals("ERR_NETWORK", payload.optString("code"));
            completed.incrementAndGet();
        };
        try {
            deferred.request("before", input(30000), callback);
            deferred.fail("Backend unavailable");
            deferred.request("after", input(30000), callback);
            deferred.ready(actual);
            assertEquals(2, completed.get());
            assertEquals(0, actual.sent);
            assertTrue(actual.closed);
        } finally { deferred.close(); }
    }

    @Test public void closingActivityCancelsQueuedAndDispatchedCallsOnlyOnce() throws Exception {
        DeferredTransport deferred = new DeferredTransport();
        RecordingTransport actual = new RecordingTransport();
        AtomicInteger completed = new AtomicInteger();
        deferred.request("login", input(30000), payload -> {
            assertEquals("ERR_CANCELED", payload.optString("code"));
            completed.incrementAndGet();
        });
        deferred.ready(actual);
        deferred.close();
        actual.callback.complete(new JSONObject());
        deferred.close();
        assertTrue(actual.closed);
        assertEquals(1, completed.get());
    }

    @Test public void cancelAfterDispatchCannotDeliverLateSuccess() throws Exception {
        DeferredTransport deferred = new DeferredTransport();
        RecordingTransport actual = new RecordingTransport();
        AtomicInteger completed = new AtomicInteger();
        try {
            deferred.ready(actual);
            deferred.request("login", input(30000), payload -> {
                assertEquals("ERR_CANCELED", payload.optString("code"));
                completed.incrementAndGet();
            });
            deferred.cancel("login");
            actual.callback.complete(new JSONObject());
            assertEquals(1, actual.sent);
            assertEquals(1, actual.canceled);
            assertEquals(1, completed.get());
        } finally { deferred.close(); }
    }

    @Test public void dispatchedTimeoutCancelsTransportAndIgnoresLateResponse() throws Exception {
        DeferredTransport deferred = new DeferredTransport();
        RecordingTransport actual = new RecordingTransport();
        CountDownLatch done = new CountDownLatch(1);
        AtomicInteger completed = new AtomicInteger();
        try {
            deferred.ready(actual);
            deferred.request("login", input(1000), payload -> {
                assertEquals("ECONNABORTED", payload.optString("code"));
                completed.incrementAndGet();
                done.countDown();
            });
            assertTrue(done.await(3, TimeUnit.SECONDS));
            actual.callback.complete(new JSONObject());
            assertEquals(1, actual.canceled);
            assertEquals(1, completed.get());
        } finally { deferred.close(); }
    }

    @Test public void closedQueueCannotDispatchAfterActivityRecreation() throws Exception {
        DeferredTransport deferred = new DeferredTransport();
        RecordingTransport actual = new RecordingTransport();
        AtomicInteger completed = new AtomicInteger();
        deferred.request("login", input(30000), payload -> completed.incrementAndGet());
        deferred.close();
        deferred.ready(actual);
        assertTrue(actual.closed);
        assertEquals(0, actual.sent);
        assertEquals(1, completed.get());
    }
}
