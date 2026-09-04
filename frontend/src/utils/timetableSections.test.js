import {
  TIMETABLE_CAMPUS_SCHEDULES,
  TIMETABLE_CAMPUS_SCHEDULE_VERSION,
  resolveTimetableSections,
  timetableCampusScheduleKey,
} from './timetableSections';

const emptySections = Array.from({ length: 12 }, (_, index) => ({
  number: index + 1,
  name: `第${index + 1}节`,
  start_time: '',
  end_time: '',
}));

describe('timetable campus schedules', () => {
  test('ships a versioned twelve-section schedule for every supported campus', () => {
    expect(TIMETABLE_CAMPUS_SCHEDULE_VERSION).toBeTruthy();
    expect(Object.keys(TIMETABLE_CAMPUS_SCHEDULES)).toEqual(['nanhu', 'east', 'network', 'hunnan']);
    Object.values(TIMETABLE_CAMPUS_SCHEDULES).forEach(profile => {
      expect(profile.sections).toHaveLength(12);
    });
  });

  test('uses the same schedule for Nanhu, east campus and network campus', () => {
    expect(TIMETABLE_CAMPUS_SCHEDULES.east.sections).toEqual(TIMETABLE_CAMPUS_SCHEDULES.nanhu.sections);
    expect(TIMETABLE_CAMPUS_SCHEDULES.network.sections).toEqual(TIMETABLE_CAMPUS_SCHEDULES.nanhu.sections);
    expect(TIMETABLE_CAMPUS_SCHEDULES.nanhu.sections[0]).toMatchObject({
      start_time: '08:00', end_time: '08:45',
    });
    expect(TIMETABLE_CAMPUS_SCHEDULES.nanhu.sections[11]).toMatchObject({
      start_time: '21:15', end_time: '22:00',
    });
  });

  test('keeps only the Hunnan morning half an hour later', () => {
    expect(TIMETABLE_CAMPUS_SCHEDULES.hunnan.sections.slice(0, 4).map(item => item.start_time))
      .toEqual(['08:30', '09:25', '10:30', '11:25']);
    expect(TIMETABLE_CAMPUS_SCHEDULES.hunnan.sections.slice(4))
      .toEqual(TIMETABLE_CAMPUS_SCHEDULES.nanhu.sections.slice(4));
  });

  test('recognizes official names and confirmed timetable codes without overriding a supplied name', () => {
    expect(timetableCampusScheduleKey('南湖')).toBe('nanhu');
    expect(timetableCampusScheduleKey('浑南校区')).toBe('hunnan');
    expect(timetableCampusScheduleKey('东校区')).toBe('east');
    expect(timetableCampusScheduleKey('网络区')).toBe('network');
    expect(timetableCampusScheduleKey('00')).toBe('nanhu');
    expect(timetableCampusScheduleKey('01')).toBe('hunnan');
    expect(timetableCampusScheduleKey({ code: '01', name: '南湖校区' })).toBe('nanhu');
  });

  test('fills an old browser snapshot whose official section times are empty', () => {
    const resolved = resolveTimetableSections({
      sections: emptySections,
      campusCode: '01',
      campusName: '浑南校区',
    });
    expect(resolved.map(item => item.start_time)).toEqual([
      '08:30', '09:25', '10:30', '11:25', '14:00', '14:55',
      '16:00', '16:55', '18:30', '19:25', '20:20', '21:15',
    ]);
    expect(resolved.every(item => item.time_source === 'builtin')).toBe(true);
  });

  test('lets official section values override bundled data', () => {
    const resolved = resolveTimetableSections({
      sections: [{ number: 1, name: '第一节', start_time: '08:10', end_time: '08:55' }],
      campusCode: '00',
      campusName: '南湖校区',
    });
    expect(resolved[0]).toMatchObject({
      start_time: '08:10', end_time: '08:55', time_source: 'official',
    });
    expect(resolved[1]).toMatchObject({ start_time: '08:55', end_time: '09:40' });
  });

  test('lets a self-consistent official course block override bundled section times', () => {
    const resolved = resolveTimetableSections({
      sections: emptySections,
      campusCode: '00',
      campusName: '南湖校区',
      courses: [{
        campus: '南湖校区', start_section: 1, end_section: 2,
        start_time: '08:10', end_time: '09:50',
      }],
    });
    expect(resolved[0]).toMatchObject({ start_time: '08:10', end_time: '08:55', time_source: 'course' });
    expect(resolved[1]).toMatchObject({ start_time: '09:05', end_time: '09:50', time_source: 'course' });
  });

  test('splits differing morning clocks but collapses identical afternoon clocks in all-campus mode', () => {
    const resolved = resolveTimetableSections({
      sections: emptySections,
      campusCode: 'all',
      campuses: [
        { code: 'all', name: '全部校区' },
        { code: '00', name: '南湖校区' },
        { code: '01', name: '浑南校区' },
      ],
    });
    expect(resolved[0].time_variants.map(item => item.start_time)).toEqual(['08:00', '08:30']);
    expect(resolved[0].time_source).toBe('multiple');
    expect(resolved[4].time_variants).toHaveLength(1);
    expect(resolved[4]).toMatchObject({ start_time: '14:00', end_time: '14:45' });
  });
});
