const loadApiWithAxios = () => {
  let rejectResponse;
  let apiModule;
  const client = {
    get: jest.fn(),
    post: jest.fn(),
    put: jest.fn(),
    request: jest.fn(),
    interceptors: {
      response: {
        use: jest.fn((resolve, reject) => {
          rejectResponse = reject;
        }),
      },
    },
  };

  jest.resetModules();
  jest.doMock('axios', () => ({
    __esModule: true,
    default: { create: jest.fn(() => client) },
  }));
  jest.isolateModules(() => {
    apiModule = require('./api');
  });
  return { client, rejectResponse, apiModule };
};

describe('JWXK automation settings API', () => {
  test('does not submit response-only batch and SMTP fields', async () => {
    const { client, apiModule } = loadApiWithAxios();
    client.put.mockResolvedValue({ data: { strategy_schedule_mode: 'final_windows' } });

    await apiModule.updateJwxkAutomationSettings('batch-1', {
      batch_code: 'batch-1', batch_name: '轮次', smtp_configured: true,
      smtp_status: '邮件通道可用', strategy_schedule_mode: 'final_windows',
      rebalance_seconds: 600, final_check_minutes: 4,
      final_notice_minutes: 20, final_notice_latest_time: '21:30',
      mail_enabled: true, notify_round_start: true, notify_round_end: true,
      unknown_readonly_field: 'ignored',
    });

    expect(client.put).toHaveBeenCalledWith(
      '/api/course-selection/jwxk/batches/batch-1/automation-settings',
      {
        strategy_schedule_mode: 'final_windows',
        rebalance_seconds: 600,
        final_check_minutes: 4,
        final_notice_minutes: 20,
        final_notice_latest_time: '21:30',
        mail_enabled: true,
        notify_round_start: true,
        notify_round_end: true,
      },
    );
  });

  test('targets JWXK for in-page WebVPN password and QR recovery', async () => {
    const { client, apiModule } = loadApiWithAxios();
    client.post.mockResolvedValue({ data: { success: true, status: 'pending' } });

    await apiModule.startWebVPNQRLogin('20250001', 'jwxk');
    await apiModule.startWebVPNPasswordLogin('20250001', 'secret', true, 'jwxk');

    expect(client.post).toHaveBeenNthCalledWith(1, '/api/webvpn/qr/start', {
      username: '20250001', target_service: 'jwxk',
    });
    expect(client.post).toHaveBeenNthCalledWith(2, '/api/webvpn/password/start', {
      username: '20250001', password: 'secret', remember: true, target_service: 'jwxk',
    });
  });

  test('saves the JWXK route locally before starting a separate short probe', async () => {
    const { client, apiModule } = loadApiWithAxios();
    client.put.mockResolvedValue({ data: { service_auth_state: 'checking' } });

    await apiModule.updateJwxkSettings('follow');

    expect(client.put).toHaveBeenCalledWith(
      '/api/course-selection/jwxk/settings',
      { network_mode: 'follow' },
      { params: { probe: false }, timeout: 5000 },
    );
  });

  test('treats an unavailable avatar as an empty optional resource', async () => {
    const { client, apiModule } = loadApiWithAxios();
    client.get.mockResolvedValue({
      status: 204,
      headers: { 'x-avatar-unavailable': 'true' },
      data: new Blob([]),
    });

    await expect(apiModule.getUserAvatar(true)).resolves.toBeNull();
    expect(client.get).toHaveBeenCalledWith('/api/user/avatar', {
      params: { refresh: true }, responseType: 'blob', timeout: 12000,
    });
  });
});

describe('WebVPN error compatibility helpers', () => {
  test('prefer stable error codes and recognize expired flows', () => {
    const { apiModule } = loadApiWithAxios();
    expect(apiModule.getWebVPNErrorCode({
      error_code: 'WEBVPN_FLOW_EXPIRED',
      message: '旧版提示',
    })).toBe('WEBVPN_FLOW_EXPIRED');
    expect(apiModule.isWebVPNFlowInvalid({
      error_code: 'WEBVPN_FLOW_EXPIRED',
    })).toBe(true);
    expect(apiModule.getWebVPNErrorMessage({
      response: { data: { message: '服务端提示' } },
    })).toBe('服务端提示');
    expect(apiModule.isWebVPNCampusNetworkBlocked({
      error_code: 'WEBVPN_CAMPUS_NETWORK_BLOCKED',
    })).toBe(true);
    expect(apiModule.isWebVPNCampusNetworkBlocked({
      error_code: 'WEBVPN_UNKNOWN_ERROR',
    })).toBe(false);
  });
});

