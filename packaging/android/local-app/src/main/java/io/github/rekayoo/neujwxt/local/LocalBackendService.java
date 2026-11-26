package io.github.rekayoo.neujwxt.local;

import io.github.rekayoo.neujwxt.shared.Companion;
import io.github.rekayoo.neujwxt.shared.NotificationSummary;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.Build;
import android.util.Base64;

import androidx.annotation.Nullable;
import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.IOException;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.BooleanSupplier;
import java.util.function.Consumer;
import androidx.core.content.ContextCompat;

import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;

public final class LocalBackendService extends Service {
    private static final String CHANNEL_RUNTIME = "automation_runtime";
    private static final String CHANNEL_GRADES = "grade_updates";
    private static final String CHANNEL_SELECTION = "course_selection";
    private static final String CHANNEL_ERRORS = "task_errors";
    private static final int FOREGROUND_ID = 12001;
    private static final Object READY_LOCK = new Object();
    private static final List<Runnable> READY_CALLBACKS = new ArrayList<>();
    private static volatile String endpoint;
    private static volatile String token;
    private static volatile String startupEndpoint;
    private static volatile LocalBackendService instance;
    private static volatile Exception startupFailure;
    private static final Object PYTHON_LOCK = new Object();
    private static final List<Runnable> FOREGROUND_CALLBACKS = new ArrayList<>();
    private static final AtomicInteger MUTATIONS = new AtomicInteger();
    private static final AtomicLong MUTATION_EPOCH = new AtomicLong();
    private volatile boolean destroyed;
    private volatile boolean ready;

    private final ScheduledExecutorService worker = Executors.newSingleThreadScheduledExecutor();
    static final OkHttpClient HTTP = new OkHttpClient.Builder()
        .proxy(java.net.Proxy.NO_PROXY)
        .connectTimeout(5, TimeUnit.SECONDS).readTimeout(15, TimeUnit.SECONDS)
        .followRedirects(false).retryOnConnectionFailure(false).build();
    private volatile boolean foreground;

    public static void whenReady(Runnable callback) {
        boolean available;
        synchronized (READY_LOCK) {
            LocalBackendService current = instance;
            available = current != null && current.ready && !current.destroyed;
            if (!available) READY_CALLBACKS.add(callback);
        }
        if (available) callback.run();
    }

    public static void removeReadyCallback(Runnable callback) {
        synchronized (READY_LOCK) {
            READY_CALLBACKS.remove(callback);
        }
    }

    public static void withForeground(Context context, Runnable action, Consumer<Exception> failure) {
        new Handler(Looper.getMainLooper()).post(() -> {
            MUTATIONS.incrementAndGet();
            MUTATION_EPOCH.incrementAndGet();
            Runnable guarded = () -> {
                if (endpoint == null || !backgroundExecutionAllowed()) {
                    finishMutation();
                    failure.accept(new IOException("本地服务尚未就绪"));
                } else action.run();
            };
            FOREGROUND_CALLBACKS.add(guarded);
            try {
                ContextCompat.startForegroundService(context, new Intent(context, LocalBackendService.class));
            } catch (Exception exception) {
                FOREGROUND_CALLBACKS.remove(guarded);
                finishMutation();
                failure.accept(exception);
            }
        });
    }

    public static void finishMutation() {
        MUTATION_EPOCH.incrementAndGet();
        MUTATIONS.decrementAndGet();
    }

    public static String endpoint() {
        return endpoint;
    }

    public static String sessionToken() {
        return token;
    }

    static Exception startupFailure() {
        return startupFailure;
    }

    static boolean isRunning() {
        LocalBackendService current = instance;
        return current != null && !current.destroyed;
    }

    static boolean canResumeBackground(Context context) {
        return PermissionGuardTransport.notificationsGranted(context)
            && context.getSharedPreferences("mobile_runtime", Context.MODE_PRIVATE).getBoolean("background_required", false);
    }

