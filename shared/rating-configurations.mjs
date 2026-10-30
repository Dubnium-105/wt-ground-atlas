/** Mode/version-specific BR configuration resolver.
 * No inferred BR modifiers: only an explicitly selected, source-verified option
 * can change matchmaking. Independent of scoring weights and spawn-point cost. */
export const RATING_CONFIGURATION_SCHEMA = '1.0.0';
const own = (obj, key) => Object.prototype.hasOwnProperty.call(obj ?? {}, key);
const finiteBR = value => typeof value === 'number' && Number.isFinite(value) && value > 0;

function legacyBR(vehicle, version, mode) {
  if (mode === 'ground_rb') {
    const value = own(vehicle.br, version) ? vehicle.br[version] : vehicle.br?.current;
    return finiteBR(value) ? value : null;
  }
  // Wiki air RB is separate from aircraft Ground RB ("trb").
  if (mode === 'air_rb' && vehicle.kind === 'aircraft') {
    const value = vehicle.source_battle_ratings?.rb;
    return finiteBR(value) ? value : null;
  }
  return null;
}

/** A declared but unverified catalogue never falls back to the old fixed BR. */
export function hasRatingConfigurations(vehicle, mode = 'ground_rb') {
  return own(vehicle?.rating_configurations, mode);
}

function optionBR(option, version) {
  const raw = own(option?.br, version) ? option.br[version]
    : version === 'planned' ? option?.br?.current : null;
  return finiteBR(raw) ? raw : null;
}

function readCatalog(vehicle, mode) {
  const catalog = vehicle?.rating_configurations?.[mode];
  if (!catalog || catalog.status !== 'official_verified'
      || !Array.isArray(catalog.options) || !catalog.options.length
      || !catalog.source?.url) {
    return {errors: ['unverified_rating_configuration_catalog'], options: []};
  }
  const ids = catalog.options.map(option => option?.id);
  if (ids.some(id => typeof id !== 'string' || !id.trim())
      || new Set(ids).size !== ids.length) {
    return {errors: ['invalid_or_duplicate_rating_configuration_id'], options: []};
  }
  return {errors: [], options: catalog.options};
}

/** Selection and unlock are different: researched weapons never silently raise BR.
 * unlocked_modification_ids may be omitted for a hypothetical score; consumers
 * needing an owned lineup must set require_unlocked_modifications=true. */
export function resolveRatingConfiguration(vehicle, {
  mode = 'ground_rb', version = 'current', configuration_id = null,
  unlocked_modification_ids = null, require_unlocked_modifications = false,
} = {}) {
  if (!hasRatingConfigurations(vehicle, mode)) {
    const br = legacyBR(vehicle, version, mode);
    return {status: br === null ? 'unknown' : 'legacy', br,
      configuration_id: null, option: null,
      errors: br === null ? ['unknown_battle_rating'] : [], warnings: []};
  }
  const catalog = readCatalog(vehicle, mode);
  if (catalog.errors.length) {
    return {status: 'invalid', br: null, configuration_id, option: null,
      errors: catalog.errors, warnings: []};
  }
  if (typeof configuration_id !== 'string' || !configuration_id.trim()) {
    return {status: 'invalid', br: null, configuration_id: null, option: null,
      errors: ['rating_configuration_selection_required'], warnings: []};
  }
  const option = catalog.options.find(row => row.id === configuration_id);
  if (!option) return {status: 'invalid', br: null, configuration_id, option: null,
    errors: ['unknown_rating_configuration'], warnings: []};
  const errors = [], warnings = [];
  const br = optionBR(option, version);
  if (br === null) errors.push('unverified_configuration_br');
  if (version === 'planned' && !own(option.br, 'planned'))
    warnings.push('planned_br_inherits_current');
  const requirements = option.required_modification_ids;
  if (!Array.isArray(requirements) || requirements.some(id => typeof id !== 'string')) {
    warnings.push('modification_requirements_unverified');
    if (require_unlocked_modifications) errors.push('modification_requirements_unverified');
  } else if (requirements.length) {
    if (!Array.isArray(unlocked_modification_ids)) {
      warnings.push('modification_unlock_state_unknown');
      if (require_unlocked_modifications) errors.push('modification_unlock_state_unknown');
    } else if (requirements.some(id => !unlocked_modification_ids.includes(id))) {
      errors.push('required_modification_not_unlocked');
    }
  }
  return {status: errors.length ? 'invalid' : 'selected',
    br: errors.length ? null : br, configuration_id, option,
    errors, warnings};
}

export function resolveVehicleRating(vehicle, context = {}, version = context.version ?? 'current') {
  return resolveRatingConfiguration(vehicle, {
    mode: context.mode ?? 'ground_rb', version,
    configuration_id: context.configuration_by_vehicle?.[vehicle.id] ?? null,
    unlocked_modification_ids: context.unlocked_modifications_by_vehicle?.[vehicle.id] ?? null,
    require_unlocked_modifications: context.require_unlocked_modifications === true,
  });
}

/** Called *after* original physical station/preset validation. In a configured
 * tier a verified allowlist is compulsory; exclusions alone are not evidence.
 * Both allowlists, if supplied, must permit the selected loadout. */
export function validateRatingLoadout(vehicle, loadout, context = {}) {
  const rating = resolveVehicleRating(vehicle, context);
  if (!hasRatingConfigurations(vehicle, context.mode ?? 'ground_rb')) {
    return {valid: true, errors: [], warnings: [], rating};
  }
  if (rating.status !== 'selected') {
    return {valid: false, errors: rating.errors, warnings: rating.warnings, rating};
  }
  const option = rating.option;
  const byPreset = Array.isArray(option.allowed_loadout_ids);
  const byWeapon = Array.isArray(option.allowed_weapon_ids);
  const errors = [];
  if (!byPreset && !byWeapon) errors.push('configuration_loadout_policy_unverified');
  if (byPreset && !option.allowed_loadout_ids.includes(loadout?.id))
    errors.push('loadout_forbidden_by_rating_configuration');
  if (byWeapon && (loadout?.weapons ?? []).some(w =>
    typeof w.id !== 'string' || !option.allowed_weapon_ids.includes(w.id)))
    errors.push('weapon_forbidden_by_rating_configuration');
  return {valid: !errors.length, errors, warnings: rating.warnings, rating};
}

/** Enumeration is for a future lineup variant search. It never chooses for
 * the player, infers a BR from a weapon's name, or treats unverified entries as real. */
export function enumerateRatingConfigurations(vehicle, {
  mode = 'ground_rb', version = 'current',
  unlocked_modification_ids = null, require_unlocked_modifications = false,
} = {}) {
  if (!hasRatingConfigurations(vehicle, mode)) {
    const rating = resolveRatingConfiguration(vehicle, {mode, version});
    return rating.br === null ? [] : [{configuration_id: null, br: rating.br, status: 'legacy'}];
  }
  const catalog = readCatalog(vehicle, mode);
  if (catalog.errors.length) return [];
  return catalog.options.map(option => {
    const rating = resolveRatingConfiguration(vehicle, {mode, version,
      configuration_id: option.id, unlocked_modification_ids, require_unlocked_modifications});
    return {configuration_id: option.id, br: rating.br, status: rating.status,
      errors: rating.errors, warnings: rating.warnings};
  }).filter(entry => entry.status === 'selected')
    .sort((a, b) => a.br - b.br || a.configuration_id.localeCompare(b.configuration_id));
}
