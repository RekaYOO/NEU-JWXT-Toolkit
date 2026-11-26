package io.github.rekayoo.neujwxt.shared;

import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.ComponentName;
import android.content.Context;
import android.view.View;
import android.widget.RemoteViews;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Calendar;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.TimeZone;

public class TimetableWidget extends AppWidgetProvider {
    public static final class Next extends TimetableWidget { }
    public static final class Square extends TimetableWidget { }
    public static final class Tall extends TimetableWidget { }

    enum Preset {
        NEXT(Next.class, R.string.widget_next_label, 0, false),
        SQUARE(Square.class, R.string.widget_square_label, 2, false),
        WIDE(TimetableWidget.class, R.string.widget_wide_label, 3, true),
        TALL(Tall.class, R.string.widget_tall_label, 5, false);

        final Class<? extends TimetableWidget> provider;
        final int label;
        final int rows;
        final boolean wide;

        Preset(Class<? extends TimetableWidget> provider, int label, int rows, boolean wide) {
            this.provider = provider;
            this.label = label;
            this.rows = rows;
            this.wide = wide;
        }

        ComponentName component(Context context) {
            return new ComponentName(context, provider);
        }
    }

    static boolean installed(Context context) {
        AppWidgetManager manager = AppWidgetManager.getInstance(context);
        for (Preset preset : Preset.values()) {
            if (manager.getAppWidgetIds(preset.component(context)).length > 0) return true;
        }
        return false;
    }

    @Override
    public void onEnabled(Context context) {
        Companion.schedule(context, true);
    }

    @Override
    public void onUpdate(Context context, AppWidgetManager manager, int[] ids) {
        render(context);
        Companion.schedule(context, false);
    }

    @Override
    public void onDisabled(Context context) {
        Companion.removeWidget(context);
    }

    static void render(Context context) {
        JSONObject days;
        try {
            days = new JSONObject(Companion.prefs(context).getString("timetable", "{}"));
        } catch (JSONException ignored) {
            days = new JSONObject();
        }
        Calendar today = Calendar.getInstance(TimeZone.getTimeZone("Asia/Shanghai"));
        AppWidgetManager manager = AppWidgetManager.getInstance(context);
        for (Preset preset : Preset.values()) {
            ComponentName component = preset.component(context);
            if (manager.getAppWidgetIds(component).length == 0) continue;
            RemoteViews views = preset == Preset.NEXT ? renderNext(context, days, today) : renderDay(context, preset, days, today);
            views.setOnClickPendingIntent(R.id.widget_timetable, Companion.open(context, "/timetable", 7304));
            manager.updateAppWidget(component, views);
        }
    }

