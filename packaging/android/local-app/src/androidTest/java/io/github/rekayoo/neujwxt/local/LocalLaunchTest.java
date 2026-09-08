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
            Python.getInstance().getModule("Crypto.Cipher.AES").callAttr(
                "new", new byte[16], 1).callAttr("encrypt", new byte[16]);
            for (int attempt = 0; attempt < 60; attempt++) {
                CountDownLatch done = new CountDownLatch(1);
                AtomicBoolean rendered = new AtomicBoolean();
                scenario.onActivity(activity -> {
                    WebView web = activity.findViewById(io.github.rekayoo.neujwxt.shared.R.id.webview);
                    web.evaluateJavascript(
                        "location.origin === 'https://appassets.androidplatform.net' && "
                        + "!!document.querySelector('#root > *') && document.body.innerText.trim().length > 20",
                        value -> { rendered.set("true".equals(value)); done.countDown(); });
                });
                assertTrue(done.await(5, TimeUnit.SECONDS));
                if (rendered.get()) return;
                Thread.sleep(500);
            }
            fail("Local React application never rendered");
        }
    }
}
