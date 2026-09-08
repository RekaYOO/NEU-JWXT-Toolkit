package io.github.rekayoo.neujwxt.local;

import android.app.UiAutomation;
import android.content.Context;
import android.os.ParcelFileDescriptor;
import androidx.core.app.NotificationManagerCompat;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import io.github.rekayoo.neujwxt.shared.ApiTransport;
import io.github.rekayoo.neujwxt.shared.NativeRequest;
import java.io.InputStream;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import org.json.JSONObject;
import org.junit.Test;
import org.junit.runner.RunWith;
import static org.junit.Assert.*;

@RunWith(AndroidJUnit4.class)
public class NotificationPermissionTest {
    private void notificationMode(UiAutomation ui, Context context, String mode) throws Exception {
        try (InputStream input = new ParcelFileDescriptor.AutoCloseInputStream(ui.executeShellCommand(
            "appops set " + context.getPackageName() + " POST_NOTIFICATION " + mode))) {
            byte[] buffer = new byte[1024];
            while (input.read(buffer) != -1) {}
        }
        for (int attempt = 0; attempt < 50; attempt++) {
            if (NotificationManagerCompat.from(context).areNotificationsEnabled() == mode.equals("allow")) return;
            Thread.sleep(100);
        }
        fail("Notification app-op did not change");
    }

    @Test public void deniedNotificationsBlockEnablingButNotStoppingTasks() throws Exception {
        Context context = InstrumentationRegistry.getInstrumentation().getTargetContext();
        UiAutomation ui = InstrumentationRegistry.getInstrumentation().getUiAutomation();
        assertTrue("CI must install the local test APK with notification permission",
            NotificationManagerCompat.from(context).areNotificationsEnabled());
        AtomicInteger delegated = new AtomicInteger();
        ApiTransport delegate = new ApiTransport() {
            @Override public void request(String id, NativeRequest request, Callback callback) {
                delegated.incrementAndGet();
                try { callback.complete(new JSONObject().put("status", 200)); }
                catch (Exception exception) { throw new AssertionError(exception); }
            }
            @Override public void cancel(String id) {}
            @Override public void close() {}
        };
        PermissionGuardTransport guarded = new PermissionGuardTransport(context, delegate);
        try {
            notificationMode(ui, context, "ignore");
            String[] denied = {
                "{\"method\":\"PATCH\",\"path\":\"/api/grade-tracking/enabled\",\"body\":\"{\\\"enabled\\\":true}\"}",
                "{\"method\":\"PUT\",\"path\":\"/api/grade-tracking/config\",\"body\":\"{\\\"enabled\\\":true}\"}",
                "{\"method\":\"POST\",\"path\":\"/api/course-selection/jwxk/automation/tasks\"}",
                "{\"method\":\"POST\",\"path\":\"/api/course-selection/jwxk/automation/tasks/%73tart\"}"
            };
            for (String value : denied) {
                AtomicReference<JSONObject> result = new AtomicReference<>();
                guarded.request("denied", NativeRequest.parse(value), result::set);
                assertNotNull(result.get());
                assertEquals(409, result.get().getInt("status"));
            }
            assertEquals("Denied tasks reached the backend", 0, delegated.get());
            AtomicReference<JSONObject> result = new AtomicReference<>();
            guarded.request("stop", NativeRequest.parse(
                "{\"method\":\"PATCH\",\"path\":\"/api/grade-tracking/enabled\",\"body\":\"{\\\"enabled\\\":false}\"}"),
                result::set);
            assertEquals(200, result.get().getInt("status"));
            assertEquals(1, delegated.get());
        } finally {
            guarded.close();
            notificationMode(ui, context, "allow");
        }
    }
}