    static boolean backgroundExecutionAllowed() {
        LocalBackendService current = instance;
        return current != null && current.foreground && !current.destroyed && PermissionGuardTransport.notificationsGranted(current);
    }

    @Override
    public void onCreate() {
        super.onCreate();
        instance = this;
        startupFailure = null;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        List<Runnable> callbacks = new ArrayList<>(FOREGROUND_CALLBACKS);
        FOREGROUND_CALLBACKS.clear();
        if (!PermissionGuardTransport.notificationsGranted(this) || (callbacks.isEmpty() && !canResumeBackground(this))) {
            for (Runnable callback : callbacks) callback.run();
            stopSelf(startId);
            return START_NOT_STICKY;
        }
        if (!foreground) {
            createChannels();
            promote("自动任务正在本机运行");
            worker.execute(this::startPython);
        }
        for (Runnable callback : callbacks) whenReady(callback);
        return START_STICKY;
    }

    @Nullable
    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private void startPython() {
        try {
            if (destroyed) return;
            ensureRuntime(getApplicationContext());
            if (destroyed) return;
            ready = true;
            notifyReady();
            if (!destroyed) worker.scheduleWithFixedDelay(this::pollBackend, 0, 5, TimeUnit.SECONDS);
        } catch (Exception exception) {
            if (destroyed) return;
            startupFailure = exception;
            if ((getApplicationInfo().flags & android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE) != 0) {
                android.util.Log.e("NeuMobileRuntime", "Embedded backend startup failed", exception);
            }
            endpoint = null;
            ready = false;
            notifyReady();
            postErrorNotification("本地服务启动失败", "请打开应用重试");
            stopSelf();
        }
    }

    static void ensureRuntime(Context context) throws Exception {
        synchronized (PYTHON_LOCK) {
            if (Thread.currentThread().isInterrupted()) throw new InterruptedException();
            if (endpoint != null) {
                try (Response health = healthCheck(endpoint)) {
                    if (health.isSuccessful()) return;
                } catch (IOException ignored) { }
            }
            if (token == null) {
                byte[] random = new byte[48];
                new SecureRandom().nextBytes(random);
                token = Base64.encodeToString(random, Base64.NO_WRAP | Base64.URL_SAFE);
            }
            if (!Python.isStarted()) Python.start(new AndroidPlatform(context));
            File data = new File(context.getFilesDir(), "data");
            File resources = new File(context.getFilesDir(), "runtime-resources");
            if (!data.exists() && !data.mkdirs()) throw new IOException("无法创建数据目录");
            if (!resources.exists() && !resources.mkdirs()) throw new IOException("无法创建资源目录");
            PackageInfo packageInfo = context.getPackageManager().getPackageInfo(context.getPackageName(), 0);
            PyObject module = Python.getInstance().getModule("launchers.mobile");
            BooleanSupplier backgroundPermission = LocalBackendService::backgroundExecutionAllowed;
            int port = module.callAttr("start", data.getAbsolutePath(), resources.getAbsolutePath(), token, packageInfo.versionName,
                backgroundPermission).toInt();
            startupEndpoint = "http://127.0.0.1:" + port + "/";
            waitUntilHealthy();
            endpoint = startupEndpoint;
        }
    }

    private static Response healthCheck(String address) throws IOException {
        return HTTP.newBuilder().callTimeout(1, TimeUnit.SECONDS).build().newCall(new Request.Builder()
            .url(address + "api/health").header("X-NEU-Mobile-Token", token).build()).execute();
    }

    private static void waitUntilHealthy() throws Exception {
        Exception last = null;
        long deadline = android.os.SystemClock.elapsedRealtime() + 30_000;
        while (android.os.SystemClock.elapsedRealtime() < deadline) {
            if (Thread.currentThread().isInterrupted()) throw new InterruptedException();
            try (Response response = healthCheck(startupEndpoint)) {
                if (response.isSuccessful()) return;
            } catch (Exception exception) {
                last = exception;
            }
            Thread.sleep(100);
        }
        throw new IOException("本地服务健康检查失败", last);
    }

