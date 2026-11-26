package io.github.rekayoo.neujwxt.local;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

import androidx.core.content.ContextCompat;

public final class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        if (!Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction())) return;
        if (LocalBackendService.canResumeBackground(context)) {
            try {
                ContextCompat.startForegroundService(context, new Intent(context, LocalBackendService.class));
            } catch (RuntimeException blockedBySystem) {
                BootRecoveryNotification.show(context);
            }
        }
    }
}
