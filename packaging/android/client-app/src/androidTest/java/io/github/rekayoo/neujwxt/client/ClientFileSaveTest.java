package io.github.rekayoo.neujwxt.client;

import android.app.UiAutomation;
import android.os.ParcelFileDescriptor;
import android.view.accessibility.AccessibilityNodeInfo;
import android.webkit.WebView;
import androidx.test.core.app.ActivityScenario;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import io.github.rekayoo.neujwxt.shared.BaseShellActivity;
import io.github.rekayoo.neujwxt.shared.NativeFileRegistry;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.lang.reflect.Method;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.Test;
import org.junit.runner.RunWith;
import static org.junit.Assert.*;

@RunWith(AndroidJUnit4.class)
public class ClientFileSaveTest {
    private AccessibilityNodeInfo saveButton(UiAutomation ui) throws Exception {
        for (int attempt = 0; attempt < 100; attempt++) {
            AccessibilityNodeInfo root = ui.getRootInActiveWindow();
            if (root != null && root.getPackageName() != null
                && root.getPackageName().toString().endsWith(".documentsui")) {
                for (AccessibilityNodeInfo node : root.findAccessibilityNodeInfosByText("SAVE")) {
                    if (node.isClickable() && node.isEnabled()) return node;
                }
                for (AccessibilityNodeInfo node : root.findAccessibilityNodeInfosByViewId("android:id/button1")) {
                    if (node.isClickable() && node.isEnabled()) return node;
                }
            }
            Thread.sleep(100);
        }
        throw new AssertionError("System document picker save action never appeared");
    }

    private byte[] shell(UiAutomation ui, String command) throws Exception {
        try (InputStream input = new ParcelFileDescriptor.AutoCloseInputStream(ui.executeShellCommand(command));
             ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[65536];
            int count;
            while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
            return output.toByteArray();
        }
    }

    @Test public void systemPickerSavesStreamAndCancellationDiscardsTemporaryFile() throws Exception {
        new ServerConfigStore(InstrumentationRegistry.getInstrumentation().getTargetContext())
            .setServerUrl("https://offline.invalid/");
        UiAutomation ui = InstrumentationRegistry.getInstrumentation().getUiAutomation();
        android.accessibilityservice.AccessibilityServiceInfo info = ui.getServiceInfo();
        info.flags |= android.accessibilityservice.AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS;
        ui.setServiceInfo(info);
        String filename = "neu-integration-" + UUID.randomUUID() + ".bin";
        String path = "/sdcard/Download/" + filename;
        try (ActivityScenario<MainActivity> scenario = ActivityScenario.launch(MainActivity.class)) {
            boolean ready = false;
            for (int attempt = 0; attempt < 100; attempt++) {
                CountDownLatch done = new CountDownLatch(1);
                AtomicBoolean loaded = new AtomicBoolean();
                scenario.onActivity(activity -> {
                    WebView web = activity.findViewById(io.github.rekayoo.neujwxt.shared.R.id.webview);
                    web.evaluateJavascript("!!window.NeuNative", value -> {
                        loaded.set("true".equals(value));
                        done.countDown();
                    });
                });
                assertTrue(done.await(5, TimeUnit.SECONDS));
                if (loaded.get()) { ready = true; break; }
                Thread.sleep(100);
            }
            assertTrue("Bundled page was not ready", ready);
            AtomicReference<NativeFileRegistry> selected = new AtomicReference<>();
            scenario.onActivity(activity -> {
                try {
                    Method method = BaseShellActivity.class.getDeclaredMethod("nativeFiles");
                    method.setAccessible(true);
                    selected.set((NativeFileRegistry) method.invoke(activity));
                } catch (ReflectiveOperationException exception) {
                    throw new AssertionError(exception);
                }
            });
            NativeFileRegistry registry = selected.get();
            byte[] content = new byte[2 * 1024 * 1024 + 31];
            for (int index = 0; index < content.length; index++) content[index] = (byte) (index % 251);
            String token = registry.store(new ByteArrayInputStream(content));
            scenario.onActivity(activity -> activity.saveFile(filename, "application/octet-stream", "@native:" + token));
            assertTrue(saveButton(ui).performAction(AccessibilityNodeInfo.ACTION_CLICK));
            byte[] saved = new byte[0];
            for (int attempt = 0; attempt < 100; attempt++) {
                saved = shell(ui, "cat " + path);
                if (saved.length == content.length) break;
                Thread.sleep(100);
            }
            assertArrayEquals("System-selected document did not contain the complete stream", content, saved);
            assertEquals(0, registry.size(token));

            String canceled = registry.store(new ByteArrayInputStream(new byte[]{1, 2, 3}));
            scenario.onActivity(activity -> activity.saveFile("neu-canceled.bin", "application/octet-stream",
                "@native:" + canceled));
            saveButton(ui);
            shell(ui, "input keyevent KEYCODE_BACK");
            Thread.sleep(500);
            if (registry.size(canceled) != 0) shell(ui, "input keyevent KEYCODE_BACK");
            for (int attempt = 0; attempt < 100 && registry.size(canceled) != 0; attempt++) Thread.sleep(100);
            assertEquals("Canceled save left a claimable temporary file", 0, registry.size(canceled));
        } finally {
            shell(ui, "rm -f " + path);
        }
    }
}
