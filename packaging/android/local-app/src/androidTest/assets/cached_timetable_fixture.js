(function () {
  const namespace = 'account:cac4ed';
  const term = '2026-2027-1';
  const sections = [{number: 1, name: '第1节'}, {number: 2, name: '第2节'}];
  const personal = {
    term_code: term,
    campuses: [{code: '00', name: '测试校区'}],
    weeks: [{number: 1, name: '第1周', current: true}],
    sections, sections_by_campus: {'00': sections},
    courses: [{
      id: 'startup-cache-fixture', course_name: '启动缓存验收课程',
      // Mobile opens today's column, so the visible fixture must follow the device date.
      weekday: new Date().getDay() || 7, start_section: 1, end_section: 2, weeks: [1],
      teachers: ['测试教师'], location: '测试教室',
    }],
    unscheduled: [], practices: [], is_fresh: true,
  };
  const open = indexedDB.open('neu-toolbox-browser-cache', 1);
  open.onupgradeneeded = function () {
    const store = open.result.createObjectStore('timetable', {keyPath: 'key'});
    store.createIndex('namespace', 'namespace', {unique: false});
    store.createIndex('kind', 'kind', {unique: false});
  };
  open.onsuccess = function () {
    const tx = open.result.transaction('timetable', 'readwrite');
    const store = tx.objectStore('timetable');
    store.put({
      key: namespace + ':index', namespace, kind: 'index', version: 1,
      payload: {
        terms: [{code: term, name: '测试学期', current: true}],
        current: term,
        viewState: {termCode: term, campusCode: '00', weekNumber: 1, viewMode: 'week'},
      },
    });
    store.put({
      key: namespace + ':personal:' + term, namespace, kind: 'personal', version: 1, payload: personal,
    });
    tx.oncomplete = function () {
      open.result.close();
      localStorage.setItem('neu-toolbox-timetable-recovery-namespace', namespace);
      localStorage.setItem('neu_toolbox:defaultTimetableOnOpen', 'true');
      sessionStorage.removeItem('neu_manual_logout');
      sessionStorage.removeItem('neu_offline_mode');
      window.__cachedFixtureReady = true;
    };
  };
}());
