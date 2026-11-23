package io.github.rekayoo.neujwxt.client;

import android.os.SystemClock;
import android.widget.EditText;
import androidx.test.core.app.ActivityScenario;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import org.junit.Test;
import org.junit.runner.RunWith;
import static androidx.test.espresso.Espresso.onView;
import static androidx.test.espresso.action.ViewActions.click;
import static androidx.test.espresso.action.ViewActions.closeSoftKeyboard;
import static androidx.test.espresso.action.ViewActions.replaceText;
import static androidx.test.espresso.matcher.ViewMatchers.isAssignableFrom;
import static androidx.test.espresso.matcher.ViewMatchers.withText;
import static org.junit.Assert.assertEquals;
import static org.junit.Assume.assumeNotNull;

@RunWith(AndroidJUnit4.class)
public class TrustedServerTest {
    @Test public void savesTrustedServerAfterProbeAndReopens() {
        String endpoint = InstrumentationRegistry.getArguments().getString("toolboxHttpsOrigin");
        assumeNotNull(endpoint);
        String expected = ServerConfigStore.validate(endpoint).toString();
        ServerConfigStore config = new ServerConfigStore(InstrumentationRegistry.getInstrumentation().getTargetContext());
        String original = config.getServerUrl();
        try {
            config.setServerUrl("");
            try (ActivityScenario<MainActivity> scenario = ActivityScenario.launch(MainActivity.class)) {
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
