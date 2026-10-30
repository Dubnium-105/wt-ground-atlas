/** A released change belongs to the baseline; an empty plan is no new version. */
export function hasFutureScenario(scenario) {
  return Array.isArray(scenario?.entries) && scenario.entries.length > 0 &&
    !['released', 'unavailable', 'not_imported', 'unknown'].includes(scenario.status);
}
