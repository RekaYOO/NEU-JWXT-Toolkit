/** Apply the server-declared post-mutation consistency actions once each. */
export const synchronizeEvaluationViews = async (
  refetches = [],
  { loadTasks, loadCourses } = {},
) => {
  const requested = new Set(refetches);
  const jobs = [];
  if (requested.has('evaluation-tasks') && typeof loadTasks === 'function') {
    jobs.push(loadTasks());
  }
  if (requested.has('evaluation-courses') && typeof loadCourses === 'function') {
    jobs.push(loadCourses());
  }
  await Promise.all(jobs);
};
