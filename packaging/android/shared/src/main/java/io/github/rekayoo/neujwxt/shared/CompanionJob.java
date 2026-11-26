package io.github.rekayoo.neujwxt.shared;

import android.app.job.JobParameters;
import android.app.job.JobService;
import android.content.SharedPreferences;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.IOException;
import java.util.ArrayList;
import java.util.Calendar;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.TimeZone;
import java.util.concurrent.FutureTask;

import okhttp3.HttpUrl;
import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;
import okio.ByteString;

public abstract class CompanionJob extends JobService {
    private static final Object RUN = new Object();
    private final java.util.Map<Integer, Run> tasks = new java.util.HashMap<>();

    private final class Run {
        final FutureTask<Void> task;

        Run(JobParameters parameters) {
            task = new FutureTask<>(() -> {
                synchronized (RUN) {
                    if (!Thread.currentThread().isInterrupted()) sync();
                }
                new android.os.Handler(getMainLooper()).post(() -> {
                    if (tasks.get(parameters.getJobId()) == this) {
                        tasks.remove(parameters.getJobId());
                        jobFinished(parameters, false);
                    }
                });
                return null;
            });
        }
    }

    protected abstract Connection connect() throws Exception;

    protected static final class Connection {
        private final OkHttpClient client;
        private final HttpUrl root;
        private final String token;

        public Connection(OkHttpClient client, HttpUrl root, String token) {
            this.client = client;
            this.root = root;
            this.token = token;
        }

        JSONObject query(String path, boolean submit) throws Exception {
            if (Thread.currentThread().isInterrupted()) throw new InterruptedException();
            Request.Builder request = new Request.Builder().url(root.resolve(path));
            if (token != null) request.header("X-NEU-Mobile-Token", token);
            if (submit) request.post(RequestBody.create("{}", MediaType.get("application/json")));
            try (Response response = client.newCall(request.build()).execute()) {
                if (response.code() == 401) throw new SessionUnavailable();
                if (!response.isSuccessful() || response.body() == null) throw new IOException("Background request failed");
                if (response.peekBody(4 * 1024 * 1024 + 1).bytes().length > 4 * 1024 * 1024) {
                    throw new IOException("Background response exceeded its size limit");
                }
                return new JSONObject(response.body().string());
            }
        }

        void refresh(String resource) throws Exception {
            JSONObject submission = query("api/cache/refresh/" + resource, true);
            String id = submission.optString("job_id", "");
            if (!id.matches("[a-zA-Z0-9_-]{1,128}")) return;
            for (int attempt = 0; attempt < 5; attempt++) {
                String status = query("api/cache/jobs/" + id, false).optString("status");
                if (!status.equals("queued") && !status.equals("running")) return;
                Thread.sleep(800);
            }
        }
    }

    private static final class SessionUnavailable extends IOException { }

    @Override
    public boolean onStartJob(JobParameters parameters) {
        if (!Companion.mayRun(this)) {
            Companion.schedule(this, false);
            return false;
        }
        Run run = new Run(parameters);
        Run replaced = tasks.put(parameters.getJobId(), run);
        if (replaced != null) replaced.task.cancel(true);
        new Thread(run.task, "neu-companion").start();
        return true;
    }

    @Override
    public boolean onStopJob(JobParameters parameters) {
        Run run = tasks.remove(parameters.getJobId());
        if (run != null) run.task.cancel(true);
        return false;
    }

    @Override
    public void onDestroy() {
        for (Run run : tasks.values()) run.task.cancel(true);
        tasks.clear();
        super.onDestroy();
    }

    private void sync() {
        SharedPreferences config = Companion.prefs(this);
        long epoch = config.getLong("epoch", 0);
        if (!Companion.mayRun(this)) return;
        try {
            Connection connection = connect();
            if (connection == null) return;
            String account = account(connection);
            synchronized (Companion.STATE) {
                if (stale(config, epoch)) return;
                if (!config.getString("identity", "").equals(account)) {
                    Companion.clearData(this, config.edit(), true);
                    config.edit().putString("identity", account).apply();
                }
            }
            JSONObject timetable = Companion.syncWidget(this) ? loadTimetable(connection) : null;
            JSONArray messages = config.getBoolean(Companion.MESSAGES, false) ? loadMessages(connection) : null;
            JSONObject grades = config.getBoolean(Companion.GRADES, false) ? loadGrades(connection, !Companion.hideDetails(this)) : null;
            if (!account.equals(account(connection))) return;
            synchronized (Companion.STATE) {
                if (stale(config, epoch)) return;
                if (Companion.syncWidget(this) && timetable != null) {
                    config.edit().putString("timetable", timetable.toString()).apply();
                    TimetableWidget.render(this);
                }
                if (config.getBoolean(Companion.MESSAGES, false) && messages != null && Companion.mayNotify(this, Companion.MESSAGES)) {
                    publishMessages(config, messages);
                }
                if (config.getBoolean(Companion.GRADES, false) && grades != null && Companion.mayNotify(this, Companion.GRADES)) {
                    publishGrade(config, grades);
                }
            }
        } catch (SessionUnavailable expired) {
            synchronized (Companion.STATE) {
                if (!stale(config, epoch)) Companion.clearData(this, config.edit(), false);
            }
        } catch (Exception ignored) { }
    }

