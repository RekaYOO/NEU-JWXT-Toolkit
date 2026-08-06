import { synchronizeEvaluationViews } from './evaluationConsistency';

describe('evaluation consistency actions', () => {
  test('real submission refetches both task counts and course state once', async () => {
    const loadTasks = jest.fn().mockResolvedValue(undefined);
    const loadCourses = jest.fn().mockResolvedValue(undefined);

    await synchronizeEvaluationViews(
      ['evaluation-tasks', 'evaluation-courses', 'evaluation-tasks'],
      { loadTasks, loadCourses },
    );

    expect(loadTasks).toHaveBeenCalledTimes(1);
    expect(loadCourses).toHaveBeenCalledTimes(1);
  });

  test('preview response without refetches does not reload remote views', async () => {
    const loadTasks = jest.fn();
    const loadCourses = jest.fn();

    await synchronizeEvaluationViews([], { loadTasks, loadCourses });

    expect(loadTasks).not.toHaveBeenCalled();
    expect(loadCourses).not.toHaveBeenCalled();
  });
});
