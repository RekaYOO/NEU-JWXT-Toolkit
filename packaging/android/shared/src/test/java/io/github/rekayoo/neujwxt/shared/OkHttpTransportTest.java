package io.github.rekayoo.neujwxt.shared;

import java.util.Collections;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import okhttp3.OkHttpClient;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.tls.HandshakeCertificates;
import okhttp3.tls.HeldCertificate;
import org.json.JSONObject;
import org.junit.Test;
import static org.junit.Assert.*;

public class OkHttpTransportTest {
    @Test public void firstMutationDoesNotUseTheLoginPagesAgingConnection() throws Exception {
        for (String method : new String[] {"POST", "PUT", "PATCH", "DELETE"}) {
            try (MockWebServer server = new MockWebServer()) {
                server.enqueue(new MockResponse().setBody("login page ready"));
                server.enqueue(new MockResponse().setBody("{\"success\":true}"));
                AtomicReference<java.net.Socket> pageConnection = new AtomicReference<>();
                AtomicReference<java.net.Socket> activeConnection = new AtomicReference<>();
                OkHttpClient client = new OkHttpClient.Builder()
                    .eventListener(new okhttp3.EventListener() {
                        @Override public void connectionAcquired(okhttp3.Call call, okhttp3.Connection connection) {
                            activeConnection.set(connection.socket());
                            if (call.request().method().equals("GET")) {
                                pageConnection.set(connection.socket());
                            }
                        }
                        @Override public void requestHeadersEnd(okhttp3.Call call, okhttp3.Request request) {
                            if (!request.method().equals("GET") && activeConnection.get() == pageConnection.get()) {
                                // The old socket closes after the health check and
                                // headers, before the first credential body is sent.
                                try {
                                    activeConnection.get().close();
                                } catch (java.io.IOException error) {
                                    throw new AssertionError(error);
                                }
                            }
                        }
                    }).build();
                OkHttpTransport transport = new OkHttpTransport(client, server.url("/"),
                    Collections.emptyMap(), null);
                try {
                    assertEquals(200, request(transport, "{\"path\":\"/api/status\"}").getInt("status"));
                    assertNotNull(pageConnection.get());
                    JSONObject response = request(transport,
                        "{\"path\":\"/api/login\",\"method\":\"" + method + "\",\"body\":\"{}\"}");
                    assertEquals(method + ": " + response, 200, response.getInt("status"));
                    assertEquals(2, server.getRequestCount());
                    server.takeRequest();
                    assertEquals("Mutation must use a fresh connection", 0,
                        server.takeRequest().getSequenceNumber());
                } finally {
                    transport.close();
                    client.connectionPool().evictAll();
                }
            }
        }
    }

    @Test public void consecutiveMutationsUseFreshConnectionsButReadsStillPool() throws Exception {
        try (MockWebServer server = new MockWebServer()) {
            for (int index = 0; index < 4; index++) server.enqueue(new MockResponse().setBody("{}"));
            OkHttpClient client = new OkHttpClient();
            OkHttpTransport transport = new OkHttpTransport(client, server.url("/"),
                Collections.emptyMap(), null);
            try {
                assertEquals(200, request(transport, "{\"path\":\"/api/health\"}").getInt("status"));
                assertEquals(200, request(transport, "{\"path\":\"/api/health\"}").getInt("status"));
                assertEquals(200, request(transport,
                    "{\"path\":\"/api/login\",\"method\":\"POST\",\"body\":\"{}\"}").getInt("status"));
                assertEquals(200, request(transport,
                    "{\"path\":\"/api/webvpn/sms/verify\",\"method\":\"POST\",\"body\":\"{}\"}").getInt("status"));
                assertEquals(0, server.takeRequest().getSequenceNumber());
                assertEquals("Healthy reads should retain connection pooling", 1,
                    server.takeRequest().getSequenceNumber());
                assertEquals(0, server.takeRequest().getSequenceNumber());
                assertEquals(0, server.takeRequest().getSequenceNumber());
            } finally {
                transport.close();
                client.connectionPool().evictAll();
            }
        }
    }

