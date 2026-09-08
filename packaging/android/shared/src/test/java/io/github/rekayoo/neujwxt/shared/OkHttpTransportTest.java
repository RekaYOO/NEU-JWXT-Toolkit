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

    @Test public void serviceUnavailableDoesNotReplayMutations() throws Exception {
        try (MockWebServer server = new MockWebServer()) {
            server.enqueue(new MockResponse().setResponseCode(503).addHeader("Retry-After", "0"));
            server.enqueue(new MockResponse().setBody("must not be sent"));
            OkHttpTransport transport = new OkHttpTransport(new OkHttpClient(), server.url("/"),
                Collections.emptyMap(), null);
            assertEquals(503, request(transport,
                "{\"path\":\"/api/mutation\",\"method\":\"POST\",\"body\":\"{}\"}").getInt("status"));
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
}
