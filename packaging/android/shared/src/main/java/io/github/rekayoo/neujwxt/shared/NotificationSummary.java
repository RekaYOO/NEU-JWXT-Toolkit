package io.github.rekayoo.neujwxt.shared;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

public final class NotificationSummary {
    private static final String GRADE_FALLBACK = "教务成绩有变化，点击查看成绩明细";

    private NotificationSummary() { }

    public static String body(boolean hide, String fallback, String details) {
        if (hide || details.trim().isEmpty()) return fallback;
        return details;
    }

    static String grades(JSONArray before, JSONArray after) {
        if (before == null || after == null) return "";
        Set<String> ambiguous = new HashSet<>();
        Map<String, JSONObject> previous = index(before, ambiguous);
        Map<String, JSONObject> current = index(after, ambiguous);
        List<String> changes = new ArrayList<>();
        for (Map.Entry<String, JSONObject> entry : current.entrySet()) {
            if (ambiguous.contains(entry.getKey())) continue;
            JSONObject next = entry.getValue();
            JSONObject old = previous.get(entry.getKey());
            String name = field(next, "name", 35);
            if (name.isEmpty()) continue;
            String score = field(next, "score", 24);
            if (old == null) changes.add(name + " 新出成绩 " + score);
            else if (!old.optString("score").equals(next.optString("score"))) {
                changes.add(name + " " + field(old, "score", 24) + " → " + score);
            } else if (Double.compare(old.optDouble("gpa"), next.optDouble("gpa")) != 0) {
                changes.add(name + " 绩点 " + field(old, "gpa", 10) + " → " + field(next, "gpa", 10));
            }
        }
        for (Map.Entry<String, JSONObject> entry : previous.entrySet()) {
            String name = field(entry.getValue(), "name", 35);
            if (!name.isEmpty() && !ambiguous.contains(entry.getKey()) && !current.containsKey(entry.getKey())) {
                changes.add(name + " 成绩记录已移除");
            }
        }
        return lines(changes, "项成绩变化");
    }

    private static Map<String, JSONObject> index(JSONArray rows, Set<String> ambiguous) {
        Map<String, JSONObject> result = new LinkedHashMap<>();
        for (int index = 0; index < rows.length(); index++) {
            JSONObject row = rows.optJSONObject(index);
            if (row == null) continue;
            String key = row.optString("key");
            if (key.isEmpty()) continue;
            if (result.put(key, row) != null) ambiguous.add(key);
        }
        return result;
    }

    static String lines(List<String> items, String overflowLabel) {
        if (items.size() <= 3) return String.join("\n", items);
        return String.join("\n", items.subList(0, 3)) + "\n另有 " + (items.size() - 3) + " " + overflowLabel;
    }

    static String field(JSONObject row, String key, int limit) {
        String text = row.isNull(key) ? "" : row.optString(key).replaceAll("[\\p{Cc}\\p{Cf}\\s]+", " ").trim();
        if (text.codePointCount(0, text.length()) <= limit) return text;
        return text.substring(0, text.offsetByCodePoints(0, limit)) + "…";
    }

    public static String gradeFallback() {
        return GRADE_FALLBACK;
    }
}
