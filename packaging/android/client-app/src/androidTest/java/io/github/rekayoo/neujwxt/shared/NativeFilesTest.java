package io.github.rekayoo.neujwxt.shared;

import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.security.MessageDigest;
import org.junit.Test;
import org.junit.runner.RunWith;
import static org.junit.Assert.*;

@RunWith(AndroidJUnit4.class)
public class NativeFilesTest {
    private NativeFileRegistry registry() {
        return new NativeFileRegistry(InstrumentationRegistry.getInstrumentation().getTargetContext());
    }

    @Test public void largeDownloadSurvivesRegistryRecreationAndIsConsumedOnce() throws Exception {
        byte[] content = new byte[2 * 1024 * 1024 + 31];
        for (int index = 0; index < content.length; index++) content[index] = (byte) (index % 251);
        NativeFileRegistry original = registry();
        String token = original.store(new ByteArrayInputStream(content));
        NativeFileRegistry recreated = registry();
        assertEquals(content.length, recreated.size(token));
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        try (InputStream input = recreated.claim(token)) {
            byte[] buffer = new byte[8192];
            int count;
            while ((count = input.read(buffer)) != -1) digest.update(buffer, 0, count);
        }
        assertArrayEquals(MessageDigest.getInstance("SHA-256").digest(content), digest.digest());
        assertEquals(0, original.size(token));
        try {
            original.claim(token);
            fail("Download token was reused");
        } catch (IOException expected) {}
    }

    @Test public void generatedFileChunksAreBoundedAndAbortDeletesPendingFile() throws Exception {
        NativeFileRegistry registry = registry();
        String token = registry.beginUpload();
        registry.appendUpload(token, new byte[65536]);
        registry.appendUpload(token, new byte[]{1, 2, 3});
        assertEquals(65539, registry.size(token));
        try {
            registry.appendUpload(token, new byte[65537]);
            fail("Oversized bridge chunk was accepted");
        } catch (IOException expected) {}
        assertEquals(65539, registry.size(token));
        registry.discard(token);
        assertEquals(0, registry.size(token));
        try {
            registry.appendUpload(token, new byte[]{4});
            fail("Aborted upload was restored");
        } catch (IOException expected) {}
    }
}
