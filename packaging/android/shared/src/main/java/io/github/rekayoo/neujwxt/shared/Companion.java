package io.github.rekayoo.neujwxt.shared;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.AlertDialog;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.job.JobInfo;
import android.app.job.JobScheduler;
import android.appwidget.AppWidgetManager;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.provider.Settings;
import android.widget.Toast;

import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;
import androidx.security.crypto.EncryptedSharedPreferences;
import androidx.security.crypto.MasterKey;

import org.json.JSONArray;
import org.json.JSONObject;

public final class Companion {
    static final String MESSAGES = "official_messages";
    static final String GRADES = "grade_updates";
    static final String WIDGET_SYNC = "widget_background_sync";
    static final String HIDE_DETAILS = "hide_notification_details";
    public static final String LOCAL_NOTICE = "local_task_notice";
    static final Object STATE = new Object();
    private static final int PERIODIC = 7320;
    private static final int IMMEDIATE = 7321;
    private static final int PERMISSION = 7322;

    private Companion() { }

    static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences("android_companion", Context.MODE_PRIVATE);
    }

    public static boolean hideDetails(Context context) {
        return prefs(context).getBoolean(HIDE_DETAILS, true);
    }

    static boolean syncWidget(Context context) {
        return prefs(context).getBoolean(WIDGET_SYNC, false) && TimetableWidget.installed(context);
    }

    static boolean mayRun(Context context) {
        SharedPreferences config = prefs(context);
        return !config.getBoolean("signed_out", false) && (syncWidget(context)
            || config.getBoolean(MESSAGES, false) && mayNotify(context, MESSAGES)
            || config.getBoolean(GRADES, false) && mayNotify(context, GRADES));
    }

    private static SharedPreferences gradeDetails(Context context) throws Exception {
        MasterKey key = new MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build();
        return EncryptedSharedPreferences.create(context, "companion_grade_details", key,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM);
    }

    static String recordGradeDetails(Context context, String previousRevision, String revision, JSONArray current) {
        if (current == null) return "";
        try {
            SharedPreferences encrypted = gradeDetails(context);
            JSONObject baseline = new JSONObject(encrypted.getString("baseline", "{}"));
            String summary = "";
            if (!previousRevision.isEmpty() && previousRevision.equals(baseline.optString("revision"))
                    && prefs(context).getString("identity", "").equals(baseline.optString("identity"))) {
                summary = NotificationSummary.grades(baseline.optJSONArray("grades"), current);
            }
            encrypted.edit().putString("baseline", new JSONObject().put("revision", revision).put("grades", current)
                .put("identity", prefs(context).getString("identity", "")).toString()).apply();
            return summary;
        } catch (Exception unavailable) {
            return "";
        }
    }

    private static void clearGradeDetails(Context context) {
        context.deleteSharedPreferences("companion_grade_details");
    }

    static PendingIntent open(Context context, String route, int id) {
        Intent intent = context.getPackageManager().getLaunchIntentForPackage(context.getPackageName());
        if (intent == null) return null;
        intent.putExtra("route", route).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        return PendingIntent.getActivity(context, id, intent, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    static void schedule(Context context, boolean immediate) {
        synchronized (STATE) {
            String service;
            try {
                service = context.getPackageManager().getApplicationInfo(context.getPackageName(), PackageManager.GET_META_DATA)
                    .metaData.getString("io.github.rekayoo.neujwxt.COMPANION_JOB");
            } catch (Exception exception) {
                return;
            }
            if (service == null) return;
            JobScheduler jobs = context.getSystemService(JobScheduler.class);
            if (!mayRun(context)) {
                jobs.cancel(PERIODIC);
                jobs.cancel(IMMEDIATE);
                return;
            }
            ComponentName component = new ComponentName(context, service);
            if (jobs.getPendingJob(PERIODIC) == null) {
                jobs.schedule(new JobInfo.Builder(PERIODIC, component).setPersisted(true)
                    .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY).setPeriodic(30 * 60_000L).build());
            }
            if (immediate && jobs.getPendingJob(IMMEDIATE) == null) {
                jobs.schedule(new JobInfo.Builder(IMMEDIATE, component)
                    .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY).build());
            }
        }
    }

    static void onAuthenticated(Context context, String account) {
        synchronized (STATE) {
            SharedPreferences config = prefs(context);
            boolean resumed = config.getBoolean("signed_out", false);
            String current = config.getString("identity", "");
            String identity = CompanionJob.digest(account);
            boolean changed = !current.isEmpty() && !current.equals(identity);
            SharedPreferences.Editor update = config.edit().remove("signed_out").putString("identity", identity);
            if (changed) {
                clearData(context, update.putLong("epoch", config.getLong("epoch", 0) + 1), true);
                config.edit().putString("identity", identity).apply();
            } else {
                update.apply();
            }
            schedule(context, changed || resumed);
        }
    }

    public static void clearSession(Context context) {
        synchronized (STATE) {
            SharedPreferences config = prefs(context);
            clearData(context, config.edit().putBoolean("signed_out", true)
                .putLong("epoch", config.getLong("epoch", 0) + 1), true);
            schedule(context, false);
        }
    }

    static void clearData(Context context, SharedPreferences.Editor editor, boolean clearBaseline) {
        editor.remove("timetable");
        if (clearBaseline) {
            editor.remove("identity").remove("seen_messages").remove("grade_revision");
            clearGradeDetails(context);
        }
        editor.apply();
        clearNotices(context);
        TimetableWidget.render(context);
    }

    private static void clearNotices(Context context) {
        NotificationManager notifications = context.getSystemService(NotificationManager.class);
        notifications.cancel(MESSAGES, 7323);
        notifications.cancel(GRADES, 7324);
        for (android.service.notification.StatusBarNotification notice : notifications.getActiveNotifications()) {
            if (LOCAL_NOTICE.equals(notice.getTag()) || (Build.VERSION.SDK_INT >= 26 && notice.getTag() == null
                    && isLegacyTaskChannel(notice.getNotification().getChannelId()))) {
                notifications.cancel(notice.getTag(), notice.getId());
            }
        }
    }

    private static boolean isLegacyTaskChannel(String channel) {
        return GRADES.equals(channel) || "course_selection".equals(channel) || "task_errors".equals(channel);
    }

    public static void withPrivacyLock(Runnable action) {
        synchronized (STATE) { action.run(); }
    }

    static void removeWidget(Context context) {
        synchronized (STATE) {
            if (TimetableWidget.installed(context)) return;
            prefs(context).edit().remove("timetable").apply();
            schedule(context, false);
        }
    }

    static boolean mayNotify(Context context, String channel) {
        if (!NotificationManagerCompat.from(context).areNotificationsEnabled()) return false;
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel current = context.getSystemService(NotificationManager.class).getNotificationChannel(channel);
            return current == null || current.getImportance() != NotificationManager.IMPORTANCE_NONE;
        }
        return true;
    }

    @SuppressLint("MissingPermission")
    static void notify(Context context, String channel, int id, String title, String body, String route) {
        if (!mayNotify(context, channel)) return;
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        if (Build.VERSION.SDK_INT >= 26) {
            manager.createNotificationChannel(new NotificationChannel(channel, title, NotificationManager.IMPORTANCE_DEFAULT));
        }
        int icon = channel.equals(GRADES) ? R.drawable.ic_companion_grades : R.drawable.ic_companion_messages;
        manager.notify(channel, id, new NotificationCompat.Builder(context, channel)
            .setSmallIcon(icon)
            .setColor(context.getColor(R.color.widget_accent)).setContentTitle(title).setContentText(body)
            .setStyle(new NotificationCompat.BigTextStyle().bigText(body))
            .setContentIntent(open(context, route, id)).setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .setPublicVersion(new NotificationCompat.Builder(context, channel).setSmallIcon(icon)
                .setContentTitle("NEU 工具箱有新消息").setContentText("解锁后查看详情").build())
            .setAutoCancel(true).build());
    }

    static void showSettings(BaseShellActivity activity, boolean client) {
        SharedPreferences config = prefs(activity);
        String[] keys = client ? new String[]{HIDE_DETAILS, WIDGET_SYNC, MESSAGES, GRADES} : new String[]{HIDE_DETAILS, WIDGET_SYNC, MESSAGES};
        String[] labels = client ? new String[]{"隐藏通知详情（打开应用查看）", "允许在后台同步桌面课表", "教务新消息（需通知授权）", "成绩更新（需通知授权）"}
            : new String[]{"隐藏通知详情（打开应用查看）", "允许在后台同步桌面课表", "教务新消息（需通知授权）"};
        boolean[] enabled = new boolean[keys.length];
        for (int index = 0; index < keys.length; index++) enabled[index] = config.getBoolean(keys[index], keys[index].equals(HIDE_DETAILS));
        new AlertDialog.Builder(activity).setTitle("桌面课表与通知")
            .setMultiChoiceItems(labels, enabled, (dialog, index, checked) -> enabled[index] = checked)
            .setNeutralButton("添加桌面课表", (dialog, which) -> pinWidget(activity))
            .setNegativeButton("取消", null)
            .setPositiveButton("保存", (dialog, which) -> {
                boolean requestsNotification = enabled[2] || client && enabled[3];
                synchronized (STATE) {
                    SharedPreferences.Editor edit = config.edit();
                    if (enabled[2] != config.getBoolean(MESSAGES, false)) edit.remove("seen_messages");
                    if (client && enabled[3] != config.getBoolean(GRADES, false)) edit.remove("grade_revision");
                    if (!enabled[2]) activity.getSystemService(NotificationManager.class).cancel(MESSAGES, 7323);
                    if (client && !enabled[3]) activity.getSystemService(NotificationManager.class).cancel(GRADES, 7324);
                    if (enabled[0] || client && !enabled[3]) clearGradeDetails(activity);
                    if (enabled[0] && !hideDetails(activity)) clearNotices(activity);
                    edit.putLong("epoch", config.getLong("epoch", 0) + 1);
                    for (int index = 0; index < keys.length; index++) {
                        edit.putBoolean(keys[index], enabled[index]);
                    }
                    edit.apply();
                    activity.getSystemService(JobScheduler.class).cancel(IMMEDIATE);
                    schedule(activity, true);
                }
                if (requestsNotification && !NotificationManagerCompat.from(activity).areNotificationsEnabled()) {
                    if (Build.VERSION.SDK_INT >= 33 && activity.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
                        activity.requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, PERMISSION);
                    } else {
                        activity.startActivity(new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
                            .setData(Uri.fromParts("package", activity.getPackageName(), null)));
                    }
                }
            }).show();
    }

    private static void pinWidget(Context context) {
        AppWidgetManager manager = AppWidgetManager.getInstance(context);
        if (Build.VERSION.SDK_INT >= 26 && manager.isRequestPinAppWidgetSupported()) {
            TimetableWidget.Preset[] presets = TimetableWidget.Preset.values();
            String[] labels = new String[presets.length];
            for (int index = 0; index < presets.length; index++) labels[index] = context.getString(presets[index].label);
            new AlertDialog.Builder(context).setTitle("选择桌面课表尺寸")
                .setItems(labels, (dialog, index) -> {
                    if (!manager.requestPinAppWidget(presets[index].component(context), null, null)) widgetHelp(context);
                }).setNegativeButton("取消", null).show();
            return;
        }
        widgetHelp(context);
    }

    private static void widgetHelp(Context context) {
        Toast.makeText(context, "请长按桌面空白处，在工具箱小组件下选择课表尺寸", Toast.LENGTH_LONG).show();
    }
}
