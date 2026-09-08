package io.github.rekayoo.neujwxt.client;

import android.webkit.WebView;
import androidx.test.core.app.ActivityScenario;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import io.github.rekayoo.neujwxt.shared.ApiTransport;
import io.github.rekayoo.neujwxt.shared.BaseShellActivity;
import io.github.rekayoo.neujwxt.shared.OkHttpTransport;
import java.lang.reflect.Field;
import java.util.Collections;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import okhttp3.OkHttpClient;
import okhttp3.mockwebserver.Dispatcher;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import okhttp3.tls.HandshakeCertificates;
import okhttp3.tls.HeldCertificate;
import org.json.JSONObject;
import org.json.JSONTokener;
import org.junit.Test;
import org.junit.runner.RunWith;
import static org.junit.Assert.*;

@RunWith(AndroidJUnit4.class)
public class ClientBridgeTest {
    private String evaluate(ActivityScenario<MainActivity> scenario, String script) throws Exception {
        CountDownLatch completed = new CountDownLatch(1);
        AtomicReference<String> result = new AtomicReference<>();
        scenario.onActivity(activity -> {
            WebView web = activity.findViewById(io.github.rekayoo.neujwxt.shared.R.id.webview);
            web.evaluateJavascript(script, value -> { result.set(value); completed.countDown(); });
        });
        assertTrue("WebView evaluation timed out", completed.await(5, TimeUnit.SECONDS));
        return result.get();
    }

    private JSONObject request(ActivityScenario<MainActivity> scenario, String path) throws Exception {
        evaluate(scenario, "window.__bridgeTestId = NeuNative.request("
            + JSONObject.quote(new JSONObject().put("path", path).toString()) + ");");
        for (int attempt = 0; attempt < 100; attempt++) {
            String value = evaluate(scenario,
                "JSON.stringify(window.__bridgeTestResults[window.__bridgeTestId] || null)");
            Object decoded = new JSONTokener(value).nextValue();
            if (decoded instanceof String && !decoded.equals("null")) return new JSONObject((String) decoded);
            Thread.sleep(50);
        }
        throw new AssertionError("Native HTTPS response never reached JavaScript");
    }

    @Test public void webViewBridgeUsesHttpsAndKeepsCookiesNative() throws Exception {
        HeldCertificate certificate = new HeldCertificate.Builder().addSubjectAlternativeName("localhost").build();
        HandshakeCertificates serverCertificates = new HandshakeCertificates.Builder().heldCertificate(certificate).build();
        HandshakeCertificates clientCertificates = new HandshakeCertificates.Builder()
            .addTrustedCertificate(certificate.certificate()).build();
        try (MockWebServer server = new MockWebServer()) {
            server.useHttps(serverCertificates.sslSocketFactory(), false);
            AtomicReference<String> receivedCookie = new AtomicReference<>();
            server.setDispatcher(new Dispatcher() {
                @Override public MockResponse dispatch(RecordedRequest request) {
                    if ("/api/native-integration".equals(request.getPath())) {
                        return new MockResponse().addHeader("Content-Type", "application/json")
                            .addHeader("Set-Cookie", "integration=fake-session; Path=/api/; Secure; HttpOnly")
                            .setBody("{\"bridge\":\"ok\"}");
                    }
                    if ("/api/native-integration/session".equals(request.getPath())) {
                        receivedCookie.set(request.getHeader("Cookie"));
                        return new MockResponse().setBody("{\"session\":\"ok\"}");
                    }
                    return new MockResponse().setResponseCode(404).setBody("{}");
                }
            });
            server.start();
            okhttp3.HttpUrl serverUrl = server.url("/");
            ServerConfigStore config = new ServerConfigStore(
                InstrumentationRegistry.getInstrumentation().getTargetContext());
            config.setServerUrl("https://offline.invalid/");
            try (ActivityScenario<MainActivity> scenario = ActivityScenario.launch(MainActivity.class)) {
                boolean loaded = false;
                for (int attempt = 0; attempt < 120; attempt++) {
                    loaded = "true".equals(evaluate(scenario,
                        "!!window.NeuNative && typeof window.__neuNativeDeliver === 'function'"));
                    if (loaded) break;
                    Thread.sleep(250);
                }
                assertTrue("Bundled frontend never installed the bridge", loaded);
                // Test-only trust is injected into the running bridge. Production remains system-trust-only.
                scenario.onActivity(activity -> {
                    try {
                        Field cookieField = MainActivity.class.getDeclaredField("cookies");
                        cookieField.setAccessible(true);
                        EncryptedCookieJar cookies = (EncryptedCookieJar) cookieField.get(activity);
                        cookies.setOrigin(serverUrl);
                        Field transportField = BaseShellActivity.class.getDeclaredField("transport");
                        transportField.setAccessible(true);
                        ((ApiTransport) transportField.get(activity)).close();
                        OkHttpClient http = new OkHttpClient.Builder().cookieJar(cookies)
                            .sslSocketFactory(clientCertificates.sslSocketFactory(), clientCertificates.trustManager()).build();
                        transportField.set(activity, new OkHttpTransport(http, serverUrl, Collections.emptyMap(), null));
                    } catch (ReflectiveOperationException exception) {
                        throw new AssertionError(exception);
                    }
                });
                evaluate(scenario, "window.__bridgeTestResults = {}; "
                    + "window.__bridgeOriginalDeliver = window.__neuNativeDeliver; "
                    + "window.__neuNativeDeliver = (id, value) => { window.__bridgeTestResults[id] = value; "
                    + "window.__bridgeOriginalDeliver(id, value); };");
                JSONObject first = request(scenario, "/api/native-integration");
                assertEquals(200, first.getInt("status"));
                assertEquals("ok", new JSONObject(first.getString("body")).getString("bridge"));
                assertFalse(first.getJSONObject("headers").has("set-cookie"));
                assertEquals(200, request(scenario, "/api/native-integration/session").getInt("status"));
                assertEquals("integration=fake-session", receivedCookie.get());
                assertEquals("\"\"", evaluate(scenario, "document.cookie"));
                assertEquals("\"https://appassets.androidplatform.net\"", evaluate(scenario, "location.origin"));
            }
        }
    }
}