    @Test public void freshMutationConnectionsKeepTlsCookiesAndFixedHeaders() throws Exception {
        HeldCertificate certificate = new HeldCertificate.Builder().addSubjectAlternativeName("localhost").build();
        HandshakeCertificates serverTls = new HandshakeCertificates.Builder().heldCertificate(certificate).build();
        HandshakeCertificates clientTls = new HandshakeCertificates.Builder()
            .addTrustedCertificate(certificate.certificate()).build();
        AtomicReference<java.util.List<okhttp3.Cookie>> cookies = new AtomicReference<>(Collections.emptyList());
        AtomicReference<java.io.IOException> readFailure = new AtomicReference<>();
        OkHttpClient client = new OkHttpClient.Builder()
            .sslSocketFactory(clientTls.sslSocketFactory(), clientTls.trustManager())
            .eventListener(new okhttp3.EventListener() {
                @Override public void responseFailed(okhttp3.Call call, java.io.IOException error) {
                    readFailure.set(error);
                }
            })
            .cookieJar(new okhttp3.CookieJar() {
                @Override public void saveFromResponse(okhttp3.HttpUrl url, java.util.List<okhttp3.Cookie> values) {
                    cookies.set(values);
                }
                @Override public java.util.List<okhttp3.Cookie> loadForRequest(okhttp3.HttpUrl url) {
                    return cookies.get();
                }
            }).build();
        try (MockWebServer server = new MockWebServer()) {
            server.useHttps(serverTls.sslSocketFactory(), false);
            server.enqueue(new MockResponse().addHeader("Set-Cookie", "access=synthetic; Secure; HttpOnly; Path=/")
                .setBody("{}"));
            for (int index = 0; index < 100; index++) {
                server.enqueue(new MockResponse().setBody("{\"success\":true}"));
            }
            OkHttpTransport transport = new OkHttpTransport(client,
                server.url("/").newBuilder().host("localhost").build(),
                Collections.singletonMap("X-NEU-Mobile-Token", "synthetic-native-token"), null);
            try {
                JSONObject access = request(transport, "{\"path\":\"/api/access/login\",\"method\":\"POST\"}");
                assertEquals(access.toString(), 200, access.getInt("status"));
                server.takeRequest();
                for (int index = 0; index < 100; index++) {
                    JSONObject login = request(transport, "{\"path\":\"/api/login\",\"method\":\"POST\",\"body\":\"{}\"}");
                    assertEquals(login + " body failure: " + readFailure.get(), 200, login.getInt("status"));
                    okhttp3.mockwebserver.RecordedRequest sent = server.takeRequest();
                    assertEquals(0, sent.getSequenceNumber());
                    assertEquals("access=synthetic", sent.getHeader("Cookie"));
                    assertEquals("synthetic-native-token", sent.getHeader("X-NEU-Mobile-Token"));
                }
            } finally {
                transport.close();
                client.connectionPool().evictAll();
            }
        }
    }

    @Test public void axiosTimeoutOverridesInheritedSocketTimeouts() throws Exception {
        try (MockWebServer server = new MockWebServer()) {
            server.enqueue(new MockResponse().setHeadersDelay(350, TimeUnit.MILLISECONDS)
                .setBody("{\"requires_webvpn\":true,\"error_code\":\"DIRECT_ACCESS_FAILED\"}"));
            server.enqueue(new MockResponse().setBodyDelay(350, TimeUnit.MILLISECONDS)
                .setBody("{\"items\":[{\"course_code\":\"TEST001\"}],\"total\":1}"));
            OkHttpClient shortSocketTimeout = new OkHttpClient.Builder()
                .readTimeout(100, TimeUnit.MILLISECONDS).build();
            OkHttpTransport transport = new OkHttpTransport(shortSocketTimeout, server.url("/"),
                Collections.emptyMap(), null);
            JSONObject login = request(transport,
                "{\"path\":\"/api/login\",\"method\":\"POST\",\"timeout_ms\":3000}");
            assertEquals(login.toString(), 200, login.getInt("status"));
            assertTrue(new JSONObject(login.getString("body")).getBoolean("requires_webvpn"));
            JSONObject outlines = request(transport,
                "{\"path\":\"/api/course-outlines/search\",\"method\":\"POST\",\"timeout_ms\":3000}");
            assertEquals(outlines.toString(), 200, outlines.getInt("status"));
            assertEquals(1, new JSONObject(outlines.getString("body")).getInt("total"));
            assertEquals(2, server.getRequestCount());
        }
    }

