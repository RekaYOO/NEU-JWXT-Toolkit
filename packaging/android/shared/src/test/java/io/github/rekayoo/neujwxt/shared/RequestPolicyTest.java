package io.github.rekayoo.neujwxt.shared;

import org.junit.Test;
import static org.junit.Assert.*;

public class RequestPolicyTest {
    @Test public void onlyRelativeApiPaths() {
        assertTrue(RequestPolicy.isApiPath("/api/health"));
        assertTrue(RequestPolicy.isApiPath("/api/search?q=https%3A%2F%2Fexample.com"));
        for (String path : new String[]{
            "https://evil.test/api/health", "//evil.test/api/health", "/api/../health",
            "/api/%2e./health", "/api/%252e%252e/health", "/api/a%2fb", "/api/a%5cb",
            "/api/a\\b", "/api/a#fragment", "/api/a%00b", "/api/./health",
            "/api/a\nb", "/assets/index.html"
        }) assertFalse(path, RequestPolicy.isApiPath(path));
    }

    @Test public void stripsPrivilegedHeaders() throws Exception {
        NativeRequest request = NativeRequest.parse(
            "{\"path\":\"/api/health\",\"headers\":{\"Cookie\":\"secret\",\"HOST\":\"evil\","
            + "\"X-NEU-Mobile-Token\":\"forged\",\"Content-Length\":\"42\",\"Accept\":\"application/json\"}}");
        assertEquals(1, request.headers.size());
        assertEquals("application/json", request.headers.get("Accept"));
    }
}
