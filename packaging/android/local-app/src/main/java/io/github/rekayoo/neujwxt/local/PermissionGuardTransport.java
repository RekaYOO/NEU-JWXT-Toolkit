package io.github.rekayoo.neujwxt.local;

import android.Manifest;
import android.content.Context;
import android.content.pm.PackageManager;
import android.os.Build;

import androidx.core.content.ContextCompat;
import androidx.core.app.NotificationManagerCompat;

import org.json.JSONObject;

import io.github.rekayoo.neujwxt.shared.ApiTransport;
import io.github.rekayoo.neujwxt.shared.NativeRequest;

final class PermissionGuardTransport implements ApiTransport {
    private final Context context;
    private final ApiTransport delegate;
    private final java.util.Set<String> queued = new java.util.HashSet<>();

    PermissionGuardTransport(Context context, ApiTransport delegate) {
        this.context = context;
        this.delegate = delegate;
    }

    @Override
    public void request(String id, NativeRequest request, Callback callback) {
        if (enablesBackgroundTask(request) && !notificationsGranted()) {
            if (Build.VERSION.SDK_INT >= 33 && context instanceof android.app.Activity) {
                android.app.Activity activity = (android.app.Activity) context;
                activity.runOnUiThread(() -> androidx.core.app.ActivityCompat.requestPermissions(
                    activity, new String[]{Manifest.permission.POST_NOTIFICATIONS}, 8103));
            }
            try {
                callback.complete(new JSONObject()
                    .put("status", 409)
                    .put("body", new JSONObject().put(
                        "detail", "请先允许系统通知，再开启无人值守任务"
                    ).toString()));
            } catch (Exception impossible) {
                callback.complete(new JSONObject());
            }
            return;
        }
        if (enablesBackgroundTask(request)) {
            synchronized (queued) { queued.add(id); }
            LocalBackendService.withForeground(context, () -> {
                synchronized (queued) {
                    if (!queued.remove(id)) {
                        LocalBackendService.finishMutation();
                        return;
                    }
                    try {
                        delegate.request(id, request, payload -> {
                            try {
                                if (payload.optInt("status") >= 200 && payload.optInt("status") < 300) {
                                    context.getSharedPreferences("mobile_runtime", Context.MODE_PRIVATE).edit()
                                        .putBoolean("background_required", true).commit();
                                }
                                callback.complete(payload);
                            } finally {
                                LocalBackendService.finishMutation();
                            }
                        });
                    } catch (RuntimeException exception) {
                        LocalBackendService.finishMutation();
                        try {
                            callback.complete(new JSONObject().put("status", 0)
                                .put("code", "ERR_INVALID_REQUEST").put("error", "原生请求参数无效"));
                        } catch (Exception impossible) {
                            callback.complete(new JSONObject());
                        }
                    }
                }
            }, exception -> {
                synchronized (queued) { queued.remove(id); }
                try {
                    callback.complete(new JSONObject().put("status", 409).put("body",
                        "{\"detail\":\"系统暂不允许启动后台服务，请保持应用在前台后重试\"}"));
                } catch (Exception impossible) {
                    callback.complete(new JSONObject());
                }
            });
            return;
        }
        delegate.request(id, request, callback);
    }

    @Override
    public void cancel(String id) {
        synchronized (queued) {
            queued.remove(id);
            delegate.cancel(id);
        }
    }

    @Override
    public void close() {
        synchronized (queued) {
            queued.clear();
            delegate.close();
        }
    }

    private boolean notificationsGranted() {
        return NotificationManagerCompat.from(context).areNotificationsEnabled()
            && (Build.VERSION.SDK_INT < 33 || ContextCompat.checkSelfPermission(
            context, Manifest.permission.POST_NOTIFICATIONS
        ) == PackageManager.PERMISSION_GRANTED);
    }

    static boolean enablesBackgroundTask(NativeRequest request) {
        String path = request.path.split("\\?", 2)[0];
        if ((request.method.equals("PATCH") && path.equals("/api/grade-tracking/enabled"))
            || (request.method.equals("PUT") && path.equals("/api/grade-tracking/config"))) {
            try {
                JSONObject body = new JSONObject(request.body);
                return body.has("enabled") && !Boolean.FALSE.equals(body.opt("enabled"));
            } catch (Exception exception) {
                return true;
            }
        }
        if (request.method.equals("POST") && path.equals("/api/course-selection/jwxk/automation/tasks")) {
            return true;
        }
        return request.method.equals("POST")
            && path.equals("/api/course-selection/jwxk/automation/tasks/start");
    }
}