    private void notifyReady() {
        new Handler(Looper.getMainLooper()).post(() -> {
            if (destroyed || instance != this) return;
            List<Runnable> callbacks;
            synchronized (READY_LOCK) {
                callbacks = new ArrayList<>(READY_CALLBACKS);
                READY_CALLBACKS.clear();
            }
            for (Runnable callback : callbacks) callback.run();
        });
    }

    private void pollBackend() {
        if (!PermissionGuardTransport.notificationsGranted(this)) {
            foreground = false;
            stopForeground(STOP_FOREGROUND_REMOVE);
            stopSelf();
            return;
        }
        if (endpoint == null) return;
        try {
            deliverNotifications();
            updateBackgroundState();
        } catch (Exception ignored) {
            // The next bounded poll retries; response bodies are never logged.
        }
    }

    private void deliverNotifications() throws Exception {
        try (Response response = request("api/mobile/notifications?limit=50", "GET", null)) {
            if (!response.isSuccessful() || response.body() == null) return;
            JSONArray messages = new JSONObject(response.body().string()).optJSONArray("notifications");
            if (messages == null) return;
            for (int index = 0; index < messages.length(); index++) {
                JSONObject message = messages.getJSONObject(index);
                boolean displayed = displayOutboxMessage(message);
                String id = message.optString("id");
                if (displayed && !id.trim().isEmpty()) {
                    try (Response ignored = request(
                        "api/mobile/notifications/" + id + "/ack", "POST", "{}"
                    )) {
                        // Acknowledgement commits the backend delivery callback.
                    }
                }
            }
        }
    }

