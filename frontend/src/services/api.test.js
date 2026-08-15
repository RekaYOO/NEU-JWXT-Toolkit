const loadApiWithAxios = () => {
  let rejectResponse;
  let apiModule;
  const client = {
    get: jest.fn(),
    post: jest.fn(),
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
