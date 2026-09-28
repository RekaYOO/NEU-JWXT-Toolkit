import React from 'react';
import { CloseOutlined } from '@ant-design/icons';
import { Button, Tooltip } from 'antd';

export default function SchedulePreviewControls({ courses, courseKey, onCancel }) {
  if (!courses.length) return null;

  const single = courses.length === 1;
  return (
    <div className="jwxk-schedule-preview-controls" role="region" aria-label="课表预览操作">
      <div className="jwxk-schedule-preview-controls__summary">
        <span className="jwxk-schedule-preview-controls__indicator" aria-hidden="true" />
        <strong>正在预览{single ? '' : ` ${courses.length} 门`}</strong>
        {single && <span title={courses[0].course_name}>{courses[0].course_name}</span>}
      </div>
      <div className="jwxk-schedule-preview-controls__actions">
        {courses.map(course => (
          <Tooltip key={courseKey(course)} title={`取消“${course.course_name}”的课表预览`}>
            <Button
              size="small"
              icon={<CloseOutlined />}
              aria-label={`取消“${course.course_name}”的课表预览`}
              onClick={() => onCancel(course)}
            >
              {single ? '取消预览' : <span className="jwxk-schedule-preview-controls__course">{course.course_name}</span>}
            </Button>
          </Tooltip>
        ))}
      </div>
    </div>
  );
}
