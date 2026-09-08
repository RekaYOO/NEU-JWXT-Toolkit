package io.github.rekayoo.neujwxt.local;

import android.content.Context;
import androidx.core.app.NotificationManagerCompat;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import io.github.rekayoo.neujwxt.shared.ApiTransport;
import io.github.rekayoo.neujwxt.shared.NativeRequest;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import org.json.JSONObject;
import org.junit.Test;
import org.junit.runner.RunWith;
import static org.junit.Assert.*;

@RunWith(AndroidJUnit4.class)
public class NotificationPermissionTest {
    @Test public void deniedNotificationsBlockEnablingButNotStoppingTasks() throws Exception {
        Context context = InstrumentationRegistry.getInstrumentation().getTargetContext();
        assertFalse("Run this test separately after revoking POST_NOTIFICATIONS",
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
        }
    }
}