    private static RemoteViews renderNext(Context context, JSONObject days, Calendar today) {
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_timetable_next);
        NextCourse next = nextCourse(days, today);
        if (next == null) {
            boolean ready = days.optJSONArray(format(today, "yyyy-MM-dd")) != null;
            views.setTextViewText(R.id.widget_next_title, ready ? "暂无下一堂课" : "打开同步课表");
            views.setViewVisibility(R.id.widget_next_details, View.GONE);
        } else {
            views.setTextViewText(R.id.widget_next_title, next.course.optString("title"));
            views.setTextViewText(R.id.widget_next_details, withPlace(next.date + " " + courseTime(next.course), next.course));
            views.setViewVisibility(R.id.widget_next_details, View.VISIBLE);
        }
        return views;
    }

    private static RemoteViews renderDay(Context context, Preset preset, JSONObject days, Calendar today) {
        String date = format(today, "yyyy-MM-dd");
        JSONArray courses = days.optJSONArray(date);
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_timetable);
        views.setTextViewText(R.id.widget_date, format(today, "M/d EEE"));
        views.removeAllViews(R.id.widget_courses);
        int count = courses == null ? 0 : courses.length();
        views.setViewVisibility(R.id.widget_status, count == 0 ? View.VISIBLE : View.GONE);
        if (count == 0) {
            Object previous = days.opt(date);
            String status = courses != null ? "今日无排课" : previous instanceof String ? (String) previous : "打开同步课表";
            views.setTextViewText(R.id.widget_status, status);
        }
        for (int index = 0; index < Math.min(count, preset.rows); index++) {
            JSONObject course = courses.optJSONObject(index);
            if (course == null) continue;
            RemoteViews row = new RemoteViews(context.getPackageName(), preset.wide ? R.layout.widget_timetable_row_wide : R.layout.widget_timetable_row);
            row.setTextViewText(R.id.widget_course_time, courseTime(course));
            row.setTextViewText(R.id.widget_course_title, preset.wide ? withPlace(course.optString("title"), course) : course.optString("title"));
            if (!preset.wide) {
                String place = course.optString("place");
                row.setTextViewText(R.id.widget_course_place, place);
                row.setViewVisibility(R.id.widget_course_place, place.isEmpty() ? View.GONE : View.VISIBLE);
            }
            views.addView(R.id.widget_courses, row);
        }
        if (count > preset.rows) views.setTextViewText(R.id.widget_date, "+" + (count - preset.rows) + " 堂 · " + format(today, "M/d"));
        return views;
    }

    private static String withPlace(String text, JSONObject course) {
        String place = course.optString("place");
        return place.isEmpty() ? text : text + " · " + place;
    }

    private static String courseTime(JSONObject course) {
        int minutes = course.optInt("minute", -1);
        return minutes >= 0 ? String.format(Locale.ROOT, "%02d:%02d", minutes / 60, minutes % 60)
            : course.optInt("section") + "节";
    }

    static final class NextCourse {
        final String date;
        final JSONObject course;

        NextCourse(String date, JSONObject course) {
            this.date = date;
            this.course = course;
        }
    }

    static NextCourse nextCourse(JSONObject days, Calendar today) {
        if (days.optJSONArray(format(today, "yyyy-MM-dd")) == null) return null;
        int currentMinute = today.get(Calendar.HOUR_OF_DAY) * 60 + today.get(Calendar.MINUTE);
        List<String> dates = new ArrayList<>();
        days.keys().forEachRemaining(dates::add);
        dates.sort(Comparator.naturalOrder());
        String currentDate = format(today, "yyyy-MM-dd");
        for (String date : dates) {
            int order = date.compareTo(currentDate);
            JSONArray courses = days.optJSONArray(date);
            if (order < 0 || courses == null) continue;
            for (int index = 0; index < courses.length(); index++) {
                JSONObject course = courses.optJSONObject(index);
                if (course == null || (order == 0 && course.optInt("minute", -1) <= currentMinute)) continue;
                return new NextCourse(order == 0 ? "今天" : date.substring(5).replace('-', '/'), course);
            }
        }
        return null;
    }

    static JSONObject project(JSONObject snapshot, Calendar today) throws JSONException {
        JSONObject result = new JSONObject();
        JSONArray terms = snapshot.optJSONArray("personal");
        if (terms == null || terms.length() == 0) return result;
        Calendar day = (Calendar) today.clone();
        for (int offset = 0; offset < 370; offset++, day.add(Calendar.DATE, 1)) {
            String date = format(day, "yyyy-MM-dd");
            int weekday = (day.get(Calendar.DAY_OF_WEEK) + 5) % 7 + 1;
            JSONArray daily = new JSONArray();
            for (int termIndex = 0; termIndex < terms.length(); termIndex++) {
                JSONObject term = terms.optJSONObject(termIndex);
                if (term == null) continue;
                int week = teachingWeek(term.optJSONArray("weeks"), date);
                JSONArray courses = term.optJSONArray("courses");
                if (week <= 0 || courses == null) continue;
                daily = describe(term, courses, weekday, week);
                break;
            }
            if (offset < 8 || daily.length() > 0) result.put(date, daily);
            if (offset >= 8 && daily.length() > 0) break;
        }
        return result;
    }

    private static int teachingWeek(JSONArray weeks, String date) {
        if (weeks == null) return 0;
        for (int index = 0; index < weeks.length(); index++) {
            JSONObject week = weeks.optJSONObject(index);
            if (week == null) continue;
            if (week.optString("start_date").compareTo(date) <= 0 && week.optString("end_date").compareTo(date) >= 0) {
                return week.optInt("number");
            }
        }
        return 0;
    }

    private static JSONArray describe(JSONObject term, JSONArray courses, int weekday, int week) throws JSONException {
        List<JSONObject> meetings = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        for (int index = 0; index < courses.length(); index++) {
            JSONObject course = courses.optJSONObject(index);
            if (course == null || course.optInt("weekday") != weekday || !containsWeek(course.optJSONArray("weeks"), week)) continue;
            int section = course.optInt("start_section");
            String title = text(course, "course_name", 40);
            if (section < 1 || section > 30 || title.isEmpty()) continue;
            String place = text(course, "location", 32);
            int minute = startMinute(term, course, section);
            JSONObject meeting = new JSONObject().put("section", section).put("minute", minute).put("title", title).put("place", place);
            if (seen.add(meeting.toString())) meetings.add(meeting);
        }
        meetings.sort(Comparator.comparingInt(TimetableWidget::sortMinute)
            .thenComparingInt(meeting -> meeting.optInt("section")).thenComparing(meeting -> meeting.optString("title")));
        return new JSONArray(meetings);
    }

    private static int sortMinute(JSONObject meeting) {
        int minute = meeting.optInt("minute");
        if (minute >= 0) return minute;
        int section = meeting.optInt("section");
        return section <= 4 ? EARLY_MORNING[section - 1] : 1440 + section;
    }

    private static int startMinute(JSONObject term, JSONObject course, int section) {
        int explicit = parseMinute(course.optString("start_time"));
        if (explicit >= 0) return explicit;
        String campus = course.optString("campus", "").trim();
        String code = campus;
        JSONArray campuses = term.optJSONArray("campuses");
        for (int index = 0; campuses != null && index < campuses.length(); index++) {
            JSONObject item = campuses.optJSONObject(index);
            if (item != null && (campus.equals(item.optString("name")) || campus.equals(item.optString("code")))) {
                code = item.optString("code");
                campus = item.optString("name");
                break;
            }
        }
        JSONObject catalog = term.optJSONObject("sections_by_campus");
        JSONArray sections = catalog == null || code.isEmpty() || code.equals("all") ? null : catalog.optJSONArray(code);
        for (int index = 0; sections != null && index < sections.length(); index++) {
            JSONObject slot = sections.optJSONObject(index);
            if (slot == null || slot.optInt("number") != section) continue;
            int official = parseMinute(slot.optString("start_time"));
            if (official >= 0) return official;
        }
        return schoolStartMinute(campus, code, section);
    }

    private static final int[] EARLY_MORNING = {480, 535, 600, 655};
    private static final int[] HUNNAN_MORNING = {510, 565, 630, 685};
    private static final int[] AFTERNOON = {840, 895, 960, 1015, 1110, 1165, 1220, 1275};

    private static int schoolStartMinute(String campus, String code, int section) {
        if (section >= 5 && section <= 12) return AFTERNOON[section - 5];
        if (section > 4) return -1;
        if (campus.contains("浑南") || code.equals("01")) return HUNNAN_MORNING[section - 1];
        if (campus.contains("南湖") || campus.contains("东校区") || campus.equals("东区") || campus.startsWith("网络") || code.equals("00")) {
            return EARLY_MORNING[section - 1];
        }
        return -1;
    }

    private static int parseMinute(String value) {
        String time = value.trim();
        if (!time.matches("[0-9]{1,2}:[0-9]{2}(:[0-9]{2})?")) return -1;
        String[] parts = time.split(":");
        int hour = Integer.parseInt(parts[0]);
        int minute = Integer.parseInt(parts[1]);
        return hour < 24 && minute < 60 ? hour * 60 + minute : -1;
    }

    private static boolean containsWeek(JSONArray weeks, int week) {
        if (weeks == null) return false;
        for (int index = 0; index < weeks.length(); index++) {
            if (weeks.optInt(index) == week) return true;
        }
        return false;
    }

    private static String format(Calendar date, String pattern) {
        SimpleDateFormat formatter = new SimpleDateFormat(pattern, Locale.SIMPLIFIED_CHINESE);
        formatter.setTimeZone(date.getTimeZone());
        return formatter.format(date.getTime());
    }

    private static String text(JSONObject value, String field, int limit) {
        if (value.isNull(field)) return "";
        String clean = value.optString(field).replaceAll("[\\s\\p{Cntrl}]+", " ").trim();
        if (clean.codePointCount(0, clean.length()) <= limit) return clean;
        return clean.substring(0, clean.offsetByCodePoints(0, limit)) + "…";
    }
}
