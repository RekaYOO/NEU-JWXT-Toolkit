package io.github.rekayoo.neujwxt.client;

import io.github.rekayoo.neujwxt.shared.CompanionJob;
import java.util.concurrent.TimeUnit;
import okhttp3.HttpUrl;
import okhttp3.OkHttpClient;

public final class MobileCompanionJob extends CompanionJob {
    @Override
    protected Connection connect() throws Exception {
        String address = new ServerConfigStore(this).getServerUrl();
        if (address.isEmpty()) return null;
        HttpUrl origin = ServerConfigStore.validate(address);
        EncryptedCookieJar jar = new EncryptedCookieJar(MainActivity.cookiePreferences(this));
        jar.readOnlyOrigin(origin);
        OkHttpClient client = new OkHttpClient.Builder().cookieJar(jar)
            .followRedirects(false).followSslRedirects(false).retryOnConnectionFailure(false)
            .callTimeout(10, TimeUnit.SECONDS).build();
        return new Connection(client, origin, null);
    }
}
