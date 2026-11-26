package io.github.rekayoo.neujwxt.shared;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.json.JSONArray;
import org.junit.Test;

public final class CompanionJobTest {
    @Test
    public void notifiesOnlyForNewUnreadMessagesAfterRecordingBaseline() throws Exception {
        JSONArray oldMessages = new JSONArray("""
            [{"id":"fixture-1","kind":"reminder","read":false}]
            """);
        CompanionJob.MessageUpdate baseline = CompanionJob.compareMessages(null, oldMessages);
        assertEquals(0, baseline.unseen);
        assertEquals(1, baseline.seen.size());

        JSONArray updatedMessages = new JSONArray("""
            [{"id":"fixture-1","kind":"reminder","read":false},
             {"id":"fixture-1","kind":"audit","read":false,"title":"考试安排更新"},
             {"id":"fixture-1","kind":"audit","read":false},
             {"id":"fixture-2","kind":"reminder","read":true}]
            """);
        CompanionJob.MessageUpdate update = CompanionJob.compareMessages(baseline.seen, updatedMessages);
        assertEquals(1, update.unseen);
        assertEquals("考试安排更新", update.summary);
        assertEquals(3, update.seen.size());
        assertEquals(0, CompanionJob.compareMessages(update.seen, updatedMessages).unseen);
    }

    @Test
    public void gradesNeedBothARecordedBaselineAndAChangedRevision() {
        assertFalse(CompanionJob.gradeChanged("", "first"));
        assertFalse(CompanionJob.gradeChanged("first", "first"));
        assertFalse(CompanionJob.gradeChanged("first", ""));
        assertTrue(CompanionJob.gradeChanged("first", "second"));
    }

    @Test
    public void summaryShowsGradeChangesOnlyWhenDetailsAreAllowed() throws Exception {
        JSONArray before = new JSONArray("""
            [{"key":"math","name":"数学","score":"80","gpa":3},
             {"key":"repeat","name":"重修","score":"60"},{"key":"repeat","name":"重修","score":"70"}]
            """);
        JSONArray after = new JSONArray("""
            [{"key":"math","name":"数学","score":"92","gpa":4},
             {"key":"repeat","name":"重修","score":"91"},{"key":"physics","name":"物理","score":"优秀"}]
            """);
        String summary = NotificationSummary.grades(before, after);
        assertEquals("数学 80 → 92\n物理 新出成绩 优秀", summary);
        assertEquals("打开应用", NotificationSummary.body(true, "打开应用", summary));
        assertEquals(summary, NotificationSummary.body(false, "打开应用", summary));
        assertEquals("打开应用", NotificationSummary.body(false, "打开应用", NotificationSummary.grades(null, after)));
    }
}
