package io.github.rekayoo.neujwxt.local;

import android.content.Context;
import android.content.res.AssetManager;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;

final class EmbeddedAssets {
    private static final Set<String> WEB_ROOTS = new HashSet<>(Arrays.asList(
        "index.html", "asset-manifest.json", "manifest.webmanifest", "favicon.ico",
        "favicon-16.png", "favicon-32.png", "apple-touch-icon.png", "icon-192.png",
        "icon-512.png", "static"
    ));

    private EmbeddedAssets() {}

    static synchronized File install(Context context, String version) throws IOException {
        File parent = new File(context.getFilesDir(), "web-runtime");
        File target = new File(parent, safeVersion(version));
        File marker = new File(target, ".complete");
        if (marker.isFile() && new File(target, "index.html").isFile()) return target;
        if (!parent.exists() && !parent.mkdirs()) throw new IOException("Unable to create web runtime directory");
        File temporary = new File(parent, safeVersion(version) + ".tmp");
        delete(temporary);
        if (!temporary.mkdirs()) throw new IOException("Unable to create temporary web runtime directory");
        AssetManager assets = context.getAssets();
        String[] roots = assets.list("");
        if (roots == null) throw new IOException("Embedded web assets are missing");
        for (String root : roots) {
            if (!WEB_ROOTS.contains(root)) continue;
            String[] children = assets.list(root);
            if (children == null) continue;
            if (children.length == 0) copyFile(assets, root, new File(temporary, root));
            else copyTree(assets, root, new File(temporary, root));
        }
        if (!new File(temporary, "index.html").isFile()) throw new IOException("Embedded index.html is missing");
        try (FileOutputStream output = new FileOutputStream(new File(temporary, ".complete"))) {
            output.write(version.getBytes(java.nio.charset.StandardCharsets.UTF_8));
        }
        delete(target);
        if (!temporary.renameTo(target)) throw new IOException("Unable to activate embedded web runtime");
        makeReadOnly(target);
        return target;
    }

    private static void copyTree(AssetManager assets, String source, File destination) throws IOException {
        String[] children = assets.list(source);
        if (children == null || children.length == 0) {
            copyFile(assets, source, destination);
            return;
        }
        if (!destination.exists() && !destination.mkdirs()) throw new IOException("Unable to create " + destination);
        for (String child : children) copyTree(assets, source + "/" + child, new File(destination, child));
    }

    private static void copyFile(AssetManager assets, String source, File destination) throws IOException {
        File parent = destination.getParentFile();
        if (parent != null && !parent.exists() && !parent.mkdirs()) throw new IOException("Unable to create " + parent);
        try (InputStream input = assets.open(source); FileOutputStream output = new FileOutputStream(destination)) {
            byte[] buffer = new byte[32768];
            int count;
            while ((count = input.read(buffer)) >= 0) output.write(buffer, 0, count);
        }
    }

    private static String safeVersion(String value) {
        return value.replaceAll("[^0-9A-Za-z._-]", "_");
    }

    private static void makeReadOnly(File file) {
        File[] children = file.listFiles();
        if (children != null) for (File child : children) makeReadOnly(child);
        file.setWritable(false, false);
        file.setReadable(true, true);
    }

    private static void delete(File file) {
        if (!file.exists()) return;
        file.setWritable(true, true);
        File[] children = file.listFiles();
        if (children != null) for (File child : children) delete(child);
        if (!file.delete()) throw new IllegalStateException("Unable to remove " + file);
    }
}
