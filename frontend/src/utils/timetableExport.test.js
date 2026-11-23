import { saveNativeFile } from '../services/nativeBridge';
import { downloadTimetable } from './timetableExport';

jest.mock('../services/nativeBridge', () => ({ saveNativeFile: jest.fn() }));

const fixture = {
  termCode: '2026-1', mode: 'personal', view: 'week', week: 3,
  days: { 4: [{ course_name: '实验', weekday: 4, agenda_source: '2026-04-06', campus: '南校区', cell_details: ['双周实验'], internal: 'omit' }] },
  schedule: { practices: [{ course_name: '实习' }] },
};

test('mobile save receives visible arrangements and other course groups', async () => {
  saveNativeFile.mockReturnValue(true);
  downloadTimetable(fixture);
  const [filename, type, blob] = saveNativeFile.mock.calls[0];
  expect(filename).toContain('第3周');
  expect(type).toBe('application/json');
  const reader = new FileReader();
  const text = new Promise(resolve => { reader.onload = () => resolve(reader.result); });
  reader.readAsText(blob);
  expect(JSON.parse(await text)).toMatchObject({ week: 3, courses: [{ course_name: '实验', weekday: 4, agenda_source: '2026-04-06', campus: '南校区', cell_details: ['双周实验'] }], practices: fixture.schedule.practices, unscheduled: [] });
  expect(JSON.parse(reader.result).courses[0]).not.toHaveProperty('internal');
});

test('browser export downloads the selected term and releases the file URL', () => {
  jest.useFakeTimers();
  saveNativeFile.mockReturnValue(false);
  URL.createObjectURL = jest.fn(() => 'blob:test');
  URL.revokeObjectURL = jest.fn();
  const click = jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function () {
    expect(this.download).toContain('全学期');
    expect(this.href).toBe('blob:test');
  });
  try {
    downloadTimetable({ ...fixture, view: 'term' });
    expect(click).toHaveBeenCalledTimes(1);
    jest.runAllTimers();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:test');
  } finally {
    click.mockRestore();
    jest.useRealTimers();
  }
});
