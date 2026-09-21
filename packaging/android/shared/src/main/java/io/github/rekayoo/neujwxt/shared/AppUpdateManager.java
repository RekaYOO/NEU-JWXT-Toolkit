package io.github.rekayoo.neujwxt.shared;

import android.content.Intent;
import android.content.pm.PackageInfo;
import android.net.Uri;
import android.os.Build;
import android.provider.Settings;

import androidx.core.content.FileProvider;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.MessageDigest;
import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Official GitHub-only Android update checker and installer. */
public final class AppUpdateManager {
    private static final String API = "https://api.github.com/repos/RekaYOO/NEU-JWXT-Toolkit/releases/latest";
    private static final String MANIFEST = "release-manifest.json";
    private static final long MAX_BYTES = 1024L * 1024L * 1024L;
    private final BaseShellActivity activity;

    public AppUpdateManager(BaseShellActivity activity) { this.activity = activity; }

    public JSONObject check() throws Exception {
        PackageInfo info = activity.getPackageManager().getPackageInfo(activity.getPackageName(), 0);
        String current = info.versionName == null ? "0.0.0" : info.versionName;
        long currentCode = Build.VERSION.SDK_INT >= 28 ? info.getLongVersionCode() : info.versionCode;
        JSONObject result = new JSONObject()
            .put("current_version", current)
            .put("current_version_code", currentCode)
            .put("supported", isSupportedPackage(activity.getPackageName()));
        if (!isSupportedPackage(activity.getPackageName())) return result.put("available", false);

        JSONObject release = requestJson(API);
        String releaseUrl = release.optString("html_url", "https://github.com/RekaYOO/NEU-JWXT-Toolkit/releases/latest");
        String releaseTag = release.optString("tag_name", "");
        JSONArray assets = release.optJSONArray("assets");
        String manifestUrl = findAssetUrl(assets, MANIFEST);
        if (manifestUrl == null) throw new IOException("Release manifest is missing");
        JSONObject manifest = requestJson(manifestUrl);
        String latest = manifest.optString("version", "");
        long latestCode = manifest.optLong("android_version_code", 0);
        if (!releaseTag.equals("v" + latest) || !isSemVer(latest) || latestCode < 1) {
            throw new IOException("Release manifest version is invalid");
        }
        String key = activity.getPackageName().endsWith(".local") ? "android-local" : "android-client";
        JSONObject asset = manifest.optJSONObject("assets") == null ? null : manifest.optJSONObject("assets").optJSONObject(key);
        if (asset == null) throw new IOException("Android asset is missing");
        String name = asset.optString("name", "");
        String expectedName = "NEU-JWXT-Toolkit-" + latest + "-" + key + "-arm64.apk";
        String url = findAssetUrl(assets, name);
        String sha = asset.optString("sha256", "").toLowerCase(Locale.ROOT);
        if (!expectedName.equals(name) || url == null || !url.startsWith("https://github.com/") || !sha.matches("[0-9a-f]{64}")) {
            throw new IOException("Android asset metadata is invalid");
        }
        boolean available = newer(latest, current) && latestCode > currentCode;
        return result.put("latest_version", latest)
            .put("latest_version_code", latestCode)
            .put("available", available)
            .put("release_url", releaseUrl)
            .put("asset_name", name)
            .put("download_url", url)
            .put("sha256", sha);
    }

    public JSONObject downloadAndInstall() throws Exception {
        JSONObject metadata = check();
        if (!metadata.optBoolean("available", false)) return metadata;
        File directory = new File(activity.getCacheDir(), "updates");
        if (!directory.exists() && !directory.mkdirs()) throw new IOException("Unable to create update directory");
        File target = new File(directory, safeName(metadata.getString("asset_name")));
        File temporary = new File(target.getPath() + ".part");
        download(metadata.getString("download_url"), temporary);
        if (!metadata.getString("sha256").equalsIgnoreCase(sha256(temporary))) {
            temporary.delete();
            throw new IOException("APK 校验失败");
        }
        if (!temporary.renameTo(target)) throw new IOException("无法保存 APK");
        activity.runOnUiThread(() -> launchInstaller(target));
        return metadata.put("downloaded", true);
    }

