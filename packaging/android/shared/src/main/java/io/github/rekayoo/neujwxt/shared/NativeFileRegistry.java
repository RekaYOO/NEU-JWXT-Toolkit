package io.github.rekayoo.neujwxt.shared;

import android.content.Context;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

public final class NativeFileRegistry {
    private final File directory;
    private static final ConcurrentHashMap<String, File> files = new ConcurrentHashMap<>();
    private static final java.util.Set<String> uploads = ConcurrentHashMap.newKeySet();

    NativeFileRegistry(Context context) {
        directory = new File(context.getCacheDir(), "native-downloads");
        if (!directory.exists()) directory.mkdirs();
        File[] stale = directory.listFiles();
        long expiredBefore = System.currentTimeMillis() - 24 * 60 * 60 * 1000L;
        if (stale != null) for (File file : stale) {
            if (file.lastModified() < expiredBefore) file.delete();
        }
    }

    public String store(InputStream input) throws IOException {
        String token = UUID.randomUUID().toString();
        File target = new File(directory, token);
        try (FileOutputStream output = new FileOutputStream(target)) {
            byte[] buffer = new byte[65536];
            int count;
            while ((count = input.read(buffer)) >= 0) output.write(buffer, 0, count);
        } catch (IOException exception) {
            target.delete();
            throw exception;
        }
        files.put(token, target);
        return token;
    }

    public InputStream claim(String token) throws IOException {
        File file = files.remove(token);
        if (file == null || !file.isFile()) throw new IOException("Download token is invalid or expired");
        return new DeletingInputStream(file);
    }

    public long size(String token) {
        File file = files.get(token);
        return file == null ? 0 : file.length();
    }

    public String beginUpload() throws IOException {
        String token = UUID.randomUUID().toString();
        File target = new File(directory, token);
        if (!target.createNewFile()) throw new IOException("Unable to create temporary file");
        files.put(token, target);
        uploads.add(token);
        return token;
    }

    public synchronized void appendUpload(String token, byte[] bytes) throws IOException {
        File target = files.get(token);
        if (target == null || !uploads.contains(token) || bytes.length > 65536) {
            throw new IOException("Invalid file upload chunk");
        }
        try (FileOutputStream output = new FileOutputStream(target, true)) {
            output.write(bytes);
        }
    }

    public void finishUpload(String token) {
        uploads.remove(token);
    }

    public void discard(String token) {
        uploads.remove(token);
        File file = files.remove(token);
        if (file != null) file.delete();
    }

    private static final class DeletingInputStream extends FileInputStream {
        private final File file;

        DeletingInputStream(File file) throws IOException {
            super(file);
            this.file = file;
        }

        @Override
        public void close() throws IOException {
            try {
                super.close();
            } finally {
                file.delete();
            }
        }
    }
}
