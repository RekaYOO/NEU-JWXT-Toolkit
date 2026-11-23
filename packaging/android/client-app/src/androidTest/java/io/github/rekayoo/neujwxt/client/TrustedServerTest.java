package io.github.rekayoo.neujwxt.client;

import android.os.SystemClock;
import android.widget.EditText;
import androidx.test.core.app.ActivityScenario;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import java.lang.reflect.Field;
import okhttp3.OkHttpClient;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.tls.HandshakeCertificates;
import okhttp3.tls.HeldCertificate;
import org.junit.Test;
import org.junit.runner.RunWith;
import static androidx.test.espresso.Espresso.onView;
import static androidx.test.espresso.action.ViewActions.click;
import static androidx.test.espresso.action.ViewActions.closeSoftKeyboard;
import static androidx.test.espresso.action.ViewActions.replaceText;
import static androidx.test.espresso.matcher.ViewMatchers.isAssignableFrom;
import static androidx.test.espresso.matcher.ViewMatchers.withText;
import static org.junit.Assert.assertEquals;

@RunWith(AndroidJUnit4.class)
public class TrustedServerTest {
    @Test public void savesTrustedServerAfterProbeAndReopens() throws Exception {
        String endpoint = InstrumentationRegistry.getArguments().getString("toolboxHttpsOrigin");
        if (endpoint != null) {
            verifySavedServer(endpoint, null);
            return;
        }
        HeldCertificate certificate = new HeldCertificate.Builder().addSubjectAlternativeName("localhost").build();
        HandshakeCertificates serverTls = new HandshakeCertificates.Builder().heldCertificate(certificate).build();
        HandshakeCertificates clientTls = new HandshakeCertificates.Builder().addTrustedCertificate(certificate.certificate()).build();
        try (MockWebServer server = new MockWebServer()) {
            server.useHttps(serverTls.sslSocketFactory(), false);
            server.enqueue(new MockResponse().setBody("{\"mobile_api_version\":1}"));
            server.start();
            verifySavedServer(server.url("/").toString(), clientTls);
            assertEquals("/api/health", server.takeRequest().getPath());
        }
    }

    private void verifySavedServer(String endpoint, HandshakeCertificates clientTls) {
        String expected = ServerConfigStore.validate(endpoint).toString();
        ServerConfigStore config = new ServerConfigStore(InstrumentationRegistry.getInstrumentation().getTargetContext());
        String original = config.getServerUrl();
        try {
            config.setServerUrl("");
            try (ActivityScenario<MainActivity> scenario = ActivityScenario.launch(MainActivity.class)) {
                if (clientTls != null) {
                    scenario.onActivity(activity -> {
                        try {
                            Field field = MainActivity.class.getDeclaredField("client");
                            field.setAccessible(true);
                            OkHttpClient client = (OkHttpClient) field.get(activity);
                            field.set(activity, client.newBuilder().sslSocketFactory(clientTls.sslSocketFactory(), clientTls.trustManager()).build());
                        } catch (ReflectiveOperationException exception) {
                            throw new AssertionError(exception);
                        }
                    });
                }
                onView(isAssignableFrom(EditText.class)).perform(replaceText(endpoint), closeSoftKeyboard());
                onView(withText("检测并保存")).perform(click());
                long deadline = SystemClock.elapsedRealtime() + 20000;
                while (!expected.equals(config.getServerUrl()) && SystemClock.elapsedRealtime() < deadline) {
                    SystemClock.sleep(100);
                }
                assertEquals(expected, config.getServerUrl());
            }
            try (ActivityScenario<MainActivity> reopened = ActivityScenario.launch(MainActivity.class)) {
                reopened.onActivity(activity -> activity.openServerSettings());
                onView(isAssignableFrom(EditText.class)).check(androidx.test.espresso.assertion.ViewAssertions.matches(withText(expected)));
            }
        } finally {
            config.setServerUrl(original);
        }
    }
}
