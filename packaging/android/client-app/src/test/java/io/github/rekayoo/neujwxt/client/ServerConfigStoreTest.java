package io.github.rekayoo.neujwxt.client;

import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertThrows;

public final class ServerConfigStoreTest {
    @Test
    public void acceptsOnlyCleanHttpsRoot() {
        assertEquals("https://example.com/", ServerConfigStore.validate("https://example.com").toString());
        assertEquals("https://example.com:8443/", ServerConfigStore.validate("https://example.com:8443/").toString());
    }

    @Test
    public void rejectsUnsafeOrAmbiguousServerUrls() {
        assertThrows(IllegalArgumentException.class, () -> ServerConfigStore.validate("http://example.com"));
        assertThrows(IllegalArgumentException.class, () -> ServerConfigStore.validate("https://user:pass@example.com"));
        assertThrows(IllegalArgumentException.class, () -> ServerConfigStore.validate("https://example.com/subpath"));
        assertThrows(IllegalArgumentException.class, () -> ServerConfigStore.validate("https://example.com/?token=x"));
        assertThrows(IllegalArgumentException.class, () -> ServerConfigStore.validate("https://example.com/#fragment"));
    }
}
