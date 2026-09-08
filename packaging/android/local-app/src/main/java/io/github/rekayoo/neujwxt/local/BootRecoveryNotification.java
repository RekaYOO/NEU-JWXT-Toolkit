package io.github.rekayoo.neujwxt.local;

import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.os.Build;

import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;

final class BootRecoveryNotification {
    private BootRecoveryNotification() {}

    static void show(Context context) {
        if (!NotificationManagerCompat.from(context).areNotificationsEnabled()) return;
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            manager.createNotificationChannel(new NotificationChannel(
                "task_errors", "任务异常", NotificationManager.IMPORTANCE_HIGH));
        }
        PendingIntent pending = PendingIntent.getActivity(
            context, 0, new Intent(context, MainActivity.class),
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );
        manager.notify(12003, new NotificationCompat.Builder(context, "task_errors")
            .setSmallIcon(android.R.drawable.stat_notify_error)
            .setContentTitle("自动任务尚未恢复")
            .setContentText("系统阻止了自动启动，点击恢复运行")
            .setContentIntent(pending).setAutoCancel(true).build());
    }
}