    @Test public void connectFailureBeforeSendingMutationCanTryNextAddress() throws Exception {
        try (MockWebServer server = new MockWebServer()) {
            server.start(java.net.InetAddress.getByName("127.0.0.1"), 0);
            server.enqueue(new MockResponse().setBody("{\"success\":true}"));
            OkHttpClient client = new OkHttpClient.Builder()
                .proxy(java.net.Proxy.NO_PROXY)
                .dns(host -> java.util.Arrays.asList(
                    java.net.InetAddress.getByName("127.0.0.2"),
                    java.net.InetAddress.getByName("127.0.0.1")))
                .build();
            OkHttpTransport transport = new OkHttpTransport(client,
                server.url("/").newBuilder().host("backend.test").build(), Collections.emptyMap(), null);
            JSONObject response = request(transport,
                "{\"path\":\"/api/webvpn/sms/verify\",\"method\":\"POST\",\"body\":\"{}\",\"timeout_ms\":5000}");
            assertEquals(response.toString(), 200, response.getInt("status"));
            assertEquals(1, server.getRequestCount());
        }
    }

    private JSONObject request(OkHttpTransport transport, String json) throws Exception {
        AtomicReference<JSONObject> result = new AtomicReference<>();
        CountDownLatch done = new CountDownLatch(1);
        transport.request("test", NativeRequest.parse(json), payload -> {
            result.set(payload);
            done.countDown();
        });
        assertTrue("Native response timed out", done.await(10, TimeUnit.SECONDS));
        return result.get();
    }

    @Test public void redirectsNeverReachAnotherOrigin() throws Exception {
        try (MockWebServer source = new MockWebServer(); MockWebServer target = new MockWebServer()) {
            source.enqueue(new MockResponse().setResponseCode(302)
                .addHeader("Location", target.url("/api/private")));
            OkHttpTransport transport = new OkHttpTransport(new OkHttpClient(), source.url("/"),
                Collections.emptyMap(), null);
            assertEquals(302, request(transport, "{\"path\":\"/api/start\"}").getInt("status"));
            assertNull(target.takeRequest(200, TimeUnit.MILLISECONDS));
        }
    }

    @Test public void cookiesRemainNativeAndFixedTokenCannotBeForged() throws Exception {
        try (MockWebServer server = new MockWebServer()) {
            server.enqueue(new MockResponse().addHeader("Set-Cookie", "session=secret; HttpOnly")
                .addHeader("Content-Type", "application/json").setBody("{\"ok\":true}"));
            OkHttpTransport transport = new OkHttpTransport(new OkHttpClient(), server.url("/"),
                Collections.singletonMap("X-NEU-Mobile-Token", "native-token"), null);
            JSONObject response = request(transport,
                "{\"path\":\"/api/start\",\"headers\":{\"X-NEU-Mobile-Token\":\"forged\",\"Cookie\":\"forged\"}}");
            assertFalse(response.getJSONObject("headers").has("set-cookie"));
            okhttp3.mockwebserver.RecordedRequest sent = server.takeRequest();
            assertEquals("native-token", sent.getHeader("X-NEU-Mobile-Token"));
            assertNull(sent.getHeader("Cookie"));
        }
    }

    @Test public void httpFailureDoesNotReplayMutations() throws Exception {
        for (int status : new int[] {408, 503}) {
            try (MockWebServer server = new MockWebServer()) {
                server.enqueue(new MockResponse().setResponseCode(status).addHeader("Retry-After", "0"));
                server.enqueue(new MockResponse().setBody("must not be sent"));
                OkHttpTransport transport = new OkHttpTransport(new OkHttpClient(), server.url("/"),
                    Collections.emptyMap(), null);
                assertEquals(status, request(transport,
                    "{\"path\":\"/api/mutation\",\"method\":\"POST\",\"body\":\"{}\"}").getInt("status"));
                assertEquals(1, server.getRequestCount());
            }
        }
    }

