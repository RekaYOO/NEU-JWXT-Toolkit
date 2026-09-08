package io.github.rekayoo.neujwxt.client;

import android.webkit.WebView;
import androidx.test.core.app.ActivityScenario;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import org.junit.Test;
import org.junit.runner.RunWith;
import static org.junit.Assert.*;

@RunWith(AndroidJUnit4.class)
public class ClientLaunchTest {
    @Test public void bundledPageStartsEvenWhenServerIsUnreachable() throws Exception {
        ServerConfigStore config = new ServerConfigStore(
            InstrumentationRegistry.getInstrumentation().getTargetContext());
        config.setServerUrl("https://offline.invalid/");
        try (ActivityScenario<MainActivity> scenario = ActivityScenario.launch(MainActivity.class)) {
            for (int attempt = 0; attempt < 60; attempt++) {
                CountDownLatch done = new CountDownLatch(1);
                AtomicBoolean rendered = new AtomicBoolean();
                scenario.onActivity(activity -> {
                    WebView web = activity.findViewById(io.github.rekayoo.neujwxt.shared.R.id.webview);
                    web.evaluateJavascript(
                        "location.origin === 'https://appassets.androidplatform.net' && "
                        + "!!window.NeuNative && !!document.querySelector('#root > *') && "
                        + "document.body.innerText.trim().length > 20 && "
                        + "performance.getEntriesByType('resource').filter(r => "
                        + "r.initiatorType === 'script' || r.initiatorType === 'link').every(r => "
                        + "r.name.startsWith(location.origin + '/'))",
                        value -> { rendered.set("true".equals(value)); done.countDown(); });
                });
                assertTrue(done.await(5, TimeUnit.SECONDS));
                if (rendered.get()) return;
                Thread.sleep(500);
            }
            fail("Bundled React application never rendered without a reachable server");
        }
    }
}
