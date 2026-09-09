package io.github.rekayoo.neujwxt.local;

import android.webkit.WebView;
import androidx.test.core.app.ActivityScenario;
import androidx.test.core.app.ApplicationProvider;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.lifecycle.Lifecycle;
import com.chaquo.python.Python;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import org.json.JSONObject;
import org.junit.Test;
import org.junit.runner.RunWith;
import static org.junit.Assert.*;

@RunWith(AndroidJUnit4.class)
public class LocalLaunchTest {
    @Test public void bundledPythonAndReactStartWithProtectedHealth() throws Exception {
        // ActivityScenario matches lifecycle events by Intent action/categories.
        // Use the same explicit entry as notifications; launcher cold starts are
        // covered separately by CachedStartupTest.
        android.content.Intent entry = new android.content.Intent(
            ApplicationProvider.getApplicationContext(), MainActivity.class);
        try (ActivityScenario<MainActivity> scenario = ActivityScenario.launch(entry)) {
            String endpoint = null;
            for (int attempt = 0; attempt < 240; attempt++) {
                if (LocalBackendService.startupFailure() != null) {
                    throw new AssertionError("Embedded Python startup failed", LocalBackendService.startupFailure());
                }
                endpoint = LocalBackendService.endpoint();
                if (endpoint != null) break;
                Thread.sleep(500);
            }
            assertNotNull("Local FastAPI failed to start", endpoint);
            OkHttpClient http = new OkHttpClient.Builder().retryOnConnectionFailure(false).build();
            try (Response denied = http.newCall(new Request.Builder().url(endpoint + "api/health").build()).execute()) {
                assertEquals(401, denied.code());
            }
            try (Response allowed = http.newCall(new Request.Builder().url(endpoint + "api/health")
                .header("X-NEU-Mobile-Token", LocalBackendService.sessionToken()).build()).execute()) {
                assertEquals(200, allowed.code());
                JSONObject body = new JSONObject(allowed.body().string());
                assertEquals("mobile", body.getString("profile"));
                assertEquals(1, body.getInt("mobile_api_version"));
            }
            assertEquals("2.46.4", Python.getInstance().getModule("pydantic_core").get("__version__").toString());
            assertEquals("6.1.1", Python.getInstance().getModule("lxml.etree").get("__version__").toString());
            assertEquals("3.23.0", Python.getInstance().getModule("Crypto").get("__version__").toString());
            com.chaquo.python.PyObject bytes = Python.getInstance().getModule("builtins").callAttr("bytes", 16);
            assertEquals("66e94bd4ef8a2c3b884cfa59ca342b2e",
                Python.getInstance().getModule("Crypto.Cipher.AES").callAttr(
                    "new", bytes, 1).callAttr("encrypt", bytes).callAttr("hex").toString());
            for (int attempt = 0; attempt < 60; attempt++) {
                CountDownLatch done = new CountDownLatch(1);
                AtomicBoolean rendered = new AtomicBoolean();
                scenario.onActivity(activity -> {
                    WebView web = activity.findViewById(io.github.rekayoo.neujwxt.shared.R.id.webview);
                    web.evaluateJavascript(
                        "location.origin === 'https://appassets.androidplatform.net' && "
                        + "!!document.querySelector('.login-shell input') && document.body.innerText.trim().length > 20",
                        value -> { rendered.set("true".equals(value)); done.countDown(); });
                });
                assertTrue(done.await(5, TimeUnit.SECONDS));
                if (rendered.get()) {
                    CountDownLatch painted = new CountDownLatch(1);
                    scenario.onActivity(activity -> {
                        WebView web = activity.findViewById(io.github.rekayoo.neujwxt.shared.R.id.webview);
                        web.postVisualStateCallback(1, new WebView.VisualStateCallback() {
                            @Override public void onComplete(long requestId) {
                                web.invalidate();
                                painted.countDown();
                            }
                        });
                    });
                    assertTrue("Login form did not reach the WebView compositor", painted.await(10, TimeUnit.SECONDS));
                    Thread.sleep(1000);
                    android.app.Instrumentation instrumentation =
                        androidx.test.platform.app.InstrumentationRegistry.getInstrumentation();
                    java.io.File directory = new java.io.File(
                        instrumentation.getTargetContext().getExternalFilesDir(null), "test-screenshots");
                    assertTrue(directory.isDirectory() || directory.mkdirs());
                    android.graphics.Bitmap screenshot = instrumentation.getUiAutomation().takeScreenshot();
                    assertNotNull(screenshot);
                    boolean visibleForm = false;
                    for (int frame = 0; frame < 50; frame++) {
                        int white = 0;
                        int sampled = 0;
                        for (int y = screenshot.getHeight() / 5; y < screenshot.getHeight() * 4 / 5; y += 8) {
                            for (int x = screenshot.getWidth() / 10; x < screenshot.getWidth() * 9 / 10; x += 8) {
                                int pixel = screenshot.getPixel(x, y);
                                if (android.graphics.Color.red(pixel) > 250 && android.graphics.Color.green(pixel) > 250
                                    && android.graphics.Color.blue(pixel) > 250) white++;
                                sampled++;
                            }
                        }
                        // The white login panel must replace the blue-gray startup screen.
                        if (white > sampled / 3) { visibleForm = true; break; }
                        if (frame == 49) break;
                        screenshot.recycle();
                        Thread.sleep(200);
                        screenshot = instrumentation.getUiAutomation().takeScreenshot();
                        assertNotNull(screenshot);
                    }
                    try (java.io.FileOutputStream output = new java.io.FileOutputStream(
                        new java.io.File(directory, "local-startup.png"))) {
                        assertTrue(screenshot.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, output));
                    } finally {
                        screenshot.recycle();
                    }
                    assertTrue("Login form exists in DOM but was not painted on screen", visibleForm);
                    String originalToken = LocalBackendService.sessionToken();
                    String originalEndpoint = LocalBackendService.endpoint();
                    scenario.onActivity(activity -> activity.stopService(
                        new android.content.Intent(activity, LocalBackendService.class)));
                    Thread.sleep(500);
                    scenario.recreate();
                    CountDownLatch restarted = new CountDownLatch(1);
                    scenario.onActivity(activity -> LocalBackendService.whenReady(restarted::countDown));
                    assertTrue("Service recreation never became ready", restarted.await(60, TimeUnit.SECONDS));
                    assertEquals(originalToken, LocalBackendService.sessionToken());
                    assertEquals(originalEndpoint, LocalBackendService.endpoint());
                    http.connectionPool().evictAll();
                    try (Response healthy = http.newCall(new Request.Builder().url(originalEndpoint + "api/health")
                        .header("X-NEU-Mobile-Token", originalToken).build()).execute()) {
                        assertEquals(200, healthy.code());
                    }
                    verifySlowBackendResponses(scenario);
                    verifyNotificationsAndTaskLifecycle(scenario);
                    scenario.moveToState(Lifecycle.State.DESTROYED);
                    assertEquals(Lifecycle.State.DESTROYED, scenario.getState());
                    return;
                }
                Thread.sleep(500);
            }
            fail("Local React application never rendered");
        }
    }

    private void verifySlowBackendResponses(ActivityScenario<MainActivity> scenario) throws Exception {
        // Service readiness precedes the recreated page's module initialization.
        // Install the observer only after React has installed its bridge callback.
        boolean bridgeReady = false;
        for (int attempt = 0; attempt < 120; attempt++) {
            if ("true".equals(evaluate(scenario,
                "typeof window.__neuNativeDeliver === 'function' && !!document.querySelector('.login-shell input')"))) {
                bridgeReady = true;
                break;
            }
            Thread.sleep(500);
        }
        assertTrue("Recreated login page did not initialize its bridge", bridgeReady);
        com.chaquo.python.PyObject builtins = Python.getInstance().getModule("builtins");
        com.chaquo.python.PyObject scope = builtins.callAttr("dict");
        android.content.Context tests = androidx.test.platform.app.InstrumentationRegistry
            .getInstrumentation().getContext();
        String fixture;
        try (java.io.InputStream input = tests.getAssets().open("slow_runtime_fixture.py");
             java.io.ByteArrayOutputStream bytes = new java.io.ByteArrayOutputStream()) {
            byte[] buffer = new byte[8192];
            int count;
            while ((count = input.read(buffer)) != -1) bytes.write(buffer, 0, count);
            fixture = bytes.toString("UTF-8");
        }
        try {
            builtins.callAttr("exec", fixture, scope);
            evaluate(scenario,
                "window.__neuParityResults = {}; window.__neuParityDeliver = window.__neuNativeDeliver;"
                + "window.__neuNativeDeliver = (id, payload) => {"
                + "window.__neuParityResults[id] = payload; window.__neuParityDeliver(id, payload); };");
            JSONObject warm = nativeRequest(scenario, "/api/health", "", "GET");
            assertEquals(warm.toString(), 200, warm.getInt("status"));
            // A user spends time filling the form after page initialization.
            // Let Uvicorn's idle keep-alive expire before the first submission.
            Thread.sleep(6000);
            JSONObject direct = nativeRequest(scenario, "/api/login",
                "{\"username\":\"20240001\",\"password\":\"synthetic-password\",\"network_mode\":\"direct\"}");
            assertEquals(direct.toString(), 200, direct.getInt("status"));
            assertTrue(new JSONObject(direct.getString("body")).getBoolean("requires_webvpn"));
            JSONObject sms = nativeRequest(scenario, "/api/webvpn/sms/verify",
                "{\"flow_id\":\"synthetic-sms\",\"code\":\"123456\"}");
            assertEquals(sms.toString(), 200, sms.getInt("status"));
            assertEquals("authenticated", new JSONObject(sms.getString("body")).getString("status"));
            JSONObject outlines = nativeRequest(scenario, "/api/course-outlines/search", "{\"page\":1,\"page_size\":20}");
            assertEquals(outlines.toString(), 200, outlines.getInt("status"));
            assertEquals(1, new JSONObject(outlines.getString("body")).getInt("total"));
            assertEquals("no-store", outlines.getJSONObject("headers").getString("cache-control"));
            assertTrue(builtins.callAttr("eval", "verify()", scope).toBoolean());
        } finally {
            builtins.callAttr("exec", "if 'cleanup' in globals(): cleanup()", scope);
            evaluate(scenario,
                "if (window.__neuParityDeliver) window.__neuNativeDeliver = window.__neuParityDeliver;"
                + "delete window.__neuParityDeliver; delete window.__neuParityResults; delete window.__neuParityId;");
        }
    }

    private JSONObject nativeRequest(ActivityScenario<MainActivity> scenario, String path, String body) throws Exception {
        return nativeRequest(scenario, path, body, "POST");
    }

    private JSONObject nativeRequest(ActivityScenario<MainActivity> scenario, String path, String body,
                                     String method) throws Exception {
        String request = new JSONObject().put("path", path).put("method", method)
            .put("body", body).put("timeout_ms", 30000).toString();
        evaluate(scenario, "window.__neuParityId = window.NeuNative.request(" + JSONObject.quote(request) + ");");
        for (int attempt = 0; attempt < 90; attempt++) {
            String encoded = evaluate(scenario, "JSON.stringify(window.__neuParityResults[window.__neuParityId] || null)");
            Object decoded = new org.json.JSONTokener(encoded).nextValue();
            if (decoded instanceof String && !decoded.equals("null")) return new JSONObject((String) decoded);
            Thread.sleep(500);
        }
        throw new AssertionError("Native response never arrived: " + path);
    }

    private String evaluate(ActivityScenario<MainActivity> scenario, String script) throws Exception {
        CountDownLatch done = new CountDownLatch(1);
        java.util.concurrent.atomic.AtomicReference<String> result = new java.util.concurrent.atomic.AtomicReference<>();
        scenario.onActivity(activity -> {
            WebView web = activity.findViewById(io.github.rekayoo.neujwxt.shared.R.id.webview);
            web.evaluateJavascript(script, value -> { result.set(value); done.countDown(); });
        });
        assertTrue("WebView did not respond", done.await(10, TimeUnit.SECONDS));
        return result.get();
    }

    private void verifyNotificationsAndTaskLifecycle(ActivityScenario<MainActivity> scenario) throws Exception {
        java.util.concurrent.atomic.AtomicReference<MainActivity> launchedActivity =
            new java.util.concurrent.atomic.AtomicReference<>();
        scenario.onActivity(launchedActivity::set);
        Python python = Python.getInstance();
        com.chaquo.python.PyObject builtins = python.getModule("builtins");
        com.chaquo.python.PyObject testScope = builtins.callAttr("dict");
        android.content.Context context = androidx.test.platform.app.InstrumentationRegistry
            .getInstrumentation().getTargetContext();
        android.app.NotificationManager manager = context.getSystemService(android.app.NotificationManager.class);
        assertTrue(androidx.core.app.NotificationManagerCompat.from(context).areNotificationsEnabled());
        // Override only the state query. No tracking worker or remote account is enabled.
        builtins.callAttr("exec",
            "from backend.app.main import app as _neu_test_app\n"
            + "from backend.app.dependencies import get_grade_tracker as _neu_test_tracker\n"
            + "from types import SimpleNamespace as _NeuTestNamespace\n"
            + "_neu_test_app.dependency_overrides[_neu_test_tracker] = "
            + "lambda: _NeuTestNamespace(get_status=lambda: {'enabled': True})", testScope);
        try {
            com.chaquo.python.PyObject notifications = python.getModule("backend.app.dependencies")
                .callAttr("get_system_mail_service");
            assertTrue(notifications.callAttr("queue_notification", "auth_recovery",
                "Integration login required", "Synthetic notification only", "android-integration-login").toBoolean());
            scenario.onActivity(activity -> androidx.core.content.ContextCompat.startForegroundService(
                activity, new android.content.Intent(activity, LocalBackendService.class)));
            android.app.Notification delivered = null;
            for (int attempt = 0; attempt < 100; attempt++) {
                for (android.service.notification.StatusBarNotification item : manager.getActiveNotifications()) {
                    if ("Integration login required".contentEquals(
                        item.getNotification().extras.getCharSequence(android.app.Notification.EXTRA_TITLE, ""))) {
                        delivered = item.getNotification();
                    }
                }
                if (delivered != null && notifications.callAttr("pending_count").toInt() == 0) break;
                Thread.sleep(200);
            }
            assertNotNull("Durable outbox was never displayed", delivered);
            assertEquals(0, notifications.callAttr("pending_count").toInt());
            assertNotNull("Lock screen summary is missing", delivered.publicVersion);
            assertEquals("NEU 工具箱有新消息",
                delivered.publicVersion.extras.getCharSequence(android.app.Notification.EXTRA_TITLE).toString());
            if (android.os.Build.VERSION.SDK_INT >= 26) assertEquals("task_errors", delivered.getChannelId());
            assertTrue(LocalBackendService.isRunning());
            assertTrue(java.util.Arrays.stream(manager.getActiveNotifications())
                .anyMatch(item -> (item.getNotification().flags & android.app.Notification.FLAG_FOREGROUND_SERVICE) != 0));
            // The page already starts at /login. Move its URL first so a
            // dropped notification cannot accidentally satisfy the assertion.
            assertEquals("\"/notification-test-before\"", evaluate(scenario,
                "history.replaceState(null, '', '/notification-test-before'); location.pathname"));
            delivered.contentIntent.send();
            boolean deepLink = false;
            for (int attempt = 0; attempt < 50; attempt++) {
                if ("true".equals(evaluate(scenario,
                    "location.pathname === '/login' && !!document.querySelector('.login-shell input')"))) {
                    deepLink = true;
                    break;
                }
                Thread.sleep(200);
            }
            assertTrue("Login notification did not open the local login page", deepLink);
            scenario.onActivity(activity -> {
                assertSame("Notification should reuse the current shell", launchedActivity.get(), activity);
                assertEquals("/login", activity.getIntent().getStringExtra("route"));
            });
            assertEquals(Lifecycle.State.RESUMED, scenario.getState());
            scenario.moveToState(Lifecycle.State.CREATED);
            scenario.moveToState(Lifecycle.State.RESUMED);
            assertEquals(Lifecycle.State.RESUMED, scenario.getState());
        } finally {
            builtins.callAttr("exec", "_neu_test_app.dependency_overrides.pop(_neu_test_tracker, None)", testScope);
            manager.cancelAll();
        }
        for (int attempt = 0; attempt < 100 && LocalBackendService.isRunning(); attempt++) Thread.sleep(200);
        assertFalse("Foreground service remained running without enabled tasks", LocalBackendService.isRunning());
    }
}
