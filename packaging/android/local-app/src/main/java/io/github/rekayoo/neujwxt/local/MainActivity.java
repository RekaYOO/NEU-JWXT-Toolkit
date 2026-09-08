package io.github.rekayoo.neujwxt.local;

import android.content.Intent;
import android.provider.Settings;
import android.net.Uri;

import androidx.core.content.ContextCompat;

import org.json.JSONObject;

import java.util.Collections;
import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.io.IOException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import io.github.rekayoo.neujwxt.shared.ApiTransport;
import io.github.rekayoo.neujwxt.shared.BaseShellActivity;
import io.github.rekayoo.neujwxt.shared.OkHttpTransport;
import okhttp3.HttpUrl;
import okhttp3.OkHttpClient;
import androidx.webkit.WebViewAssetLoader;

public final class MainActivity extends BaseShellActivity {
    private File webRuntime;
    private Runnable readyCallback;
    private final ExecutorService preparation = Executors.newSingleThreadExecutor();

    @Override
    protected void prepareShell(Runnable ready) {
        android.content.Context application = getApplicationContext();
        preparation.execute(() -> {
            try {
                android.content.pm.PackageInfo info = application.getPackageManager()
                    .getPackageInfo(application.getPackageName(), 0);
                File installed = EmbeddedAssets.install(application, info.versionName + "-" + info.versionCode);
                runOnUiThread(() -> {
                    if (isDestroyed() || isFinishing()) return;
                    webRuntime = installed;
                    startBackend(ready);
                });
            } catch (Exception exception) {
                runOnUiThread(() -> {
                    if (!isDestroyed() && !isFinishing()) showStartupError("内置页面准备失败");
                });
            }
        });
    }

    private void startBackend(Runnable ready) {
        Intent service = new Intent(this, LocalBackendService.class);
        try {
            ContextCompat.startForegroundService(this, service);
        } catch (RuntimeException exception) {
            showStartupError("系统暂不允许启动本地服务");
            return;
        }
        readyCallback = () -> {
            if (isDestroyed() || isFinishing()) return;
            if (LocalBackendService.endpoint() == null) {
                showStartupError("本地服务启动失败，请重试");
            } else ready.run();
        };
        LocalBackendService.whenReady(readyCallback);
    }

    @Override
    protected ApiTransport createTransport() {
        String endpoint = LocalBackendService.endpoint();
        if (endpoint == null) throw new IllegalStateException("本地服务尚未启动");
        OkHttpTransport transport = new OkHttpTransport(
            new OkHttpClient.Builder().proxy(java.net.Proxy.NO_PROXY).followRedirects(false).build(),
            HttpUrl.get(endpoint),
            Collections.singletonMap("X-NEU-Mobile-Token", LocalBackendService.sessionToken()),
            nativeFiles()
        );
        return new PermissionGuardTransport(this, transport);
    }

    @Override
    protected JSONObject createShellInfo() {
        try {
            return new JSONObject().put("kind", "local").put("server_url", "本机");
        } catch (Exception impossible) {
            return new JSONObject();
        }
    }

    @Override
    protected WebViewAssetLoader.PathHandler createAssetPathHandler() {
        return new WebViewAssetLoader.InternalStoragePathHandler(this, webRuntime);
    }

    @Override
    protected InputStream openWebAsset(String path) throws IOException {
        return new FileInputStream(new File(webRuntime, path));
    }

    @Override
    public void openServerSettings() {
        startActivity(new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
            .setData(Uri.parse("package:" + getPackageName())));
    }

    @Override
    protected void onDestroy() {
        preparation.shutdown();
        LocalBackendService.removeReadyCallback(readyCallback);
        super.onDestroy();
    }
}
