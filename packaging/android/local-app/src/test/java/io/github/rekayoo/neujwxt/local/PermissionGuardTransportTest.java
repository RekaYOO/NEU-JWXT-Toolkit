package io.github.rekayoo.neujwxt.local;

import org.junit.Test;

import io.github.rekayoo.neujwxt.shared.NativeRequest;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

public final class PermissionGuardTransportTest {
    @Test
    public void detectsOnlyOperationsWhichEnableUnattendedWork() throws Exception {
        assertTrue(PermissionGuardTransport.enablesBackgroundTask(NativeRequest.parse(
            "{\"method\":\"PATCH\",\"path\":\"/api/grade-tracking/enabled\",\"body\":\"{\\\"enabled\\\":true}\"}"
        )));
        assertTrue(PermissionGuardTransport.enablesBackgroundTask(NativeRequest.parse(
            "{\"method\":\"POST\",\"path\":\"/api/course-selection/jwxk/automation/tasks/start\"}"
        )));
        assertTrue(PermissionGuardTransport.enablesBackgroundTask(NativeRequest.parse(
            "{\"method\":\"PATCH\",\"path\":\"/api/%67rade-tracking/enabled\",\"body\":\"{\\\"enabled\\\":true}\"}"
        )));
        assertTrue(PermissionGuardTransport.enablesBackgroundTask(NativeRequest.parse(
            "{\"method\":\"POST\",\"path\":\"/api/course-selection/jwxk/automation/tasks/%73tart?x=1\"}"
        )));
        assertTrue(PermissionGuardTransport.enablesBackgroundTask(NativeRequest.parse(
            "{\"method\":\"PUT\",\"path\":\"/api/grade-tracking/config?x=1\",\"body\":\"{\\\"enabled\\\": true}\"}"
        )));
        assertTrue(PermissionGuardTransport.enablesBackgroundTask(NativeRequest.parse(
            "{\"method\":\"PATCH\",\"path\":\"/api/grade-tracking/enabled\",\"body\":\"{\\\"enabled\\\": 1}\"}"
        )));
        assertFalse(PermissionGuardTransport.enablesBackgroundTask(NativeRequest.parse(
            "{\"method\":\"PUT\",\"path\":\"/api/grade-tracking/config\",\"body\":\"{\\\"interval_minutes\\\":10}\"}"
        )));
        assertFalse(PermissionGuardTransport.enablesBackgroundTask(NativeRequest.parse(
            "{\"method\":\"PATCH\",\"path\":\"/api/grade-tracking/enabled\",\"body\":\"{\\\"enabled\\\":false}\"}"
        )));
    }
}
