package io.github.rekayoo.neujwxt.local;

import android.content.Context;
import android.content.Intent;
import android.webkit.WebView;
import androidx.test.core.app.ActivityScenario;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import org.junit.Test;
import org.junit.runner.RunWith;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.lang.reflect.Field;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.Assert.*;

@RunWith(AndroidJUnit4.class)
public class CachedStartupTest {
    @Test public void cachedDefaultTimetableRendersBeforePythonCanStart() throws Exception {
        Context application = InstrumentationRegistry.getInstrumentation().getTargetContext();
        application.stopService(new Intent(application, LocalBackendService.class));
        for (int i = 0; i < 100 && LocalBackendService.isRunning(); i++) Thread.sleep(50);
        assertFalse(LocalBackendService.isRunning());
        Field lock = LocalBackendService.class.getDeclaredField("PYTHON_LOCK");
        lock.setAccessible(true);
        // Holding the real startup lock proves that no Python health response can unblock the page.
        synchronized (lock.get(null)) {
            try (ActivityScenario<MainActivity> scenario = ActivityScenario.launch(MainActivity.class)) {
                try {
                    awaitScript(scenario, "typeof window.__neuNativeDeliver === 'function'");
                    Context tests = InstrumentationRegistry.getInstrumentation().getContext();
                    try (InputStream input = tests.getAssets().open("cached_timetable_fixture.js");
                         ByteArrayOutputStream output = new ByteArrayOutputStream()) {
                        byte[] buffer = new byte[4096];
                        int count;
                        while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
                        evaluate(scenario, output.toString("UTF-8"));
                    }
                    awaitScript(scenario, "window.__cachedFixtureReady === true");
                    scenario.recreate();
                    awaitScript(scenario, "location.pathname === '/timetable' && "
                        + "document.body.innerText.includes('启动缓存验收课程') && "
                        + "!document.querySelector('.loading')");
                    scenario.onActivity(activity -> assertEquals(android.view.View.GONE,
                        activity.findViewById(io.github.rekayoo.neujwxt.shared.R.id.progress).getVisibility()));
                    CountDownLatch painted = new CountDownLatch(1);
                    scenario.onActivity(activity -> {
                        WebView web = activity.findViewById(io.github.rekayoo.neujwxt.shared.R.id.webview);
                        web.postVisualStateCallback(2, new WebView.VisualStateCallback() {
                            @Override public void onComplete(long id) { painted.countDown(); }
                        });
                    });
                    assertTrue(painted.await(10, TimeUnit.SECONDS));
                    saveScreenshot("cached-before-python.png");
                } finally {
                    // Restore the login guard even on failure so later tests remain independent.
                    evaluate(scenario, "localStorage.removeItem('neu-toolbox-timetable-recovery-namespace');"
                        + "localStorage.removeItem('neu_toolbox:defaultTimetableOnOpen');");
                }
            } finally {
                application.stopService(new Intent(application, LocalBackendService.class));
            }
        }
    }

    private static void awaitScript(ActivityScenario<MainActivity> scenario, String script) throws Exception {
        for (int i = 0; i < 100; i++) {
            if ("true".equals(evaluate(scenario, script))) return;
            Thread.sleep(200);
        }
        String state = evaluate(scenario, "JSON.stringify({path:location.pathname,"
            + "text:document.body.innerText,loading:!!document.querySelector('.loading'),"
            + "namespace:localStorage.getItem('neu-toolbox-timetable-recovery-namespace'),"
            + "defaultTimetable:localStorage.getItem('neu_toolbox:defaultTimetableOnOpen')})");
        saveScreenshot("cached-startup-failure.png");
        fail("Page did not render while Python was blocked: " + script + "; state=" + state);
    }

    private static void saveScreenshot(String filename) throws Exception {
        android.app.Instrumentation instrumentation = InstrumentationRegistry.getInstrumentation();
        android.graphics.Bitmap screenshot = instrumentation.getUiAutomation().takeScreenshot();
        assertNotNull(screenshot);
        java.io.File directory = new java.io.File(
            instrumentation.getTargetContext().getExternalFilesDir(null), "test-screenshots");
        assertTrue(directory.isDirectory() || directory.mkdirs());
        try (java.io.FileOutputStream output = new java.io.FileOutputStream(new java.io.File(directory, filename))) {
            assertTrue(screenshot.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, output));
        } finally { screenshot.recycle(); }
    }

    private static String evaluate(ActivityScenario<MainActivity> scenario, String script) throws Exception {
        CountDownLatch done = new CountDownLatch(1);
        AtomicReference<String> result = new AtomicReference<>();
        scenario.onActivity(activity -> {
            WebView web = activity.findViewById(io.github.rekayoo.neujwxt.shared.R.id.webview);
            web.evaluateJavascript(script, value -> { result.set(value); done.countDown(); });
        });
        assertTrue(done.await(5, TimeUnit.SECONDS));
        return result.get();
    }
}