    private boolean displayOutboxMessage(JSONObject value) {
        if (!NotificationManagerCompat.from(this).areNotificationsEnabled()) return false;
        String source = value.optString("source");
        String channel = source.equals("grade_tracking") ? CHANNEL_GRADES
            : source.equals("course_selection") ? CHANNEL_SELECTION : CHANNEL_ERRORS;
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel configured = getSystemService(NotificationManager.class).getNotificationChannel(channel);
            if (configured != null && configured.getImportance() == NotificationManager.IMPORTANCE_NONE) return false;
        }
        String route = value.optString("route", "/");
        Intent intent = new Intent(this, MainActivity.class).putExtra("route", route)
            .setFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent pending = PendingIntent.getActivity(
            this, value.optString("id").hashCode(), intent,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );
        int notificationIcon = source.equals("grade_tracking") ? io.github.rekayoo.neujwxt.shared.R.drawable.ic_companion_grades
            : android.R.drawable.stat_notify_more;
        Notification publicVersion = new NotificationCompat.Builder(this, channel)
            .setSmallIcon(notificationIcon)
            .setContentTitle("NEU 工具箱有新消息")
            .setContentText("解锁后查看详情")
            .build();
        Companion.withPrivacyLock(() -> {
            boolean hide = Companion.hideDetails(this);
            boolean grade = source.equals("grade_tracking");
            JSONObject metadata = value.optJSONObject("template_metadata");
            String details = grade && !route.equals("/login") ? gradeSummary(metadata) : value.optString("body");
            String title = hide ? (grade ? "成绩追踪" : "工具箱任务通知") : value.optString("title", "NEU 工具箱");
            String fallback = grade && !route.equals("/login") ? "点击应用查看成绩追踪详情" : "打开应用查看详情";
            String body = NotificationSummary.body(hide, fallback, details);
            Notification notification = new NotificationCompat.Builder(this, channel)
                .setSmallIcon(notificationIcon)
                .setColor(getColor(io.github.rekayoo.neujwxt.shared.R.color.widget_accent))
                .setContentTitle(title)
                .setContentText(body)
                .setStyle(new NotificationCompat.BigTextStyle().bigText(body))
                .setContentIntent(pending)
                .setAutoCancel(true)
                .setOnlyAlertOnce(true)
                .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
                .setPublicVersion(publicVersion)
                .build();
            getSystemService(NotificationManager.class).notify(Companion.LOCAL_NOTICE, value.optString("id").hashCode(), notification);
        });
        return true;
    }

    private static String gradeSummary(JSONObject metadata) {
        return metadata == null ? "" : metadata.optString("notification_summary");
    }

    private void updateBackgroundState() throws Exception {
        long epoch = MUTATION_EPOCH.get();
        try (Response response = request("api/mobile/background-state", "GET", null)) {
            if (!response.isSuccessful() || response.body() == null) return;
            boolean required = new JSONObject(response.body().string()).optBoolean("required");
            new Handler(Looper.getMainLooper()).post(() -> {
                if (destroyed) return;
                // A response begun before a task mutation must not overwrite its boot marker.
                if (MUTATIONS.get() != 0 || MUTATION_EPOCH.get() != epoch) return;
                getSharedPreferences("mobile_runtime", Context.MODE_PRIVATE).edit()
                    .putBoolean("background_required", required).apply();
                if (required && !foreground) promote("自动任务正在本机运行");
                if (!required) {
                    stopForeground(STOP_FOREGROUND_REMOVE);
                    foreground = false;
                    stopSelf();
                }
            });
        }
    }

    private Response request(String path, String method, String body) throws IOException {
        Request.Builder builder = new Request.Builder()
            .url((endpoint == null ? startupEndpoint : endpoint) + path)
            .header("X-NEU-Mobile-Token", token);
        if (body == null) builder.method(method, null);
        else builder.method(method, RequestBody.create(body, MediaType.get("application/json")));
        return HTTP.newCall(builder.build()).execute();
    }

    private void promote(String text) {
        Intent intent = new Intent(this, MainActivity.class);
        PendingIntent pending = PendingIntent.getActivity(
            this, 0, intent, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );
        Notification notification = new NotificationCompat.Builder(this, CHANNEL_RUNTIME)
            .setSmallIcon(android.R.drawable.stat_notify_sync)
            .setContentTitle("NEU 工具箱本地服务")
            .setContentText(text)
            .setContentIntent(pending)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setVisibility(NotificationCompat.VISIBILITY_SECRET)
            .build();
        startForeground(FOREGROUND_ID, notification);
        foreground = true;
    }

    private void postErrorNotification(String title, String text) {
        if (!NotificationManagerCompat.from(this).areNotificationsEnabled()) return;
        PendingIntent pending = PendingIntent.getActivity(this, 12002,
            new Intent(this, MainActivity.class),
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Notification notification = new NotificationCompat.Builder(this, CHANNEL_ERRORS)
            .setSmallIcon(android.R.drawable.stat_notify_error)
            .setContentTitle(title).setContentText(text).setContentIntent(pending).setAutoCancel(true).build();
        getSystemService(NotificationManager.class).notify(12002, notification);
    }

    private void createChannels() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        NotificationManager manager = getSystemService(NotificationManager.class);
        manager.createNotificationChannel(new NotificationChannel(
            CHANNEL_RUNTIME, "自动任务运行", NotificationManager.IMPORTANCE_LOW));
        manager.createNotificationChannel(new NotificationChannel(
            CHANNEL_GRADES, "成绩更新", NotificationManager.IMPORTANCE_DEFAULT));
        manager.createNotificationChannel(new NotificationChannel(
            CHANNEL_SELECTION, "选课结果", NotificationManager.IMPORTANCE_HIGH));
        manager.createNotificationChannel(new NotificationChannel(
            CHANNEL_ERRORS, "任务异常", NotificationManager.IMPORTANCE_HIGH));
    }

    @Override
    public void onDestroy() {
        destroyed = true;
        worker.shutdown();
        if (instance == this) instance = null;
        // Python and its token belong to the process, not to a transient foreground service.
        super.onDestroy();
    }
}
