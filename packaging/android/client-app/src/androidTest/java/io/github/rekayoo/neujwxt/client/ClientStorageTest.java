package io.github.rekayoo.neujwxt.client;

import android.content.Context;
import android.content.SharedPreferences;
import androidx.security.crypto.EncryptedSharedPreferences;
import androidx.security.crypto.MasterKey;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import java.util.Collections;
import okhttp3.Cookie;
import okhttp3.HttpUrl;
import org.junit.Test;
import org.junit.runner.RunWith;
import static org.junit.Assert.*;

@RunWith(AndroidJUnit4.class)
public class ClientStorageTest {
    private SharedPreferences preferences() throws Exception {
        Context context = InstrumentationRegistry.getInstrumentation().getTargetContext();
        MasterKey key = new MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build();
        return EncryptedSharedPreferences.create(context, "test_cookies", key,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM);
    }

    @Test public void encryptedCookiesSurviveRecreationAndRemainOriginScoped() throws Exception {
        SharedPreferences preferences = preferences();
        preferences.edit().clear().commit();
        HttpUrl origin = HttpUrl.get("https://server.example/");
        EncryptedCookieJar first = new EncryptedCookieJar(preferences);
        first.setOrigin(origin);
        Cookie cookie = Cookie.parse(origin, "session=fake-test-session; Path=/; Secure; HttpOnly; Max-Age=3600");
        first.saveFromResponse(origin, Collections.singletonList(cookie));
        first.invalidate();
        EncryptedCookieJar restored = new EncryptedCookieJar(preferences());
        restored.setOrigin(origin);
        assertEquals("fake-test-session", restored.loadForRequest(origin).get(0).value());
        assertTrue(restored.loadForRequest(HttpUrl.get("https://server.example:444/")).isEmpty());
        assertTrue(restored.loadForRequest(HttpUrl.get("https://sub.server.example/")).isEmpty());
        restored.setOrigin(HttpUrl.get("https://other.example/"));
        restored.saveFromResponse(origin, Collections.singletonList(cookie));
        restored.setOrigin(origin);
        assertTrue(restored.loadForRequest(origin).isEmpty());
    }

    @Test public void lateResponsesCannotRestoreClearedLogin() throws Exception {
        SharedPreferences preferences = preferences();
        preferences.edit().clear().commit();
        HttpUrl origin = HttpUrl.get("https://server.example/");
        EncryptedCookieJar previous = new EncryptedCookieJar(preferences);
        previous.setOrigin(origin);
        Cookie cookie = Cookie.parse(origin, "session=stale-test-session; Path=/; Secure; HttpOnly");
        previous.saveFromResponse(origin, Collections.singletonList(cookie));
        previous.clear();
        EncryptedCookieJar current = new EncryptedCookieJar(preferences);
        current.setOrigin(origin);
        previous.saveFromResponse(origin, Collections.singletonList(cookie));
        assertTrue(current.loadForRequest(origin).isEmpty());
    }
}
