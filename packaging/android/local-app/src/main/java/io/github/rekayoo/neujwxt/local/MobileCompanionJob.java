package io.github.rekayoo.neujwxt.local;

import android.content.Context;
import io.github.rekayoo.neujwxt.shared.CompanionJob;
import java.util.concurrent.TimeUnit;
import okhttp3.HttpUrl;

public final class MobileCompanionJob extends CompanionJob {
    @Override
    protected Connection connect() throws Exception {
        boolean automationRequired = getSharedPreferences("mobile_runtime", Context.MODE_PRIVATE)
            .getBoolean("background_required", false);
        if (automationRequired && !LocalBackendService.isRunning()) return null;
        LocalBackendService.ensureRuntime(getApplicationContext());
        return new Connection(LocalBackendService.HTTP.newBuilder().callTimeout(10, TimeUnit.SECONDS).build(),
            HttpUrl.get(LocalBackendService.endpoint()), LocalBackendService.sessionToken());
    }
}
