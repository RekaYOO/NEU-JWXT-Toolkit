package io.github.rekayoo.neujwxt.local;

import android.webkit.WebView;
import androidx.test.core.app.ActivityScenario;
import androidx.test.ext.junit.runners.AndroidJUnit4;
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
        try (ActivityScenario<MainActivity> scenario = ActivityScenario.launch(MainActivity.class)) {
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
                    try (java.io.FileOutputStream output = new java.io.FileOutputStream(
                        new java.io.File(directory, "local-startup.png"))) {
                        assertTrue(screenshot.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, output));
                    } finally {
                        screenshot.recycle();
                    }
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
                    verifyNotificationsAndTaskLifecycle(scenario);
                    return;
                }
                Thread.sleep(500);
            }
            fail("Local React application never rendered");
        }
    }

    private void verifyNotificationsAndTaskLifecycle(ActivityScenario<MainActivity> scenario) throws Exception {
        java.util.concurrent.atomic.AtomicReference<android.content.Intent> launchIntent =
            new java.util.concurrent.atomic.AtomicReference<>();
        scenario.onActivity(activity -> launchIntent.set(new android.content.Intent(activity.getIntent())));
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
            delivered.contentIntent.send();
            boolean deepLink = false;
            for (int attempt = 0; attempt < 50; attempt++) {
                CountDownLatch done = new CountDownLatch(1);
                AtomicBoolean matched = new AtomicBoolean();
                scenario.onActivity(activity -> {
                    WebView web = activity.findViewById(io.github.rekayoo.neujwxt.shared.R.id.webview);
                    web.evaluateJavascript("location.pathname === '/login'", value -> {
                        matched.set("true".equals(value));
                        done.countDown();
                    });
                });
                assertTrue(done.await(5, TimeUnit.SECONDS));
                if (matched.get()) { deepLink = true; break; }
                Thread.sleep(200);
            }
            assertTrue("Login notification did not open the local login page", deepLink);
        } finally {
            builtins.callAttr("exec", "_neu_test_app.dependency_overrides.pop(_neu_test_tracker, None)", testScope);
            manager.cancelAll();
            // ActivityScenario matches cleanup events by the original launch intent.
            scenario.onActivity(activity -> activity.setIntent(launchIntent.get()));
        }
        for (int attempt = 0; attempt < 100 && LocalBackendService.isRunning(); attempt++) Thread.sleep(200);
        assertFalse("Foreground service remained running without enabled tasks", LocalBackendService.isRunning());
    }
}