    private void launchInstaller(File apk) {
        if (Build.VERSION.SDK_INT >= 26 && !activity.getPackageManager().canRequestPackageInstalls()) {
            Intent settings = new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                Uri.parse("package:" + activity.getPackageName()));
            activity.startActivity(settings);
            return;
        }
        Uri uri = FileProvider.getUriForFile(activity,
            activity.getPackageName() + ".fileprovider", apk);
        Intent intent = new Intent(Intent.ACTION_VIEW)
            .setDataAndType(uri, "application/vnd.android.package-archive")
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK);
        try { activity.startActivity(intent); }
        catch (Exception exception) { activity.showError("系统安装器不可用，请从 Release 页面手动安装"); }
    }

    private static boolean isSupportedPackage(String name) {
        return "io.github.rekayoo.neujwxt.client".equals(name)
            || "io.github.rekayoo.neujwxt.local".equals(name);
    }

    private static String findAssetUrl(JSONArray assets, String name) {
        if (assets == null) return null;
        for (int i = 0; i < assets.length(); i++) {
            JSONObject item = assets.optJSONObject(i);
            if (item != null && name.equals(item.optString("name"))) return item.optString("browser_download_url", null);
        }
        return null;
    }

    private static JSONObject requestJson(String url) throws Exception {
        byte[] body = request(url, 4 * 1024 * 1024);
        return new JSONObject(new String(body, java.nio.charset.StandardCharsets.UTF_8));
    }

    private static byte[] request(String source, long max) throws Exception {
        if (!source.startsWith("https://api.github.com/") && !source.startsWith("https://github.com/")) throw new IOException("不允许的更新地址");
        HttpURLConnection connection = (HttpURLConnection) new URL(source).openConnection();
        connection.setConnectTimeout(12000); connection.setReadTimeout(30000);
        connection.setRequestProperty("Accept", "application/vnd.github+json");
        connection.setRequestProperty("User-Agent", "NEU-JWXT-Toolkit-Android");
        int responseCode = connection.getResponseCode();
        if (!allowedHost(connection.getURL().getHost())
            || responseCode < 200 || responseCode >= 300) throw new IOException("GitHub Release 请求失败");
        try (java.io.InputStream input = connection.getInputStream(); java.io.ByteArrayOutputStream output = new java.io.ByteArrayOutputStream()) {
            byte[] buffer = new byte[65536]; int count; long total = 0;
            while ((count = input.read(buffer)) != -1) { total += count; if (total > max) throw new IOException("更新文件过大"); output.write(buffer, 0, count); }
            return output.toByteArray();
        } finally { connection.disconnect(); }
    }

    private static void download(String source, File target) throws Exception {
        if (!source.startsWith("https://github.com/")) throw new IOException("不允许的更新地址");
        HttpURLConnection connection = (HttpURLConnection) new URL(source).openConnection();
        connection.setConnectTimeout(12000); connection.setReadTimeout(30000);
        connection.setRequestProperty("User-Agent", "NEU-JWXT-Toolkit-Android");
        int responseCode = connection.getResponseCode();
        if (!allowedHost(connection.getURL().getHost())
            || responseCode < 200 || responseCode >= 300) throw new IOException("APK 下载失败");
        try (java.io.InputStream input = connection.getInputStream(); FileOutputStream output = new FileOutputStream(target)) {
            byte[] buffer = new byte[65536]; int count; long total = 0;
            while ((count = input.read(buffer)) != -1) { total += count; if (total > MAX_BYTES) throw new IOException("APK 文件过大"); output.write(buffer, 0, count); }
            output.flush();
        } finally { connection.disconnect(); }
    }

    private static String sha256(File file) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        try (FileInputStream input = new FileInputStream(file)) { byte[] buffer = new byte[65536]; int count; while ((count = input.read(buffer)) != -1) digest.update(buffer, 0, count); }
        StringBuilder result = new StringBuilder(); for (byte value : digest.digest()) result.append(String.format(Locale.ROOT, "%02x", value)); return result.toString();
    }

    private static String safeName(String value) { return value.replaceAll("[^A-Za-z0-9._-]", "_"); }

    private static boolean allowedHost(String host) {
        return "api.github.com".equalsIgnoreCase(host)
            || "github.com".equalsIgnoreCase(host)
            || "release-assets.githubusercontent.com".equalsIgnoreCase(host)
            || (host != null && host.toLowerCase(Locale.ROOT).endsWith(".githubusercontent.com"));
    }

    private static boolean isSemVer(String value) {
        return Pattern.matches("^(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:\\+[0-9A-Za-z.-]+)?$", value);
    }

    private static boolean newer(String remote, String current) {
        return compareVersions(remote, current) > 0;
    }

    private static int compareVersions(String left, String right) {
        try {
            if (!isSemVer(left) || !isSemVer(right)) return Integer.MIN_VALUE;
            Matcher a = Pattern.compile("^(\\d+)\\.(\\d+)\\.(\\d+)(?:-([^+]+))?(?:\\+.*)?$").matcher(left);
            Matcher b = Pattern.compile("^(\\d+)\\.(\\d+)\\.(\\d+)(?:-([^+]+))?(?:\\+.*)?$").matcher(right);
            if (!a.matches() || !b.matches()) return Integer.MIN_VALUE;
            for (int i = 1; i <= 3; i++) {
                int result = Integer.compare(Long.parseLong(a.group(i)) > Integer.MAX_VALUE ? Integer.MAX_VALUE : Integer.parseInt(a.group(i)),
                    Long.parseLong(b.group(i)) > Integer.MAX_VALUE ? Integer.MAX_VALUE : Integer.parseInt(b.group(i)));
                if (result != 0) return result;
            }
            String[] ap = a.group(4) == null ? new String[0] : a.group(4).split("\\.");
            String[] bp = b.group(4) == null ? new String[0] : b.group(4).split("\\.");
            if (ap.length == 0 && bp.length == 0) return 0;
            if (ap.length == 0) return 1;
            if (bp.length == 0) return -1;
            for (int i = 0; i < Math.min(ap.length, bp.length); i++) {
                String x = ap[i], y = bp[i];
                boolean xn = x.matches("0|[1-9][0-9]*"), yn = y.matches("0|[1-9][0-9]*");
                if (xn && yn) { int result = Integer.compare(Integer.parseInt(x), Integer.parseInt(y)); if (result != 0) return result; }
                else if (xn != yn) return xn ? -1 : 1;
                else { int result = x.compareTo(y); if (result != 0) return result; }
            }
            return Integer.compare(ap.length, bp.length);
        } catch (Exception ignored) { return Integer.MIN_VALUE; }
    }
}