describe('client bootstrap and request coalescing', () => {
  test('reuses timetable data already returned by the aggregate bootstrap', async () => {
    const { client, apiModule } = loadApiWithAxios();
    client.get.mockResolvedValue({
      data: {
        auth: { is_logged_in: true },
        timetable: { current: '2026-2027-1', terms: [], personal: [] },
      },
    });

    await apiModule.getClientBootstrap();
    const timetable = await apiModule.getTimetableBootstrap();

    expect(timetable.current).toBe('2026-2027-1');
    expect(client.get).toHaveBeenCalledTimes(1);
  });

  test('coalesces simultaneous automation status polls for the same batch', async () => {
    const { client, apiModule } = loadApiWithAxios();
    let resolve;
    client.get.mockReturnValue(new Promise(done => { resolve = done; }));

    const first = apiModule.listJwxkAutomationTasks('batch-1');
    const second = apiModule.listJwxkAutomationTasks('batch-1');
    resolve({ data: { tasks: [] } });

    await expect(first).resolves.toEqual({ tasks: [] });
    await expect(second).resolves.toEqual({ tasks: [] });
    expect(client.get).toHaveBeenCalledTimes(1);
  });
});

describe('Evaluation API term discovery', () => {
  test('omits xnxq so the backend can discover the current evaluation cycle', async () => {
    const { client, apiModule } = loadApiWithAxios();
    client.get.mockResolvedValue({ data: { tasks: [] } });

    await apiModule.getEvaluationTasks();
    await apiModule.getEvaluationCourses('task-id');

    expect(client.get).toHaveBeenNthCalledWith(1, '/api/evaluation/tasks');
    expect(client.get).toHaveBeenNthCalledWith(
      2,
      '/api/evaluation/tasks/task-id/courses'
    );
  });

  test('still forwards an explicitly selected evaluation cycle', async () => {
    const { client, apiModule } = loadApiWithAxios();
    client.get.mockResolvedValue({ data: { tasks: [] } });

    await apiModule.getEvaluationTasks('2026-2027-1');
    await apiModule.getEvaluationCourses('task-id', '2026-2027-1');

    expect(client.get).toHaveBeenNthCalledWith(1, '/api/evaluation/tasks', {
      params: { xnxq: '2026-2027-1' },
    });
    expect(client.get).toHaveBeenNthCalledWith(
      2,
      '/api/evaluation/tasks/task-id/courses',
      { params: { xnxq: '2026-2027-1' } }
    );
  });
});

describe('Evaluation API safety mode', () => {
  test('preview is the default and real submission requires explicit false', async () => {
    const { client, apiModule } = loadApiWithAxios();
    client.post.mockResolvedValue({ data: { success: true, dry_run: true } });

    await apiModule.submitEvaluation('task', 'course');
    await apiModule.submitEvaluation(
      'task', 'course', 'highest', null, null, false,
    );

    expect(client.post).toHaveBeenNthCalledWith(1, '/api/evaluation/submit', {
      task_id: 'task',
      xspjid: 'course',
      strategy: 'highest',
      dry_run: true,
    });
    expect(client.post).toHaveBeenNthCalledWith(2, '/api/evaluation/submit', {
      task_id: 'task',
      xspjid: 'course',
      strategy: 'highest',
      dry_run: false,
    });
  });

  test('batch preview also defaults to non-writing mode', async () => {
    const { client, apiModule } = loadApiWithAxios();
    client.post.mockResolvedValue({ data: { success_count: 1, dry_run: true } });

    await apiModule.batchEvaluation('task', 'lowest', null, ['course']);

    expect(client.post).toHaveBeenCalledWith('/api/evaluation/batch', {
      task_id: 'task',
      strategy: 'lowest',
      xspjids: ['course'],
      dry_run: true,
    });
  });
});

