package io.github.rekayoo.neujwxt.shared;

import java.net.URI;
import java.net.URISyntaxException;
import java.util.Locale;

/** Shared validation runs before any URL resolution or privileged transport access. */
public final class RequestPolicy {
    private RequestPolicy() {}

    public static boolean isApiPath(String value) {
        if (value == null || !value.startsWith("/api/") || value.contains("\\")) return false;
        try {
            URI uri = new URI(value);
            if (uri.isAbsolute() || uri.getRawAuthority() != null || uri.getRawFragment() != null) return false;
            String path = uri.getRawPath().toLowerCase(Locale.ROOT);
            // Reject encoded separators, nested escaping and dot segments, including mixed encodings.
            if (path.contains("%2f") || path.contains("%5c") || path.contains("%25")) return false;
            path = path.replace("%2e", ".");
            for (String segment : path.split("/", -1)) {
                if (segment.equals(".") || segment.equals("..")) return false;
            }
            return !uri.getPath().matches(".*[\\p{Cntrl}].*");
        } catch (URISyntaxException exception) {
            return false;
        }
    }

    public static boolean allowsHeader(String name) {
        String lower = name.toLowerCase(Locale.ROOT);
        return !lower.equals("host") && !lower.equals("cookie")
            && !lower.equals("cookie2") && !lower.equals("content-length")
            && !lower.equals("connection") && !lower.equals("transfer-encoding")
            && !lower.equals("origin") && !lower.equals("referer")
            && !lower.startsWith("proxy-") && !lower.startsWith("sec-")
            && !lower.equals("x-neu-mobile-token");
    }
}
