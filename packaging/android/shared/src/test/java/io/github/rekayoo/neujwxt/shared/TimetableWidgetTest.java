package io.github.rekayoo.neujwxt.shared;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;

import org.json.JSONObject;
import org.junit.Test;

import java.util.Calendar;
import java.util.TimeZone;

public final class TimetableWidgetTest {
    @Test
    public void projectsTodayAndNextDayInTeachingWeek() throws Exception {
        Calendar today = Calendar.getInstance(TimeZone.getTimeZone("Asia/Shanghai"));
        today.set(2026, Calendar.SEPTEMBER, 6, 10, 0);
        JSONObject snapshot = new JSONObject("""
            {"personal":[{"term_code":"demo-term","weeks":[{"number":2,"start_date":"2026-09-06","end_date":"2026-09-12"}],
            "courses":[{"weekday":7,"weeks":[2],"start_section":10,"course_name":"晚课"},
            {"weekday":7,"weeks":[2],"start_section":2,"course_name":"上午课","location":"示例楼 101","campus":"浑南校区"},
            {"weekday":7,"weeks":[3],"start_section":1,"course_name":"其他周课"},
            {"weekday":1,"weeks":[2],"start_section":1,"course_name":"周一课","start_time":"08:10"}]}]}
            """);
        JSONObject projected = TimetableWidget.project(snapshot, today);
        assertEquals(2, projected.getJSONArray("2026-09-06").length());
        assertEquals(565, projected.getJSONArray("2026-09-06").getJSONObject(0).getInt("minute"));
        assertEquals("示例楼 101", projected.getJSONArray("2026-09-06").getJSONObject(0).getString("place"));
        assertEquals(490, projected.getJSONArray("2026-09-07").getJSONObject(0).getInt("minute"));
        assertEquals(0, projected.getJSONArray("2026-09-08").length());
        assertEquals("晚课", TimetableWidget.nextCourse(projected, today).course.getString("title"));
        today.set(Calendar.HOUR_OF_DAY, 20);
        TimetableWidget.NextCourse next = TimetableWidget.nextCourse(projected, today);
        assertEquals("09/07", next.date);
        assertEquals("周一课", next.course.getString("title"));
        assertEquals(0, TimetableWidget.project(new JSONObject(), today).length());
    }

    @Test
    public void nextCourseUsesOfficialCampusTimeAndSkipsUnknownMorning() throws Exception {
        Calendar today = Calendar.getInstance(TimeZone.getTimeZone("Asia/Shanghai"));
        today.set(2026, Calendar.SEPTEMBER, 6, 9, 12);
        JSONObject snapshot = new JSONObject("""
            {"personal":[{"weeks":[{"number":2,"start_date":"2026-09-06","end_date":"2026-09-12"},
                {"number":4,"start_date":"2026-09-20","end_date":"2026-09-26"}],
              "campuses":[{"name":"南湖校区","code":"main"}],
              "sections_by_campus":{"main":[{"number":2,"start_time":"09:13"}]},
              "courses":[{"weekday":7,"weeks":[2],"start_section":1,"course_name":"未知校区"},
                {"weekday":7,"weeks":[2],"start_section":2,"course_name":"校区时间","campus":"南湖校区"},
                {"weekday":7,"weeks":[4],"start_section":1,"course_name":"隔周课","start_time":"08:00"}]}]}
            """);
        JSONObject projected = TimetableWidget.project(snapshot, today);
        assertEquals("校区时间", TimetableWidget.nextCourse(projected, today).course.getString("title"));
        today.set(Calendar.MINUTE, 13);
        TimetableWidget.NextCourse later = TimetableWidget.nextCourse(projected, today);
        assertEquals("09/20", later.date);
        assertEquals("隔周课", later.course.getString("title"));
        assertNull(TimetableWidget.nextCourse(new JSONObject("{\"2026-09-06\":\"旧版课表文本\"}"), today));
    }
}