    private static boolean stale(SharedPreferences config, long epoch) {
        return Thread.currentThread().isInterrupted() || config.getLong("epoch", 0) != epoch
            || config.getBoolean("signed_out", false);
    }

    private static String account(Connection connection) throws Exception {
        JSONObject status = connection.query("api/status", false);
        if (!status.optBoolean("is_logged_in") || status.isNull("current_user")) throw new SessionUnavailable();
        String account = status.optString("current_user").trim();
        if (account.isEmpty()) throw new SessionUnavailable();
        return digest(account);
    }

    private JSONObject loadTimetable(Connection connection) throws Exception {
        try {
            connection.query("api/timetable/sync", true);
            return TimetableWidget.project(connection.query("api/timetable/bootstrap", false),
                Calendar.getInstance(TimeZone.getTimeZone("Asia/Shanghai")));
        } catch (SessionUnavailable expired) {
            throw expired;
        } catch (Exception failure) {
            return null;
        }
    }

    private JSONArray loadMessages(Connection connection) throws Exception {
        if (!Companion.mayNotify(this, Companion.MESSAGES)) return null;
        try {
            connection.refresh("system-messages");
            JSONObject payload = connection.query("api/system-messages/cache", false);
            return payload.optBoolean("available") && !payload.optBoolean("partial") ? payload.optJSONArray("messages") : null;
        } catch (SessionUnavailable expired) {
            throw expired;
        } catch (Exception failure) {
            return null;
        }
    }

    private JSONObject loadGrades(Connection connection, boolean details) throws Exception {
        if (!Companion.mayNotify(this, Companion.GRADES)) return null;
        try {
            String path = "api/mobile/grade-revision" + (details ? "?details=true" : "");
            JSONObject baseline = connection.query(path, false);
            connection.refresh("scores");
            JSONObject current = connection.query(path, false);
            if (current.optString("revision").isEmpty()) current = baseline;
            return current.optString("revision").matches("v1:[a-f0-9]{64}") ? current : null;
        } catch (SessionUnavailable expired) {
            throw expired;
        } catch (Exception failure) {
            return null;
        }
    }

    private void publishGrade(SharedPreferences config, JSONObject grades) {
        String revision = grades.optString("revision");
        String before = config.getString("grade_revision", "");
        boolean hide = Companion.hideDetails(this);
        String summary = hide ? "" : Companion.recordGradeDetails(this, before, revision, grades.optJSONArray("grades"));
        if (gradeChanged(before, revision)) {
            Companion.notify(this, Companion.GRADES, 7324, "成绩更新", NotificationSummary.body(hide, NotificationSummary.gradeFallback(), summary), "/scores");
        }
        config.edit().putString("grade_revision", revision).apply();
    }

    static boolean gradeChanged(String before, String revision) {
        return !before.isEmpty() && !revision.isEmpty() && !before.equals(revision);
    }

    private void publishMessages(SharedPreferences config, JSONArray messages) {
        MessageUpdate update = compareMessages(config.getStringSet("seen_messages", null), messages);
        if (update.unseen > 0) {
            String fallback = "有 " + update.unseen + " 条新消息，点击查看";
            Companion.notify(this, Companion.MESSAGES, 7323, "教务新消息",
                NotificationSummary.body(Companion.hideDetails(this), fallback, update.summary), "/system-settings?tab=messages");
        }
        config.edit().putStringSet("seen_messages", update.seen).apply();
    }

    static final class MessageUpdate {
        final Set<String> seen;
        final int unseen;
        final String summary;

        MessageUpdate(Set<String> seen, int unseen, String summary) {
            this.seen = seen;
            this.unseen = unseen;
            this.summary = summary;
        }
    }

    static MessageUpdate compareMessages(Set<String> before, JSONArray messages) {
        Set<String> current = new LinkedHashSet<>();
        List<String> titles = new ArrayList<>();
        int unseen = 0;
        for (int index = 0; index < Math.min(messages.length(), 500); index++) {
            JSONObject item = messages.optJSONObject(index);
            if (item == null || item.isNull("id") || item.optString("id").isEmpty()) continue;
            String id = digest(item.optString("kind") + "\n" + item.optString("id"));
            if (current.add(id) && before != null && !before.contains(id) && !item.optBoolean("read", true)) {
                unseen++;
                String title = NotificationSummary.field(item, "title", 65);
                if (!title.isEmpty()) titles.add(title);
            }
        }
        if (before != null) for (String id : before) if (current.size() < 1000) current.add(id);
        return new MessageUpdate(current, unseen, NotificationSummary.lines(titles, "条新消息"));
    }

    static String digest(String value) {
        return ByteString.encodeUtf8(value).sha256().hex();
    }
}