    @Test public void mutationDeadlineStillExpiresWithoutReplay() throws Exception {
        try (MockWebServer server = new MockWebServer()) {
            server.enqueue(new MockResponse().setHeadersDelay(2, TimeUnit.SECONDS).setBody("{\"success\":true}"));
            server.enqueue(new MockResponse().setBody("must not be sent"));
            OkHttpTransport transport = new OkHttpTransport(new OkHttpClient(), server.url("/"),
                Collections.emptyMap(), null);
            assertEquals("ECONNABORTED", request(transport,
                "{\"path\":\"/api/webvpn/sms/verify\",\"method\":\"POST\",\"timeout_ms\":1000}").getString("code"));
            assertEquals(1, server.getRequestCount());
        }
    }

    @Test public void untrustedHttpsCertificateIsRejected() throws Exception {
        HeldCertificate certificate = new HeldCertificate.Builder().addSubjectAlternativeName("localhost").build();
        HandshakeCertificates certificates = new HandshakeCertificates.Builder().heldCertificate(certificate).build();
        try (MockWebServer server = new MockWebServer()) {
            server.useHttps(certificates.sslSocketFactory(), false);
            server.enqueue(new MockResponse().setBody("must not be read"));
            OkHttpTransport transport = new OkHttpTransport(new OkHttpClient(), server.url("/"),
                Collections.emptyMap(), null);
            assertEquals("ERR_NETWORK", request(transport, "{\"path\":\"/api/health\"}").getString("code"));
            assertEquals(0, server.getRequestCount());
        }
    }

    @Test public void timeoutDuringBodyReadRemainsTimeout() throws Exception {
        try (MockWebServer server = new MockWebServer()) {
            server.enqueue(new MockResponse().setBody("slow body").setBodyDelay(3, TimeUnit.SECONDS));
            OkHttpTransport transport = new OkHttpTransport(new OkHttpClient(), server.url("/"),
                Collections.emptyMap(), null);
            assertEquals("ECONNABORTED", request(transport,
                "{\"path\":\"/api/slow\",\"timeout_ms\":1000}").getString("code"));
        }
    }

    @Test public void closedSessionCannotSendNewRequests() throws Exception {
        try (MockWebServer server = new MockWebServer()) {
            OkHttpTransport transport = new OkHttpTransport(new OkHttpClient(), server.url("/"),
                Collections.emptyMap(), null);
            transport.close();
            assertEquals("ERR_CANCELED", request(transport,
                "{\"path\":\"/api/mutation\",\"method\":\"POST\"}").getString("code"));
            assertEquals(0, server.getRequestCount());
        }
    }

    @Test public void readReconnectsWhenPooledConnectionCloses() throws Exception {
        try (MockWebServer server = new MockWebServer()) {
            server.enqueue(new MockResponse().setBody("warm"));
            server.enqueue(new MockResponse().setSocketPolicy(
                okhttp3.mockwebserver.SocketPolicy.DISCONNECT_AFTER_REQUEST));
            server.enqueue(new MockResponse().setBody("reconnected"));
            OkHttpTransport transport = new OkHttpTransport(new OkHttpClient(), server.url("/"),
                Collections.emptyMap(), null);
            assertEquals(200, request(transport, "{\"path\":\"/api/health\"}").getInt("status"));
            assertEquals("reconnected", request(transport, "{\"path\":\"/api/health\"}").getString("body"));
            assertEquals(3, server.getRequestCount());
        }
    }

    @Test public void disconnectedMutationIsNeverReplayed() throws Exception {
        try (MockWebServer server = new MockWebServer()) {
            server.enqueue(new MockResponse().setBody("warm"));
            server.enqueue(new MockResponse().setSocketPolicy(
                okhttp3.mockwebserver.SocketPolicy.DISCONNECT_AFTER_REQUEST));
            server.enqueue(new MockResponse().setBody("must not be sent"));
            OkHttpTransport transport = new OkHttpTransport(new OkHttpClient(), server.url("/"),
                Collections.emptyMap(), null);
            assertEquals(200, request(transport, "{\"path\":\"/api/health\"}").getInt("status"));
            assertEquals("ERR_NETWORK", request(transport,
                "{\"path\":\"/api/mutation\",\"method\":\"POST\",\"body\":\"{}\"}").getString("code"));
            assertEquals(2, server.getRequestCount());
        }
    }
}