describe('API silent authentication recovery', () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  test('401 时静默恢复并自动重试原请求一次', async () => {
    const { client, rejectResponse } = loadApiWithAxios();
    const dispatch = jest.spyOn(window, 'dispatchEvent');
    client.get.mockResolvedValue({ data: { is_logged_in: true } });
    client.request.mockResolvedValue({ data: { ok: true } });

    const result = await rejectResponse({
      response: { status: 401, data: {} },
      config: { url: '/api/academic-report/cache', method: 'get' },
    });

    expect(client.get).toHaveBeenCalledWith('/api/status', {
      skipAuthRedirect: true,
    });
    expect(client.request).toHaveBeenCalledWith(expect.objectContaining({
      url: '/api/academic-report/cache',
      _silentAuthRecoveryRetried: true,
    }));
    expect(dispatch).not.toHaveBeenCalledWith(
      expect.objectContaining({ type: 'neu-auth-required' })
    );
    expect(result).toEqual({ data: { ok: true } });
    dispatch.mockRestore();
  });

  test('恢复失败后才通知应用进入登录失效流程', async () => {
    const { client, rejectResponse } = loadApiWithAxios();
    const events = [];
    const listener = event => events.push(event.type);
    window.addEventListener('neu-auth-required', listener);
    client.get.mockResolvedValue({ data: { is_logged_in: false } });
    const error = {
      response: { status: 401, data: {} },
      config: { url: '/api/academic-report/cache', method: 'get' },
    };

    await expect(rejectResponse(error)).rejects.toBe(error);

    expect(client.request).not.toHaveBeenCalled();
    expect(events).toEqual(['neu-auth-required']);
    window.removeEventListener('neu-auth-required', listener);
  });

  test('已重试请求再次 401 时不循环自动登录', async () => {
    const { client, rejectResponse } = loadApiWithAxios();
    const error = {
      response: { status: 401, data: {} },
      config: {
        url: '/api/academic-report/cache',
        method: 'get',
        _silentAuthRecoveryRetried: true,
      },
    };

    await expect(rejectResponse(error)).rejects.toBe(error);

    expect(client.get).not.toHaveBeenCalled();
    expect(client.request).not.toHaveBeenCalled();
  });

  test('主动退出标记存在时不再自动恢复', async () => {
    sessionStorage.setItem('neu_manual_logout', '1');
    const { client, rejectResponse } = loadApiWithAxios();
    const events = [];
    const listener = event => events.push(event.type);
    window.addEventListener('neu-auth-required', listener);
    const error = {
      response: { status: 401, data: {} },
      config: { url: '/api/academic-report/cache', method: 'get' },
    };

    await expect(rejectResponse(error)).rejects.toBe(error);

    expect(client.get).not.toHaveBeenCalled();
    expect(client.request).not.toHaveBeenCalled();
    expect(events).toEqual([]);
    window.removeEventListener('neu-auth-required', listener);
  });

  test('恢复进行期间发生主动退出时不重试迟到请求', async () => {
    let finishRecovery;
    const { client, rejectResponse } = loadApiWithAxios();
    client.get.mockReturnValue(new Promise(resolve => {
      finishRecovery = resolve;
    }));
    const error = {
      response: { status: 401, data: {} },
      config: { url: '/api/academic-report/cache', method: 'get' },
    };

    const pending = rejectResponse(error);
    sessionStorage.setItem('neu_manual_logout', '1');
    finishRecovery({ data: { is_logged_in: true } });

    await expect(pending).rejects.toBe(error);
    expect(client.request).not.toHaveBeenCalled();
  });

  test('JWXK 401 使用选课子会话状态恢复而不是主教务状态', async () => {
    const { client, rejectResponse } = loadApiWithAxios();
    const dispatch = jest.spyOn(window, 'dispatchEvent');
    client.get.mockResolvedValue({ data: { service_authenticated: true } });
    client.request.mockResolvedValue({ data: { selected: [] } });

    await rejectResponse({
      response: { status: 401, data: {} },
      config: {
        url: '/api/course-selection/jwxk/selected',
        method: 'post',
        authRecoveryScope: 'jwxk',
      },
    });

    expect(client.get).toHaveBeenCalledWith(
      '/api/course-selection/jwxk/status',
      { skipAuthRedirect: true },
    );
    expect(client.request).toHaveBeenCalledWith(expect.objectContaining({
      authRecoveryScope: 'jwxk',
      _silentAuthRecoveryRetried: true,
    }));
    expect(dispatch).not.toHaveBeenCalledWith(
      expect.objectContaining({ type: 'neu-auth-required' }),
    );
    dispatch.mockRestore();
  });

  test('JWXK 子会话恢复失败不会误报整个教务会话失效', async () => {
    const { client, rejectResponse } = loadApiWithAxios();
    const events = [];
    const listener = event => events.push(event.type);
    window.addEventListener('neu-auth-required', listener);
    client.get.mockResolvedValue({ data: {
      primary_authenticated: true,
      service_authenticated: false,
    } });
    const error = {
      response: { status: 401, data: {} },
      config: {
        url: '/api/course-selection/jwxk/selected',
        method: 'post',
        authRecoveryScope: 'jwxk',
      },
    };

    await expect(rejectResponse(error)).rejects.toBe(error);

    expect(client.request).not.toHaveBeenCalled();
    expect(events).toEqual([]);
    window.removeEventListener('neu-auth-required', listener);
  });
});

describe('JWXK mutation authentication boundary', () => {
  test('write requests opt out of frontend replay because backend recovers before mutation', async () => {
    const { client, apiModule } = loadApiWithAxios();
    client.post.mockResolvedValue({ data: { success: true } });

    await apiModule.confirmJwxkBatch('BATCH-1');
    await apiModule.selectJwxkCourse({ batch_code: 'BATCH-1' });
    await apiModule.deselectJwxkCourse({ batch_code: 'BATCH-1' });
    await apiModule.applyJwxkWeights({ batch_code: 'BATCH-1', items: [] });

    expect(client.post).toHaveBeenNthCalledWith(
      1,
      '/api/course-selection/jwxk/batches/confirm',
      { batch_code: 'BATCH-1', acknowledged: true },
      { skipAuthRedirect: true },
    );
    expect(client.post).toHaveBeenNthCalledWith(
      2,
      '/api/course-selection/jwxk/courses/select',
      { batch_code: 'BATCH-1' },
      { skipAuthRedirect: true },
    );
    expect(client.post).toHaveBeenNthCalledWith(
      3,
      '/api/course-selection/jwxk/courses/deselect',
      { batch_code: 'BATCH-1' },
      { skipAuthRedirect: true },
    );
    expect(client.post).toHaveBeenNthCalledWith(
      4,
      '/api/course-selection/jwxk/weights/apply',
      { batch_code: 'BATCH-1', items: [] },
      { skipAuthRedirect: true },
    );
  });
});
