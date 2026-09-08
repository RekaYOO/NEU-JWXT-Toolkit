package io.github.rekayoo.neujwxt.shared;

import org.json.JSONObject;

public interface ApiTransport {
    interface Callback {
        void complete(JSONObject payload);
    }

    void request(String id, NativeRequest request, Callback callback);
    void cancel(String id);
    default void close() {}
}
