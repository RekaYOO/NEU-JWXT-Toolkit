package io.github.rekayoo.neujwxt.shared;

import android.annotation.SuppressLint;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.os.Build;
import android.provider.Settings;
import android.util.Base64;
import android.view.View;
import android.webkit.MimeTypeMap;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.WebChromeClient;
import android.webkit.ValueCallback;

import androidx.annotation.Nullable;
import androidx.appcompat.app.AppCompatActivity;
import androidx.webkit.WebViewAssetLoader;

import org.json.JSONObject;

import java.io.FileNotFoundException;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.ByteArrayInputStream;
import java.util.HashMap;
import java.util.Map;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public abstract class BaseShellActivity extends AppCompatActivity {
    public static final String ASSET_ORIGIN = "https://appassets.androidplatform.net";
    private static final int SAVE_REQUEST = 8102;
    private static final int OPEN_REQUEST = 8104;

    protected WebView webView;
    private ApiTransport transport;
    private byte[] pendingFile;
    private String pendingNativeToken;
    private NativeFileRegistry nativeFiles;
    private ValueCallback<Uri[]> fileChooser;
    private boolean choosingSave;
    private boolean initialized;
    private final ExecutorService fileWorker = Executors.newSingleThreadExecutor();

    protected abstract void prepareShell(Runnable ready);
    protected abstract ApiTransport createTransport();
    protected abstract JSONObject createShellInfo();

    protected WebViewAssetLoader.PathHandler createAssetPathHandler() {
        return new WebViewAssetLoader.AssetsPathHandler(this);
    }

    protected InputStream openWebAsset(String path) throws IOException {
        return getAssets().open(path);
    }

    @Override
    protected void onCreate(@Nullable Bundle state) {
        super.onCreate(state);
        setContentView(R.layout.activity_shell);
        findViewById(R.id.startup_retry).setOnClickListener(view -> recreate());
        webView = findViewById(R.id.webview);
        if (state != null) {
            pendingFile = state.getByteArray("pendingFile");
            pendingNativeToken = state.getString("pendingNativeToken");
            choosingSave = state.getBoolean("choosingSave");
        }
        View content = findViewById(android.R.id.content);
        androidx.core.view.ViewCompat.setOnApplyWindowInsetsListener(content, (view, insets) -> {
            androidx.core.graphics.Insets bars = insets.getInsets(
                androidx.core.view.WindowInsetsCompat.Type.systemBars()
                | androidx.core.view.WindowInsetsCompat.Type.ime());
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom);
            return insets;
        });
        prepareShell(() -> runOnUiThread(this::initializeWebView));
    }

    @SuppressLint({"SetJavaScriptEnabled", "JavascriptInterface"})
    private void initializeWebView() {
        if (isFinishing() || isDestroyed() || initialized) return;
        initialized = true;
        transport = createTransport();
        nativeFiles = nativeFiles == null ? new NativeFileRegistry(this) : nativeFiles;
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setAllowFileAccessFromFileURLs(false);
        settings.setAllowUniversalAccessFromFileURLs(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) settings.setSafeBrowsingEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(true);
        webView.setBackgroundColor(Color.WHITE);

        android.webkit.CookieManager cookies = android.webkit.CookieManager.getInstance();
        cookies.setAcceptCookie(false);
        cookies.setAcceptThirdPartyCookies(webView, false);

        WebViewAssetLoader loader = new WebViewAssetLoader.Builder()
            .addPathHandler("/", createAssetPathHandler())
            .build();
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                if (isAssetOrigin(uri)) {
                    return false;
                }
                if (!request.isForMainFrame()) return true;
                String scheme = uri.getScheme();
                if (!"https".equals(scheme) && !"http".equals(scheme)
                    && !"mailto".equals(scheme) && !"tel".equals(scheme)) return true;
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, uri));
                } catch (ActivityNotFoundException ignored) {
                    showError("无法打开外部链接");
                }
                return true;
            }

            @Override
            public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                if (!isAssetOrigin(uri) || !"GET".equals(request.getMethod())) return blocked();
                // AssetLoader returns a non-null 404 for absent routes, so SPA routing comes first.
                if (request.isForMainFrame() && !lastPathSegment(uri).contains(".")
                    && !uri.getPath().startsWith("/api/")) {
                    try {
                        return secured(new WebResourceResponse(
                            "text/html", "UTF-8", openWebAsset("index.html")
                        ));
                    } catch (IOException ignored) {
                        return blocked();
                    }
                }
                WebResourceResponse response = loader.shouldInterceptRequest(uri);
                return response == null ? blocked() : secured(response);
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                findViewById(R.id.progress).setVisibility(View.GONE);
            }
        });
        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                                             FileChooserParams params) {
                if (fileChooser != null) fileChooser.onReceiveValue(null);
                fileChooser = callback;
                Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT)
                    .addCategory(Intent.CATEGORY_OPENABLE).setType("*/*")
                    .putExtra(Intent.EXTRA_ALLOW_MULTIPLE,
                        params.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE);
                try {
                    startActivityForResult(intent, OPEN_REQUEST);
                } catch (ActivityNotFoundException exception) {
                    fileChooser.onReceiveValue(null);
                    fileChooser = null;
                }
                return true;
            }
        });
        webView.addJavascriptInterface(
            new NativeBridge(this, webView, () -> transport, this::createShellInfo),
            "NeuNative"
        );
        loadRoute(getIntent());
    }

    private static boolean isAssetOrigin(Uri uri) {
        return "https".equals(uri.getScheme()) && "appassets.androidplatform.net".equals(uri.getHost())
            && (uri.getPort() == -1 || uri.getPort() == 443) && uri.getUserInfo() == null;
    }

    private static WebResourceResponse secured(WebResourceResponse response) {
        Map<String, String> headers = new HashMap<>();
        if (response.getResponseHeaders() != null) headers.putAll(response.getResponseHeaders());
        headers.put("Content-Security-Policy", "default-src 'self'; script-src 'self'; "
            + "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; "
            + "connect-src 'none'; frame-src 'none'; frame-ancestors 'none'; object-src 'none'; "
            + "base-uri 'none'; form-action 'none'");
        headers.put("X-Content-Type-Options", "nosniff");
        response.setResponseHeaders(headers);
        return response;
    }

    private static WebResourceResponse blocked() {
        return new WebResourceResponse("text/plain", "UTF-8", 403, "Forbidden",
            java.util.Collections.emptyMap(), new ByteArrayInputStream(new byte[0]));
    }

    private void loadRoute(Intent intent) {
        String route = intent.getStringExtra("route");
        Uri uri = Uri.parse(ASSET_ORIGIN + (route == null ? "/" : route));
        if (route == null || !route.startsWith("/") || route.startsWith("//")
            || route.contains("\\") || !isAssetOrigin(uri)) route = "/";
        webView.loadUrl(ASSET_ORIGIN + route);
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        if (initialized) loadRoute(intent);
    }

    private static String lastPathSegment(Uri uri) {
        String segment = uri.getLastPathSegment();
        return segment == null ? "" : segment.toLowerCase(Locale.ROOT);
    }

    public void replaceTransport() {
        if (!initialized) return;
        if (transport != null) transport.close();
        transport = createTransport();
        if (webView != null) webView.reload();
    }

    protected void clearSessionAndReload() {
        if (!initialized) return;
        if (transport != null) transport.close();
        // Keep the old page on a closed transport until its JavaScript context is destroyed.
        webView.evaluateJavascript("sessionStorage.clear()", ignored -> {
            if (!isFinishing() && !isDestroyed()) recreate();
        });
    }

    @Override
    protected void onSaveInstanceState(Bundle state) {
        super.onSaveInstanceState(state);
        state.putByteArray("pendingFile", pendingFile);
        state.putString("pendingNativeToken", pendingNativeToken);
        state.putBoolean("choosingSave", choosingSave);
    }

    public void saveFile(String filename, String mediaType, String base64) {
        if (choosingSave) {
            showError("请先完成当前文件保存");
            return;
        }
        pendingNativeToken = null;
        if (base64 != null && base64.startsWith("@native:")) {
            pendingNativeToken = base64.substring("@native:".length());
            pendingFile = null;
        } else {
        try {
            pendingFile = Base64.decode(base64, Base64.DEFAULT);
        } catch (IllegalArgumentException | NullPointerException exception) {
            showError("文件数据无效");
            return;
        }
        }
        Intent intent = new Intent(Intent.ACTION_CREATE_DOCUMENT)
            .addCategory(Intent.CATEGORY_OPENABLE)
            .setType(mediaType == null || mediaType.trim().isEmpty() ? "application/octet-stream"
                : mediaType.split(";")[0])
            .putExtra(Intent.EXTRA_TITLE, safeFilename(filename));
        try {
            choosingSave = true;
            startActivityForResult(intent, SAVE_REQUEST);
        } catch (ActivityNotFoundException exception) {
            choosingSave = false;
            pendingFile = null;
            pendingNativeToken = null;
            showError("系统文件选择器不可用");
        }
    }

    private static String safeFilename(String value) {
        String filename = value == null ? "download" : value.replaceAll("[\\\\/:*?\"<>|\\p{Cntrl}]", "_");
        return filename.trim().isEmpty() ? "download" : filename;
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, @Nullable Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == OPEN_REQUEST) {
            if (fileChooser != null) {
                fileChooser.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(resultCode, data));
                fileChooser = null;
            }
            return;
        }
        if (requestCode != SAVE_REQUEST) return;
        choosingSave = false;
        nativeFiles();
        byte[] content = pendingFile;
        String nativeToken = pendingNativeToken;
        pendingFile = null;
        pendingNativeToken = null;
        if (resultCode != RESULT_OK || data == null || data.getData() == null
            || (content == null && nativeToken == null)) {
            if (nativeToken != null) {
                try (InputStream ignored = nativeFiles.claim(nativeToken)) {
                    // Closing a claimed temporary file deletes it.
                } catch (IOException ignored) {}
            }
            return;
        }
        Uri destination = data.getData();
        fileWorker.execute(() -> {
        try (InputStream nativeInput = nativeToken == null ? null : nativeFiles.claim(nativeToken);
             OutputStream stream = getContentResolver().openOutputStream(destination)) {
            if (stream == null) throw new FileNotFoundException();
            if (nativeInput == null) stream.write(content);
            else {
                byte[] buffer = new byte[65536];
                int count;
                while ((count = nativeInput.read(buffer)) >= 0) stream.write(buffer, 0, count);
            }
        } catch (IOException | SecurityException exception) {
            showError("文件保存失败");
        }
        });
    }

    protected NativeFileRegistry nativeFiles() {
        if (nativeFiles == null) nativeFiles = new NativeFileRegistry(this);
        return nativeFiles;
    }

    public void openServerSettings() {
        startActivity(new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
            .setData(Uri.parse("package:" + getPackageName())));
    }

    protected void showError(String message) {
        runOnUiThread(() -> {
            if (isFinishing() || isDestroyed()) return;
            new AlertDialog.Builder(this)
            .setTitle("NEU 工具箱")
            .setMessage(message)
            .setPositiveButton("确定", null)
            .show();
        });
    }

    protected void showStartupError(String message) {
        runOnUiThread(() -> {
            if (isFinishing() || isDestroyed()) return;
            findViewById(R.id.progress).setVisibility(View.GONE);
            webView.setVisibility(View.GONE);
            findViewById(R.id.startup_error).setVisibility(View.VISIBLE);
            ((android.widget.TextView) findViewById(R.id.startup_error_message)).setText(message);
        });
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) webView.goBack();
        else super.onBackPressed();
    }

    @Override
    protected void onDestroy() {
        if (transport != null) transport.close();
        if (fileChooser != null) fileChooser.onReceiveValue(null);
        fileWorker.shutdown();
        if (webView != null) {
            webView.removeJavascriptInterface("NeuNative");
            webView.destroy();
        }
        super.onDestroy();
    }
}
