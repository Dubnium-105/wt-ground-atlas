import {resolveVehicleRating, validateRatingLoadout, hasRatingConfigurations, enumerateRatingConfigurations} from './rating-configurations.mjs';

/** Pure, deterministic Ground RB decision functions. All empirical parameters
 * are supplied through config/model.json; no vehicle-specific rules live here. */
export const ENGINE_SCHEMA = '1.0.0';
const finite = value => typeof value === 'number' && Number.isFinite(value);
const present = value => value !== null && value !== undefined;
const clamp = (value, low = 0, high = 1) => Math.min(high, Math.max(low, value));
const logistic = value => 1 / (1 + Math.exp(-value));
const copy = value => structuredClone(value);
const list = value => Array.isArray(value) ? value : value?.vehicles ?? [];
const unique = values => [...new Set(values)];
const probabilityIndexes = new WeakMap();
const pathGet = (object, path) => path.split('.').reduce((value, key) => value?.[key], object);
const pathSet = (object, path, value) => {
  const keys = path.split('.');
  let target = object;
  for (const key of keys.slice(0, -1)) target = target[key] ??= {};
  target[keys.at(-1)] = copy(value);
};
function containsNonFinite(value) {
  if (typeof value === 'number') return !Number.isFinite(value);
  if (value && typeof value === 'object') return Object.values(value).some(containsNonFinite);
  return false;
}
const mean = values => values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
const normalized = (value, scale) => finite(value) ? Math.max(0, value) / (Math.max(0, value) + scale) : null;
const contextOf = (context, config) => ({...config.defaults, ...context});
const stable = object => JSON.stringify(object, (_, value) => value && typeof value === 'object' && !Array.isArray(value) && !ArrayBuffer.isView(value) ? Object.fromEntries(Object.keys(value).sort().map(key => [key, value[key]])) : value);
/** Exact, synchronous browser/Node identity. A canonical string avoids hash
 * collisions and asynchronous crypto in the pure pairing engine. */
export function aimContextKey(suppliedContext = {}, config) {
  const context = contextOf(suppliedContext, config);
  return stable({schema: 'aim-context-v1', decision_model_version: config.model_version, aim: config.aim, unknown_prior: config.pair.unknown_prior, skill: context.skill, distance_m: context.distance_m, target_motion_mps: context.target_motion_mps, own_motion_mps: context.own_motion_mps, gun_control_deg_s: context.gun_control_deg_s ?? null, sigma_x_m: context.sigma_x_m ?? null, sigma_y_m: context.sigma_y_m ?? null});
}
/** Existing two-argument calls remain fixed-BR compatible. Optional context
 * chooses a verified configuration instead of inferring BR from weapons. */
export const getBR = (vehicle, version = 'current', context = {}) =>
  resolveVehicleRating(vehicle, context, version).br;
export function getBRs(vehicles, nation, version = 'current', context = {}) {
  return unique(list(vehicles).filter(v => !nation || v.nation === nation)
    .map(v => getBR(v, version, context)).filter(finite)).sort((a, b) => a - b);
}
export function roleOf(vehicle, config) {
  for (const [role, aliases] of Object.entries(config.roles)) if (aliases.includes(vehicle.role)) return role;
  return vehicle.kind === 'ground' ? 'main' : 'air';
}
function weighted(values, weights, unknown = null) {
  let sum = 0, used = 0, total = 0;
  for (const [key, weight] of Object.entries(weights)) {
    total += weight;
    if (finite(values[key])) {sum += values[key] * weight; used += weight;}
    else if (finite(unknown)) {sum += unknown * weight;}
  }
  return {value: (finite(unknown) ? total : used) ? sum / (finite(unknown) ? total : used) : null, coverage: total ? used / total : 0};
}

/** Apply an auditable simulated plan without mutating baseline or silently
 * accepting duplicate/conflicting rows. Both sides receive the resulting list. */
export function applyScenario(catalog, scenario = {}, version = 'planned') {
  const vehicles = copy(list(catalog));
  const byId = new Map(vehicles.map(v => [v.id, v]));
  const audit = copy(scenario.audit ?? []), applied = [], rejected = [];
  for (const vehicle of vehicles) if (!hasRatingConfigurations(vehicle)) vehicle.br = {...vehicle.br, planned: getBR(vehicle, 'current')};
  const entries = scenario.entries ?? [];
  const counts = new Map();
  for (const entry of entries) {
    const fieldKey = entry.field === 'br' || entry.field?.startsWith('br.') ? 'br' : entry.field;
    const key = `${entry.vehicle_id}|${fieldKey}`;
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  const allowed = new Set(['br', 'mobility', 'firepower', 'protection', 'sensors', 'air_defence', 'ammo', 'loadouts', 'auxiliary', 'role', 'special', 'configuration_id', 'configuration_version', 'geometry_version', 'source_version']);
  for (const entry of entries) {
    const vehicle = byId.get(entry.vehicle_id), field = entry.field ?? '';
    const fieldKey = field === 'br' || field.startsWith('br.') ? 'br' : field;
    let reason = null;
    if (!vehicle) reason = 'unmapped_vehicle';
    else if (counts.get(`${entry.vehicle_id}|${fieldKey}`) > 1) reason = 'duplicate_field';
    else if (fieldKey === 'br' && hasRatingConfigurations(vehicle)) reason = 'configuration_specific_br_change_required';
    else if (!allowed.has(field.split('.')[0]) || field.split('.').some(part => ['__proto__', 'constructor', 'prototype'].includes(part))) reason = 'unsupported_field';
    else if (!Object.prototype.hasOwnProperty.call(entry, 'old') || !Object.prototype.hasOwnProperty.call(entry, 'new')) reason = 'text_only_or_missing_old_new';
    const current = vehicle ? (field === 'br' || field.startsWith('br.') ? getBR(vehicle, 'current') : pathGet(vehicle, field)) : undefined;
    if (!reason && stable(current ?? null) !== stable(entry.old)) reason = 'old_value_conflict';
    if (!reason && (field === 'br' || field.startsWith('br.')) && !finite(entry.new)) reason = 'invalid_br';
    if (!reason && containsNonFinite(entry.new)) reason = 'nonfinite_numeric_value';
    if (!reason && present(current) && present(entry.new) && typeof current !== typeof entry.new) reason = 'field_type_conflict';
    if (reason) {
      const item = {...entry, reason, baseline_value: current ?? null, status: 'rejected'};
      rejected.push(item); audit.push(item); continue;
    }
    const path = field === 'br' || field.startsWith('br.') ? 'br.planned' : field;
    if (version === 'planned') pathSet(vehicle, path, entry.new);
    applied.push({...entry, effective_field: path});
  }
  if (version === 'planned') for (const entry of applied) if (['protection', 'ammo'].includes(entry.effective_field.split('.')[0])) byId.get(entry.vehicle_id).probability_cache_invalidated_by_scenario = true;
  return {vehicles, audit, applied, rejected, version, simulated: version === 'planned', source_status: scenario.status ?? 'not_imported'};
}

/** A display fold never changes pair/sample weighting. Only independently
 * verified equivalents may share a displayed group. */
export function foldEquivalents(vehicles) {
  const groups = new Map();
  for (const vehicle of list(vehicles)) {
    const key = vehicle.equivalence_group && vehicle.equivalence_verified === true ? vehicle.equivalence_group : vehicle.id;
    if (!groups.has(key)) groups.set(key, {id: key, verified: key !== vehicle.id, members: []});
    groups.get(key).members.push(vehicle);
  }
  return [...groups.values()];
}
function opponentWeights(vehicles) {
  const groups = foldEquivalents(vehicles), result = new Map();
  if (!groups.length) return result;
  for (const group of groups) for (const vehicle of group.members) result.set(vehicle.id, 1 / groups.length / group.members.length);
  return result;
}

export function matchEnvironment(vehicles, lineup, version, config, context = {}) {
  const all = list(vehicles), byId = new Map(all.map(v => [v.id, v]));
  const members = lineup.map(value => typeof value === 'string' ? byId.get(value) : value).filter(Boolean);
  const ratingOf = v => getBR(v, version, context);
  const known = members.map(ratingOf).filter(finite);
  const actual_br = known.length ? Math.max(...known) : null;
  const nations = unique(members.map(v => v.nation));
  const invalid = [];
  if (!members.length) invalid.push('empty_lineup');
  if (nations.length > 1) invalid.push('mixed_nations');
  if (members.some(v => !finite(ratingOf(v)))) invalid.push('unknown_ground_rb');
  if (members.some(v => hasRatingConfigurations(v) && !finite(ratingOf(v))))
    invalid.push('rating_configuration_selection_required_or_invalid');
  if (members.every(v => v.kind !== 'ground')) invalid.push('requires_ground_vehicle');
  const minimum = finite(actual_br) ? actual_br - config.matching.max_br_spread : null;
  const maximum = finite(actual_br) ? actual_br + config.matching.max_br_spread : null;
  const opponents = finite(actual_br) ? all.filter(v => !nations.includes(v.nation)
    && finite(ratingOf(v)) && ratingOf(v) >= minimum - config.matching.epsilon
    && ratingOf(v) <= maximum + config.matching.epsilon) : [];
  const carry = finite(actual_br) ? all.filter(v => !nations.includes(v.nation)
    && finite(ratingOf(v)) && ratingOf(v) < minimum - config.matching.epsilon) : [];
  // Do not silently treat an opponent with an unresolved alternate BR as absent.
  // Future opponent population weighting must be by *vehicle*, then by tier,
  // rather than multiplying the count of a multi-BR aircraft.
  const unresolved_opponent_configurations = finite(actual_br) ? all.filter(v =>
    !nations.includes(v.nation) && hasRatingConfigurations(v) && !finite(ratingOf(v))
    && (() => {
      const choices = enumerateRatingConfigurations(v, {mode: 'ground_rb', version});
      return !choices.length || choices.some(choice =>
        choice.br >= minimum - config.matching.epsilon && choice.br <= maximum + config.matching.epsilon);
    })()).map(v => v.id) : [];
  if (unresolved_opponent_configurations.length)
    invalid.push('opponent_rating_configuration_unresolved');
  const weights = opponentWeights(opponents);
  return {actual_br, version, nation: nations.length === 1 ? nations[0] : null,
    minimum, maximum, opponents, opponent_ids: opponents.map(v => v.id),
    opponent_weights: Object.fromEntries(weights),
    unresolved_opponent_configurations,
    selected_rating_configurations: Object.fromEntries(members.filter(v => hasRatingConfigurations(v))
      .map(v => [v.id, context.configuration_by_vehicle?.[v.id] ?? null])),
    lower_carry_count: carry.length, lower_carry_note: config.matching.lower_vehicle_carry_note,
    excluded_unknown_br: all.filter(v => !nations.includes(v.nation) && !finite(ratingOf(v))).map(v => v.id),
    valid: !invalid.length, invalid, prior: config.matching.opponent_prior,
    prior_is_empirical: config.matching.empirical_encounter_frequencies};
}

export function validateLoadout(vehicle, loadout, context = {}, config) {
  const errors = [], warnings = [], active_weapons = [];
  if (!loadout) return {valid: false, errors: ['missing_loadout'], warnings, active_weapons};
  if (loadout.legal !== true) errors.push('loadout_legality_unverified');
  const configured = validateRatingLoadout(vehicle, loadout, context);
  errors.push(...configured.errors);
  warnings.push(...configured.warnings);
  const found = (vehicle.loadouts ?? []).find(candidate => candidate.id === loadout.id);
  if (!found || stable(found.weapons) !== stable(loadout.weapons)) errors.push('loadout_not_available_on_vehicle');
  const stations = new Set(), weaponIds = new Set((loadout.weapons ?? []).map(w => w.id));
  let totalMass = 0, allMassKnown = true;
  for (const weapon of loadout.weapons ?? []) {
    if (present(weapon.station)) {
      if (stations.has(weapon.station)) errors.push(`station_conflict:${weapon.station}`);
      stations.add(weapon.station);
      if (vehicle.hardpoints && !vehicle.hardpoints.some(point => point.id === weapon.station)) errors.push(`unknown_station:${weapon.station}`);
    }
    if (!finite(weapon.count) || weapon.count <= 0) errors.push(`invalid_weapon_count:${weapon.id}`);
    if ((weapon.conflicts_with ?? []).some(id => weaponIds.has(id))) errors.push(`weapon_conflict:${weapon.id}`);
    if ((weapon.requires ?? []).some(id => !weaponIds.has(id))) errors.push(`missing_dependency:${weapon.id}`);
    if (finite(weapon.mass_kg) && finite(weapon.count)) totalMass += weapon.mass_kg * weapon.count;
    else allMassKnown = false;
    const isArm = config.air.anti_radiation_types.includes(weapon.type);
    if (isArm && context.target_radar_emitting !== true) {
      warnings.push(`anti_radiation_condition_not_met:${weapon.id}`);
      continue;
    }
    active_weapons.push(weapon);
  }
  const capacity = loadout.max_payload_kg ?? vehicle.max_payload_kg ?? vehicle.payload_limits?.total_mass_kg;
  if (finite(capacity) && allMassKnown && totalMass > capacity) errors.push('payload_overweight');
  if (!allMassKnown) warnings.push('payload_mass_incomplete');
  return {valid: !errors.length, errors: unique(errors), warnings: unique(warnings), active_weapons,
    preset_id: loadout.id, mixed_preset: false,
    rating_configuration_id: configured.rating.configuration_id,
    effective_br: configured.rating.br};
}

function penetrationAt(ammo, distance, angle, config) {
  if (ammo?.range_invariant === true) {
    const row = (ammo.penetration ?? []).find(row => !finite(row.distance_m) && finite(row.mm) && finite(row.angle_deg) && Math.abs(row.angle_deg - angle) <= config.matching.epsilon);
    if (row) return {mm: row.mm, method: 'source_angle_only_table_range_invariant', distance_m: distance, angle_deg: angle, source_distance_m: null};
  }
  const rows = (ammo?.penetration ?? []).filter(row => finite(row.mm) && finite(row.distance_m) && finite(row.angle_deg) && Math.abs(row.angle_deg - angle) <= config.matching.epsilon).sort((a, b) => a.distance_m - b.distance_m);
  const direct = rows.find(row => Math.abs(row.distance_m - distance) <= config.matching.epsilon);
  if (direct) return {mm: direct.mm, method: 'source_sample', distance_m: distance, angle_deg: angle};
  const lower = rows.filter(row => row.distance_m < distance).at(-1), upper = rows.find(row => row.distance_m > distance);
  if (lower && upper) return {mm: lower.mm + (upper.mm - lower.mm) * (distance - lower.distance_m) / (upper.distance_m - lower.distance_m), method: 'linear_interpolation_between_source_samples', distance_m: distance, angle_deg: angle};
  return {mm: null, method: 'missing_range_or_angle_sample', distance_m: distance, angle_deg: angle};
}
export function selectAmmunition(vehicle, context, config) {
  const requested = context.ammo_by_vehicle?.[vehicle.id] ?? context.ammo_id;
  const available = vehicle.ammo ?? [];
  if (requested) return available.find(ammo => ammo.id === requested) ?? null;
  return available.map(ammo => ({ammo, mm: penetrationAt(ammo, context.physical_distance_m, context.angle_deg, config).mm})).sort((a, b) => (b.mm ?? -Infinity) - (a.mm ?? -Infinity) || String(a.ammo.id).localeCompare(String(b.ammo.id)))[0]?.ammo ?? null;
}
function reloadOf(vehicle, ammo = null) {
  const fire = ammo?.weapon_profile ?? vehicle.firepower ?? {};
  if (fire.reload_type === 'autoloader') return finite(fire.autoload_s) ? fire.autoload_s : null;
  if (fire.reload_type === 'automatic') return finite(fire.autoload_s) ? fire.autoload_s : (finite(fire.fire_rate_rpm) && fire.fire_rate_rpm > 0 ? 60 / fire.fire_rate_rpm : null);
  if (fire.reload_type !== 'manual') return null;
  return finite(fire.reload_ace_s) ? fire.reload_ace_s : null;
}
function contrast(a, b, scale, slope) {
  return finite(a) && finite(b) ? logistic((a - b) / scale * slope) : null;
}
function booleanContrast(a, b) {
  return typeof a === 'boolean' && typeof b === 'boolean' ? (Number(a) - Number(b) + 1) / 2 : null;
}
function mobilityValue(a, b, config) {
  const values = {};
  for (const field of Object.keys(config.pair.mobility_weights)) values[field] = field === 'neutral_steering' ? booleanContrast(a.mobility?.[field], b.mobility?.[field]) : contrast(a.mobility?.[field], b.mobility?.[field], config.pair.relative_scales[field], config.pair.logistic_slope);
  return {...weighted(values, config.pair.mobility_weights), fields: values};
}
function observationValue(a, b, config) {
  const values = {};
  for (const field of Object.keys(config.pair.observation_weights)) {
    const aa = field.includes('thermal') ? a.sensors?.[field] : a.firepower?.[field];
    const bb = field.includes('thermal') ? b.sensors?.[field] : b.firepower?.[field];
    values[field] = contrast(aa, bb, config.pair.relative_scales[field], config.pair.logistic_slope);
  }
  return {...weighted(values, config.pair.observation_weights), fields: values};
}
function concealmentValue(a, b, config) {
  const values = Object.fromEntries(Object.keys(config.pair.concealment_weights).map(field => [field, booleanContrast(a.auxiliary?.[field] ?? a.sensors?.[field], b.auxiliary?.[field] ?? b.sensors?.[field])]));
  return {...weighted(values, config.pair.concealment_weights), fields: values};
}
/** Cache identity includes every physical condition. Unknown fields never
 * match another unknown, and a reference shell does not establish ownership. */
export function findExactProbability(attacker, target, ammo, context, config, probabilities = {}) {
  if (!ammo || !(attacker.ammo ?? []).some(item => item.id === ammo.id)) return null;
  if (attacker.probability_cache_invalidated_by_scenario || target.probability_cache_invalidated_by_scenario) return null;
  const records = probabilities.records ?? [];
  let prepared = probabilityIndexes.get(probabilities);
  if (!prepared || prepared.records !== records || prepared.length !== records.length) {
    const byTarget = new Map();
    for (const record of records) if (record.reference_only === false) {
      if (!byTarget.has(record.target_id)) byTarget.set(record.target_id, new Map());
      const byAmmo = byTarget.get(record.target_id);
      if (!byAmmo.has(record.ammo_id)) byAmmo.set(record.ammo_id, []);
      byAmmo.get(record.ammo_id).push(record);
    }
    prepared = {records, length: records.length, byTarget};
    probabilityIndexes.set(probabilities, prepared);
  }
  const candidates = prepared.byTarget.get(target.id)?.get(ammo.id) ?? [];
  if (!candidates.length) return null;
  const request = {
    target_id: target.id,
    configuration_id: target.configuration_id,
    ammo_id: ammo.id,
    ammo_profile_sha256: ammo.ammo_profile_sha256 ?? ammo.profile_sha256,
    physical_distance_m: context.physical_distance_m,
    angle_deg: context.angle_deg,
    geometry_version: target.geometry_version,
    configuration_version: target.configuration_version,
    model_version: context.armor_model_version ?? probabilities.model_version,
    source_version: target.source_version ?? target.game_version,
    aim_context_key: aimContextKey(context, config),
  };
  return candidates.find(record => record.reference_only === false && config.cache.exact_required_fields.every(field => present(request[field]) && present(record[field]) && (finite(request[field]) && finite(record[field]) ? Math.abs(request[field] - record[field]) <= config.matching.epsilon : request[field] === record[field]))) ?? null;
}

const layerIndexes = new WeakMap();
const sampledAmmoCache = new WeakMap();
const kineticTypes = new Set(['AP','APC','APBC','APCBC','APHE','APHEBC','APCR','APDS','APFSDS','SAP','SAPBC','AC_SHELL']);
const chemicalTypes = new Set(['HEAT','HEATFS','HEAT_FS','HEAT_GRENADE','ATGM','CE']);
export function ammunitionMechanism(ammo) {
  const type=String(ammo?.type||'').toUpperCase();
  if(kineticTypes.has(type))return 'kinetic';
  if(chemicalTypes.has(type)||/anti.tank guided missile/i.test(ammo?.description||''))return 'chemical';
  return null;
}
/** Evaluate this projectile's own distance/obliquity table against sampled
 * target layers. Reference-shell heatmaps are never used for this path. */
export function sampledAmmunitionResponse(attacker,target,ammo,suppliedContext,config,probabilities={}) {
  const context=contextOf(suppliedContext,config),bundle=probabilities.target_layers;
  if(!bundle||!ammo||!(attacker.ammo||[]).some(a=>a.id===ammo.id)||target.probability_cache_invalidated_by_scenario)return null;
  const mechanism=ammunitionMechanism(ammo);if(!mechanism)return null;
  let index=layerIndexes.get(bundle);
  if(!index){index=new Map((bundle.targets||[]).map(t=>[t.target_id,t]));layerIndexes.set(bundle,index);}
  const geometry=index.get(target.id),identity=target.client_collision??target.source_attributes?.client;
  if(!geometry||!identity||identity.geometry_sha256!==geometry.geometry_sha256||identity.configuration_sha256!==geometry.configuration_sha256||identity.source_version!==geometry.source_version)return null;
  const angle=((context.angle_deg%360)+360)%360;
  const view=geometry.views.find(v=>Math.abs(v.angle_deg-angle)<=config.matching.epsilon);
  if(!view?.rays.length)return null;
  const normal=penetrationAt(ammo,context.physical_distance_m,0,config);
  if(!finite(normal.mm)||normal.mm<=0)return null;
  const angles=unique((ammo.penetration||[]).map(p=>p.angle_deg).filter(a=>finite(a)&&a>=0&&a<90)).sort((a,b)=>a-b);
  const curve=angles.map(a=>[a,penetrationAt(ammo,context.physical_distance_m,a,config).mm]).filter(([,mm])=>finite(mm)&&mm>0);
  if(!curve.length)return null;
  const settings=config.pair.sampled_layers;
  const key=JSON.stringify([mechanism,normal.mm,curve,angle,settings]);
  let cache=sampledAmmoCache.get(view);if(!cache){cache=new Map();sampledAmmoCache.set(view,cache);}
  if(cache.has(key))return {...cache.get(key),ammo_id:ammo.id};
  const slopeRatio=cosine=>{
    const angle=Math.acos(clamp(cosine))*180/Math.PI;
    const upper=curve.find(([a])=>a>=angle),lower=curve.filter(([a])=>a<=angle).at(-1);
    if(upper&&lower){const mm=upper[0]===lower[0]?lower[1]:lower[1]+(upper[1]-lower[1])*(angle-lower[0])/(upper[0]-lower[0]);return Math.max(settings.minimum_slope_ratio,mm/normal.mm);}
    const tail=lower??curve[0];
    return Math.max(settings.minimum_slope_ratio,(tail[1]/normal.mm)*cosine/Math.max(settings.minimum_slope_ratio,Math.cos(tail[0]*Math.PI/180)));
  };
  let known=0,total=0,required=0;
  const offset=mechanism==='chemical'?4:0,spread=normal.mm*settings.penetration_transition_fraction;
  for(const ray of view.rays){
    if(ray===null)continue;
    let cost=0;
    for(const layer of ray){
      const [thickness,los,cap,through]=layer.slice(offset,offset+4);
      const response=layer[9]?los:Math.min(thickness/slopeRatio(layer[8]),cap);
      cost+=layer[9]===1?response:Math.max(response,through);
    }
    const pass=spread>0?clamp((normal.mm+spread-cost)/(2*spread)):Number(normal.mm>=cost);
    known++;total+=pass;required+=cost;
  }
  if(!known)return null;
  const value=total/known,uncertainty=settings.model_uncertainty+0.5/Math.sqrt(known);
  const result={value,lower:clamp(value-uncertainty),upper:clamp(value+uncertainty),coverage:known/view.rays.length,
    source:'owned_ammunition_against_sampled_grp_layers',sample_count:view.rays.length,known_samples:known,
    penetration_mm:normal.mm,mean_path_resistance_mm:required/known,mechanism,
    target_id:target.id,geometry_sha256:geometry.geometry_sha256,configuration_sha256:geometry.configuration_sha256,
    physical_distance_m:context.physical_distance_m,angle_deg:angle,model:bundle.model,
    scope:'Sampled protected-target projection, additive layers and projectile source angle table; no ricochet, lethality or dynamic reactive-armor claim.'};
  if(cache.size>=settings.max_cached_profiles_per_view)cache.delete(cache.keys().next().value);
  cache.set(key,result);return {...result,ammo_id:ammo.id};
}
function selectAgainstTarget(attacker,target,context,config,probabilities){
  const requested=context.ammo_by_vehicle?.[attacker.id]??context.ammo_id;
  if(requested)return selectAmmunition(attacker,context,config);
  const options=(attacker.ammo||[]).map(ammo=>{
    const exact=findExactProbability(attacker,target,ammo,context,config,probabilities);
    const sampled=exact?null:sampledAmmunitionResponse(attacker,target,ammo,context,config,probabilities);
    const response=exact?.mean??sampled?.value;
    const reload=reloadOf(attacker,ammo);
    return {ammo,response,merit:finite(response)?response*config.pair.ammo_selection_weights.penetration+(finite(reload)?config.pair.relative_scales.reload_s/(config.pair.relative_scales.reload_s+reload):0)*config.pair.ammo_selection_weights.tempo:null};
  }).filter(row=>finite(row.merit)).sort((a,b)=>b.merit-a.merit||a.ammo.id.localeCompare(b.ammo.id));
  return options[0]?.ammo??selectAmmunition(attacker,{...context,angle_deg:0},config);
}

function groundPair(attacker, target, context, config, probabilities) {
  const ammo = selectAgainstTarget(attacker, target, context, config, probabilities), missing = [], notes = [], components = {};
  const exact = findExactProbability(attacker, target, ammo, context, config, probabilities);
  const sampled = exact ? null : sampledAmmunitionResponse(attacker,target,ammo,context,config,probabilities);
  let quality = 'coarse_attributes', armor = null, armorLower = null, armorUpper = null, armorCoverage = 0;
  const penetration = penetrationAt(ammo, context.physical_distance_m, 0, config);
  if (!ammo) missing.push('attacker.ammunition');
  if (exact && finite(exact.mean ?? exact.probability)) {
    armor = exact.mean ?? exact.probability;
    armorLower = exact.lower ?? armor; armorUpper = exact.upper ?? armor;
    armorCoverage = exact.coverage ?? exact.known_mass ?? 0;
    quality = 'conditional_layered_model';
    notes.push('条件分层直线路径模型；不是游戏穿透率或击毁率。');
  } else if(sampled){
    armor=sampled.value;armorLower=sampled.lower;armorUpper=sampled.upper;armorCoverage=sampled.coverage;
    quality='sampled_layered_ammunition';
    notes.push('使用本车弹药的距离/倾角穿深表，计算敌车 GRP 抽样路径上的逐层响应；不是击毁率。');
  } else {
    const chemical = ['HEAT', 'HEATFS', 'HEAT_FS', 'ATGM', 'CE', 'heat', 'heatfs', 'atgm'].includes(ammo?.type);
    const effective = chemical ? target.protection?.front_rhae_ce_mm : target.protection?.front_rhae_ke_mm;
    const homogeneous = target.protection?.homogeneous_verified === true ? target.protection.front_mm : null;
    const resistance = finite(effective) ? effective : homogeneous;
    if (finite(penetration.mm) && finite(resistance) && resistance > 0) {
      armor = logistic(Math.log(penetration.mm / resistance) * config.pair.logistic_slope);
      armorLower = clamp(armor - config.pair.coarse_proxy_uncertainty);
      armorUpper = clamp(armor + config.pair.coarse_proxy_uncertainty);
      armorCoverage = 1;
      notes.push('穿深与已核验防护输入的粗效用指标；忽略局部几何和弹后效，不是物理穿透概率。');
    } else {
      if (!finite(penetration.mm)) missing.push('ammunition.penetration_at_physical_range_and_angle');
      if (!finite(resistance)) missing.push('target.effective_protection_or_verified_homogeneous_armor');
    }
    if ((probabilities.entries ?? []).some(entry => entry.target_id === target.id)) notes.push('存在参考弹研究图，但精确弹药/构型/版本/射距缓存键未匹配，不用于本配对。');
  }
  components.armor_pressure = {value: armor, lower: armorLower, upper: armorUpper, coverage: armorCoverage, source: exact ? 'exact_cache' : sampled ? sampled.source : 'coarse_source_inputs', ...(sampled?{sampled}:{}), already_marginalized:!!exact};
  const targetAmmo = selectAmmunition(target, {...context, ammo_id: undefined}, config);
  const reload = reloadOf(attacker, ammo), targetReload = reloadOf(target, targetAmmo);
  const firstAim = context.first_aim_s ?? config.pair.first_aim_s;
  const cycle = finite(reload) ? Math.max(reload, context.reaim_s ?? config.pair.reaim_s) : null;
  let opportunities = finite(cycle) ? Math.max(0, 1 + Math.floor((config.pair.sustain_window_s - firstAim) / cycle)) : null;
  const readyLimit = ammo?.weapon_profile?.belt_capacity ?? ammo?.weapon_profile?.ready_rounds;
  if (finite(opportunities) && finite(readyLimit)) opportunities = Math.min(opportunities, readyLimit);
  const tempo = contrast(targetReload, reload, config.pair.relative_scales.reload_s, config.pair.logistic_slope);
  components.tempo = {value: tempo, coverage: finite(tempo) ? 1 : 0, reload_s: reload, reload_basis: ammo?.weapon_profile?.reload_type ?? attacker.firepower?.reload_type ?? 'max_level_ace_required', weapon_id: ammo?.weapon_id ?? attacker.firepower?.primary_weapon_id ?? null, firing_opportunities: opportunities, cycle_s: cycle, ready_limit: readyLimit ?? null};
  if (!finite(tempo)) missing.push('ace_or_autoloader_reload_for_both_sides');
  components.mobility = mobilityValue(attacker, target, config);
  components.observation = observationValue(attacker, target, config);
  components.concealment = concealmentValue(attacker, target, config);
  for (const name of ['mobility', 'observation', 'concealment']) if (components[name].coverage < 1) missing.push(`${name}.partial_fields`);
  notes.push('装填与重新瞄准允许重叠；射击机会数不等于击毁数，未模拟毁伤状态与后续弹药分散。');
  return {components, ammo_id: ammo?.id ?? null, ammo_name: ammo?.name ?? null, penetration, quality, missing, notes, loadout_id: null};
}

function aaPotential(vehicle, config) {
  const aa = vehicle.air_defence ?? {}, radar = vehicle.sensors?.radar ?? {};
  const scan = aa.scan_period_s ?? aa.radar_scan_period_s ?? radar.scan_period_s;
  const values = {
    range: normalized(aa.missile_range_m, config.air.range_scale_m),
    overload: normalized(aa.missile_overload_g, config.air.overload_scale_g),
    ready_ammo: normalized(aa.ready_missiles, config.air.ready_ammo_scale),
    scan: finite(scan) ? config.air.scan_period_scale_s / (scan + config.air.scan_period_scale_s) : null,
    gun_rate: normalized(aa.gun_rate_rpm, config.air.gun_rate_scale_rpm),
  };
  return {...weighted(values, config.air.aa_weights), weapon_capability_known: finite(aa.missile_range_m) || finite(aa.gun_rate_rpm), fields: values, conditions: ['最大射程不是可靠命中距离', '需满足扫描模式、视线与跟踪条件'], missing: Object.entries(values).filter(([, value]) => !finite(value)).map(([key]) => `air_defence.${key}`)};
}
function bestLoadout(vehicle, context, config) {
  const requested = context.loadout_by_vehicle?.[vehicle.id];
  const candidates = (vehicle.loadouts ?? []).filter(item => !requested || item.id === requested).map(item => {
    const validated = validateLoadout(vehicle, item, context, config);
    if (!validated.valid) return {item, validated, score: null, coverage: 0, weapons: []};
    const weapons = validated.active_weapons.filter(w => config.air.ground_weapon_types.includes(w.type)).map(weapon => {
      const fields = {
        range: normalized(weapon.range_m, config.air.range_scale_m),
        explosive_mass: normalized(weapon.explosive_tnt_kg, config.air.payload_scale_kg),
        accuracy: finite(weapon.validated_hit_probability) ? weapon.validated_hit_probability : null,
        count: normalized(weapon.count, config.air.weapon_count_scale),
      };
      const result = weighted(fields, config.air.weapon_weights);
      return {weapon_id: weapon.id, ...result, fields, missing: Object.entries(fields).filter(([, value]) => !finite(value)).map(([field]) => `weapon.${weapon.id}.${field}`)};
    });
    return {item, validated, score: weapons.length ? mean(weapons.map(w => w.value).filter(finite)) : 0, coverage: weapons.length ? mean(weapons.map(w => w.coverage)) : 1, weapons};
  });
  return candidates.filter(c => c.validated.valid).sort((a, b) => (b.score ?? -Infinity) - (a.score ?? -Infinity) || String(a.item.id).localeCompare(String(b.item.id)))[0] ?? null;
}
function airPair(attacker, target, context, config) {
  const attackerAir = attacker.kind !== 'ground', targetAir = target.kind !== 'ground';
  const components = {}, missing = [], notes = [], quality = 'coarse_air_mission', result = {ammo_id: null, ammo_name: null, loadout_id: null, components, missing, notes, quality};
  if (!attackerAir && targetAir) {
    const aa = aaPotential(attacker, config);
    components.weapon_delivery = aa;
    components.target_effectiveness = {value: null, coverage: 0};
    components.survival_window = {value: null, coverage: 0};
    components.availability = {value: 1, coverage: 1};
    result.mission_capability_known = aa.weapon_capability_known;
    missing.push(...aa.missing, 'air_target_flight_and_seeker_conditions');
    notes.push(...aa.conditions);
    result.quality = 'coarse_air_defence';
    return result;
  }
  if (attackerAir && !targetAir) {
    const loadout = bestLoadout(attacker, {...context, target_radar_emitting: context.target_radar_emitting ?? target.sensors?.radar?.emitting}, config);
    if (loadout) {
      result.mission_capability_known = true;
      result.loadout_id = loadout.item.id;
      result.weapons = loadout.weapons;
      components.weapon_delivery = {value: loadout.score, coverage: loadout.coverage};
      missing.push(...loadout.validated.warnings);
      for (const weapon of loadout.weapons) missing.push(...weapon.missing);
    } else {
      result.mission_capability_known = false;
      components.weapon_delivery = {value: null, coverage: 0};
      missing.push('verified_legal_ground_attack_loadout');
    }
    const sp = loadout?.item.spawn_cost;
    let availability = config.air.unknown_sp_availability, availabilityCoverage = 0;
    if (finite(sp) && finite(context.available_spawn_points)) {availability = context.available_spawn_points >= sp ? 1 : 0; availabilityCoverage = 1;}
    else missing.push('spawn_cost_or_earned_spawn_points');
    components.availability = {value: availability, coverage: availabilityCoverage, prior: !availabilityCoverage};
    const aa = aaPotential(target, config);
    const motion = finite(target.mobility?.forward_kmh) ? target.mobility.forward_kmh : null;
    const resistance = target.protection?.front_rhae_ce_mm;
    const penetrationValues = loadout?.validated.active_weapons.map(w => w.penetration_mm).filter(finite) ?? [];
    const physicalPressure = finite(resistance) && resistance > 0 && penetrationValues.length ? mean(penetrationValues.map(p => logistic(Math.log(p / resistance) * config.pair.logistic_slope))) : null;
    const evasion = finite(motion) ? 1 - normalized(motion, config.air.target_mobility_scale_kmh) * config.air.target_evasion_prior_fraction : null;
    const effectFields = {armor_response: physicalPressure, evasion_window: evasion};
    components.target_effectiveness = {...weighted(effectFields, config.air.target_effect_weights), fields: effectFields, note: '机动回避只是配置化行为先验；缺少目标装甲响应时保留未知区间。'};
    if (!finite(physicalPressure)) missing.push('air_weapon_specific_target_armor_response');
    if (!finite(evasion)) missing.push('ground_target_evasion_mobility');
    const exposure = finite(aa.value) ? 1 - aa.value : null;
    components.survival_window = {value: exposure, coverage: aa.coverage};
    result.target_motion_kmh = motion;
    result.enemy_aa = aa;
    missing.push(...aa.missing, 'attack_altitude_line_of_sight_and_guidance_envelope');
    notes.push('挂载按一个已验证合法预设计数；未合并互斥预设。', '出击可用性按重生点条件或显式先验计算。', '该模型尚未进行实战命中与压制校准。');
    return result;
  }
  components.weapon_delivery = {value: null, coverage: 0};
  components.target_effectiveness = {value: null, coverage: 0};
  result.mission_capability_known = false;
  components.survival_window = {value: null, coverage: 0};
  components.availability = {value: config.air.unknown_sp_availability, coverage: 0, prior: true};
  missing.push('air_to_air_weapon_and_flight_model');
  return result;
}

export function evaluatePair(attacker, target, suppliedContext = {}, config, probabilities = {}) {
  const context = contextOf(suppliedContext, config);
  const raw = attacker.kind === 'ground' && target.kind === 'ground' ? groundPair(attacker, target, context, config, probabilities) : airPair(attacker, target, context, config);
  const air = attacker.kind !== 'ground' || target.kind !== 'ground';
  const weights = {...(air ? config.air.weights : config.pair.weights)};
  if (!air) {
    weights.mobility *= Math.exp(context.skill * config.pair.mobility_skill_sensitivity);
    weights.tempo *= Math.exp(context.skill * config.pair.tempo_skill_sensitivity);
    weights.armor_pressure *= Math.exp(context.skill * config.pair.armor_skill_sensitivity);
  }
  let score = 0, low = 0, high = 0, evidence = 0, sum = 0;
  for (const [name, weight] of Object.entries(weights)) {
    const component = raw.components[name] ?? {}, known = finite(component.value), coverage = known ? clamp(component.coverage ?? 1) : 0;
    const value = known ? component.already_marginalized ? component.value : component.value * coverage + config.pair.unknown_prior * (1 - coverage) : config.pair.unknown_prior;
    sum += weight;
    score += weight * value;
    evidence += weight * coverage;
    low += weight * (known ? component.already_marginalized ? (component.lower ?? value) : (component.lower ?? component.value) * coverage + config.pair.unknown_interval[0] * (1 - coverage) : config.pair.unknown_interval[0]);
    high += weight * (known ? component.already_marginalized ? (component.upper ?? value) : (component.upper ?? component.value) * coverage + config.pair.unknown_interval[1] * (1 - coverage) : config.pair.unknown_interval[1]);
  }
  const coverage = sum && raw.mission_capability_known !== false ? evidence / sum : 0;
  const resultScore = coverage >= config.pair.minimum_evidence_fraction && raw.mission_capability_known !== false ? score / sum : null;
  const aggregation_weight = roleOf(attacker, config) === 'aa' && roleOf(target, config) === 'aa' ? config.pair.aa_aa_aggregation_weight : config.pair.default_aggregation_weight;
  return {...raw, attacker_id: attacker.id, target_id: target.id, score: resultScore, lower: sum ? low / sum : 0, upper: sum ? high / sum : 1, coverage, aggregation_weight, weights, missing: unique(raw.missing), conditions: {version: context.version, attacker_configuration: attacker.configuration_id ?? null, target_configuration: target.configuration_id ?? null,
    attacker_rating_configuration_id: context.configuration_by_vehicle?.[attacker.id] ?? null,
    target_rating_configuration_id: context.configuration_by_vehicle?.[target.id] ?? null,
    ammo_id: raw.ammo_id, loadout_id: raw.loadout_id, physical_distance_m: context.physical_distance_m, aiming_distance_m: context.distance_m, angle_deg: context.angle_deg, skill: context.skill, performance_state: context.performance_state, crew_state: context.crew_state}, model_version: config.model_version, calibration_status: config.calibration_status, score_semantics: 'decision_utility_not_win_probability'};
}

function summarizeResponses(pairs, config) {
  if (!pairs.length) return {score: null, lower: 0, upper: 1, coverage: 0, best_id: null};
  const usable = pairs.filter(pair => finite(pair.score));
  if (!usable.length) return {score: null, lower: 0, upper: 1, coverage: 0, best_id: null};
  const weightedPairs = pairs.map(pair => ({...pair, value: config.pair.unknown_prior + ((pair.score ?? config.pair.unknown_prior) - config.pair.unknown_prior) * pair.aggregation_weight, lower: config.pair.unknown_prior + (pair.lower - config.pair.unknown_prior) * pair.aggregation_weight, upper: config.pair.unknown_prior + (pair.upper - config.pair.unknown_prior) * pair.aggregation_weight}));
  const mass = weightedPairs.length;
  const depth = mean(weightedPairs.map(pair => pair.value));
  const best = weightedPairs.filter(pair => finite(pair.score)).sort((a, b) => b.value - a.value || a.attacker_id.localeCompare(b.attacker_id))[0];
  const response = weighted({best: best.value, depth}, config.lineup.response_weights).value;
  return {score: response, lower: weighted({best: best.lower, depth: mean(weightedPairs.map(pair => pair.lower))}, config.lineup.response_weights).value, upper: weighted({best: best.upper, depth: mean(weightedPairs.map(pair => pair.upper))}, config.lineup.response_weights).value, coverage: mass ? weightedPairs.reduce((sum, pair) => sum + pair.coverage * pair.aggregation_weight, 0) / mass : 0, best_id: best.attacker_id};
}
// Combine directional pressure in odds space instead of averaging a response
// with its inverse, which halves every difference around the neutral point.
// This is an uncalibrated decision contrast, not a probability of winning.
export function duelBalance(outbound,incoming,config) {
  const epsilon=config.lineup.duel_epsilon,weights=config.lineup.duel_weights;
  const logit=value=>{const p=Math.min(1-epsilon,Math.max(epsilon,finite(value)?value:config.pair.unknown_prior));return Math.log(p/(1-p));};
  return logistic((weights.outbound*logit(outbound)-weights.resilience*logit(incoming))/Math.max(weights.outbound,weights.resilience));
}
function quantile(values, fraction) {
  if (!values.length) return null;
  const ordered = values.slice().sort((a, b) => a - b), index = (ordered.length - 1) * fraction;
  const lower = Math.floor(index), upper = Math.ceil(index);
  return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower);
}
function roleCoverage(lineup, config) {
  const available = new Set(lineup.map(v => roleOf(v, config)));
  return weighted(Object.fromEntries(Object.keys(config.lineup.required_role_weights).map(role => [role, Number(available.has(role))])), config.lineup.required_role_weights).value;
}
function usePairEvaluator(context, config, probabilities) {
  if (context._pairEvaluator) return context._pairEvaluator;
  return (a, b) => evaluatePair(a, b, context, config, probabilities);
}
function airEnvironment(lineup, environment, context, config) {
  const ourAir = lineup.filter(v => v.kind !== 'ground'), ourAA = lineup.filter(v => roleOf(v, config) === 'aa');
  const enemyAA = environment.opponents.filter(v => roleOf(v, config) === 'aa');
  const knownEnemy = enemyAA.map(v => aaPotential(v, config));
  const enemyPotential = mean(knownEnemy.map(v => v.value).filter(finite));
  const ourPotential = mean(ourAA.map(v => aaPotential(v, config).value).filter(finite));
  const payloads = ourAir.map(v => bestLoadout(v, context, config)).filter(Boolean);
  const delivery = mean(payloads.map(v => v.score).filter(finite));
  const availability = mean(payloads.map(v => finite(v.item.spawn_cost) && finite(context.available_spawn_points) ? Number(context.available_spawn_points >= v.item.spawn_cost) : config.air.unknown_sp_availability));
  const window = ourAir.length ? (finite(delivery) && (finite(enemyPotential) || !enemyAA.length) ? delivery * (1 - (enemyPotential ?? 0)) * (availability ?? config.air.unknown_sp_availability) : null) : 0;
  return {air_window: window, aa_coverage: ourAA.length ? ourPotential : 0, enemy_aa_potential: enemyPotential, enemy_aa_ids: enemyAA.map(v => v.id), aircraft_ids: ourAir.map(v => v.id), aa_ids: ourAA.map(v => v.id), known_enemy_aa_fraction: enemyAA.length ? knownEnemy.filter(v => finite(v.value)).length / enemyAA.length : 1, availability, conditions: ['合法挂载', '重生点可用性', '视线与武器包线', '敌方雷达辐射状态'], calibrated: false};
}

export function evaluateLineup(vehicles, lineupIds, suppliedContext = {}, config, probabilities = {}) {
  const context = contextOf(suppliedContext, config), all = list(vehicles), byId = new Map(all.map(v => [v.id, v]));
  const ids = unique(lineupIds), lineup = ids.map(id => byId.get(id)).filter(Boolean), environment = matchEnvironment(all, lineup, context.version, config, context);
  const invalid = [...environment.invalid];
  if (ids.length !== lineupIds.length) invalid.push('duplicate_vehicle_slots');
  if (lineupIds.length > context.slots) invalid.push('slot_limit_exceeded');
  if (ids.some(id => !byId.has(id))) invalid.push('unknown_vehicle_ids');
  if (context.includeSpecial === false && lineup.some(v => v.special === true)) invalid.push('special_vehicles_excluded');
  if (context.availableIds && ids.some(id => !context.availableIds.includes(id))) invalid.push('unowned_vehicle');
  const systems = lineup.map(v => v.system_id).filter(Boolean);
  if (new Set(systems).size !== systems.length) invalid.push('multi_vehicle_system_counted_more_than_once');
  const evaluate = usePairEvaluator(context, config, probabilities), matrix = [], reverse = [], opponentResults = [];
  const missing = new Set(), usedAmmo=new Map();
  for (const enemy of environment.opponents) {
    const outbound = lineup.map(v => evaluate(v, enemy));
    const incoming = lineup.map(v => evaluate(enemy, v));
    for(const pair of outbound)if(pair.ammo_id){if(!usedAmmo.has(pair.attacker_id))usedAmmo.set(pair.attacker_id,new Map());const counts=usedAmmo.get(pair.attacker_id);counts.set(pair.ammo_id,(counts.get(pair.ammo_id)||0)+1);}
    if (context.includeMatrix !== false) {matrix.push(...outbound); reverse.push(...incoming);}
    for (const pair of [...outbound, ...incoming]) for (const field of pair.missing) missing.add(field);
    const ours = summarizeResponses(outbound, config), theirs = summarizeResponses(incoming, config);
    const balance = duelBalance(ours.score,theirs.score,config);
    const balanceLow = duelBalance(ours.lower,theirs.upper,config);
    const balanceHigh = duelBalance(ours.upper,theirs.lower,config);
    const known = finite(ours.score) || finite(theirs.score);
    opponentResults.push({vehicle_id: enemy.id, br: getBR(enemy, context.version, context), role: roleOf(enemy, config), score: theirs.score, our_best: ours.score, enemy_best: theirs.score, our_best_vehicle_id: ours.best_id, balance: known ? balance : null, lower: balanceLow, upper: balanceHigh, coverage: (ours.coverage + theirs.coverage) / 2, category: !finite(theirs.score) ? 'unknown' : theirs.score >= config.lineup.threat_thresholds.high ? 'high' : theirs.score >= config.lineup.threat_thresholds.medium ? 'medium' : 'low', prior_weight: environment.opponent_weights[enemy.id]});
  }
  const tier = {down: [], same: [], up: []};
  for (const row of opponentResults) {
    const name = row.br < environment.actual_br - config.matching.epsilon ? 'down' : row.br > environment.actual_br + config.matching.epsilon ? 'up' : 'same';
    tier[name].push(row);
  }
  function tierMean(rows, key) {
    if (!rows.length) return null;
    const weightSum = rows.reduce((sum, row) => sum + row.prior_weight, 0);
    return weightSum ? rows.reduce((sum, row) => sum + (finite(row[key]) ? row[key] : config.pair.unknown_prior) * row.prior_weight, 0) / weightSum : null;
  }
  const tierValues = Object.fromEntries(Object.entries(tier).map(([key, values]) => [key, tierMean(values, 'balance')]));
  const expected = weighted(tierValues, config.lineup.scenario_prior).value;
  const expectedLower = weighted(Object.fromEntries(Object.entries(tier).map(([key, values]) => [key, tierMean(values, 'lower')])), config.lineup.scenario_prior).value;
  const expectedUpper = weighted(Object.fromEntries(Object.entries(tier).map(([key, values]) => [key, tierMean(values, 'upper')])), config.lineup.scenario_prior).value;
  const air = airEnvironment(lineup, environment, context, config);
  const metrics = {mean: expected, down_tier: tierValues.down, same_tier: tierValues.same, up_tier: tierValues.up, worst_case: quantile(opponentResults.map(row => row.balance).filter(finite), config.lineup.worst_quantile), role_coverage: roleCoverage(lineup, config), air_window: air.air_window, aa_coverage: air.aa_coverage, known_pair_fraction: opponentResults.length ? mean(opponentResults.map(row => row.coverage)) : 0};
  const total = weighted(metrics, config.lineup.weights, config.pair.unknown_prior);
  const lowerMetrics = {...metrics, mean: expectedLower, worst_case: quantile(opponentResults.map(row=>row.lower).filter(finite),config.lineup.worst_quantile), air_window: finite(metrics.air_window) ? metrics.air_window : 0, aa_coverage: finite(metrics.aa_coverage) ? metrics.aa_coverage : 0};
  const upperMetrics = {...metrics, mean: expectedUpper, worst_case: quantile(opponentResults.map(row=>row.upper).filter(finite),config.lineup.worst_quantile), air_window: finite(metrics.air_window) ? metrics.air_window : 1, aa_coverage: finite(metrics.aa_coverage) ? metrics.aa_coverage : 1};
  const threats = opponentResults.slice().sort((a, b) => (b.enemy_best ?? -Infinity) - (a.enemy_best ?? -Infinity) || a.vehicle_id.localeCompare(b.vehicle_id));
  const advantages = opponentResults.filter(row => finite(row.our_best)).sort((a, b) => b.our_best - a.our_best || a.vehicle_id.localeCompare(b.vehicle_id));
  const roleList = Object.fromEntries(Object.keys(config.roles).map(role => [role, lineup.filter(v => roleOf(v, config) === role).map(v => v.id)]));
  const ammoChoices = Object.fromEntries(lineup.filter(v => v.kind === 'ground').map(v => [v.id, [...(usedAmmo.get(v.id)||[])].sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0]))[0]?.[0]??selectAmmunition(v,context,config)?.id??null]));
  const loadoutChoices = Object.fromEntries(lineup.filter(v => v.kind !== 'ground').map(v => [v.id, bestLoadout(v, context, config)?.item.id ?? null]));
  const explanations = ['只使用每辆载具自身可用的弹药；自动模式按敌车分层防护选弹，指定弹药时严格按指定项计算。', '对手频率与升降房占比使用明确先验，不代表真实排队分布。', '效用分数越高越有利；不表示胜率、击毁率或保证收益。'];
  if (metrics.known_pair_fraction < 1) explanations.push('配对资料不完整：缺项保留不确定区间，角色完整度仍可用于初步组队。');
  if (air.aircraft_ids.length) explanations.push('航空价值受到合法挂载、重生点可用性和敌方防空条件约束。');
  if (lineup.some(v => hasRatingConfigurations(v)))
    explanations.push('自选 BR 配置由玩家显式选择；高级改装解锁不自动改变 BR，受限挂载仍按实际配置核验。');
  if (lineup.some(v => roleOf(v, config) === 'aa')) explanations.push('防空对防空只在整队汇总降权；防空对航空不使用该降权。');
  return {lineup_ids: ids, nation: environment.nation, version: context.version, br: environment.actual_br, actual_br: environment.actual_br, valid: !invalid.length, invalid: unique(invalid), score: invalid.length ? null : total.value, lower: weighted(lowerMetrics, config.lineup.weights, config.pair.unknown_interval[0]).value, upper: weighted(upperMetrics, config.lineup.weights, config.pair.unknown_interval[1]).value, coverage: metrics.known_pair_fraction, metrics, environment, matrix, reverse_matrix: reverse, threats, advantages, roles: roleList, selected_rating_configurations: environment.selected_rating_configurations,
    ammo_choices: ammoChoices, ammo_matchups:Object.fromEntries([...usedAmmo].map(([id,counts])=>[id,Object.fromEntries(counts)])), loadout_choices: loadoutChoices, air, missing: [...missing].sort(), explanations, model_version: config.model_version, calibration_status: config.calibration_status, conditions: {skill: context.skill, distance_m: context.distance_m, physical_distance_m: context.physical_distance_m, angle_deg: context.angle_deg, slots: context.slots, includeSpecial: context.includeSpecial}, scenario_prior: config.lineup.scenario_prior, score_semantics: 'decision_utility_not_win_probability'};
}

function staticVehicleMerit(vehicle, cap, context, config) {
  const mobility = mean(Object.keys(config.pair.mobility_weights).map(field => finite(vehicle.mobility?.[field]) && finite(config.pair.relative_scales[field]) ? normalized(vehicle.mobility[field], config.pair.relative_scales[field]) : null).filter(finite));
  const ammo = selectAmmunition(vehicle, {...context,angle_deg:0}, config), penetration = penetrationAt(ammo, context.physical_distance_m, 0, config);
  const reload = reloadOf(vehicle, ammo), tempo = finite(reload) ? config.pair.relative_scales.reload_s / (config.pair.relative_scales.reload_s + reload) : null;
  const fire = normalized(penetration.mm, config.pair.relative_scales.penetration_mm);
  const air = vehicle.kind !== 'ground' ? bestLoadout(vehicle, context, config)?.score ?? null : null;
  const aa = roleOf(vehicle, config) === 'aa' ? aaPotential(vehicle, config).value : null;
  const observed = [mobility, tempo, fire, air, aa].filter(finite);
  const br_proximity = finite(getBR(vehicle, context.version, context)) ? clamp(1 - (cap - getBR(vehicle, context.version, context)) / config.search.lookback_br) : 0;
  return weighted({coverage: observed.length ? mean(observed) : null, role: 1, br_proximity}, config.search.selection_weights, config.pair.unknown_prior).value;
}
function slimEvaluation(result, config) {
  const {matrix, reverse_matrix, environment, ...rest} = result;
  return {...rest, threats: rest.threats.slice(0, config.lineup.explanation_limit), advantages: rest.advantages.slice(0, config.lineup.explanation_limit), environment: {actual_br: environment.actual_br, minimum: environment.minimum, maximum: environment.maximum, opponent_count: environment.opponents.length, lower_carry_count: environment.lower_carry_count, lower_carry_note: environment.lower_carry_note, excluded_unknown_br_count: environment.excluded_unknown_br.length, prior: environment.prior, prior_is_empirical: false}};
}

/** All legal BR caps are scanned, but combinations use deterministic pruning
 * and greedy marginal opponent coverage; no global-optimum claim is made. */
export function recommendNation(vehicles, nation, suppliedContext = {}, config, probabilities = {}) {
  const context = contextOf(suppliedContext, config), all = list(vehicles);
  const eligible = all.filter(v => v.nation === nation && finite(getBR(v, context.version, context)) && (context.includeSpecial || v.special !== true) && (!context.availableIds || context.availableIds.includes(v.id)));
  const caps = getBRs(eligible, nation, context.version, context), candidates = [], pairs = new Map();
  // Explicitly selected configurations only. Variant enumeration in the
  // recommendation search requires a later, source-backed opponent prior.
  const unselected_rating_configurations = all.filter(v => v.nation === nation
    && hasRatingConfigurations(v) && !finite(getBR(v, context.version, context))).map(v => v.id);
  const roleCandidateLimit=Math.max(config.search.candidates_per_role,context.slots);
  const evaluator = (a, b) => {
    const key = `${a.id}|${b.id}`;
    if (pairs.has(key)) return pairs.get(key);
    const value = evaluatePair(a, b, context, config, probabilities);
    if (pairs.size >= config.search.max_pair_cache_entries) pairs.delete(pairs.keys().next().value);
    pairs.set(key, value);
    return value;
  };
  const cachedContext = {...context, _pairEvaluator: evaluator, includeMatrix: false};
  let tested = 0, pruned = 0;
  for (const cap of caps) {
    const near = eligible.filter(v => getBR(v, context.version, context) <= cap + config.matching.epsilon && getBR(v, context.version, context) >= cap - config.search.lookback_br - config.matching.epsilon);
    let anchors = near.filter(v => v.kind === 'ground');
    if (!anchors.length) {
      anchors = eligible.filter(v => v.kind === 'ground' && getBR(v, context.version, context) <= cap + config.matching.epsilon);
      const fallback = anchors.slice().sort((a, b) => getBR(b, context.version, context) - getBR(a, context.version, context) || a.id.localeCompare(b.id)).slice(0, config.search.candidates_per_role);
      near.push(...fallback);
    }
    anchors.sort((a, b) => getBR(b, context.version, context) - getBR(a, context.version, context) || staticVehicleMerit(b, cap, context, config) - staticVehicleMerit(a, cap, context, config) || a.id.localeCompare(b.id));
    if (!anchors.length) continue;
    const byRole = Object.fromEntries(Object.keys(config.roles).map(role => {
      const pool = near.filter(v => roleOf(v, config) === role).sort((a, b) => staticVehicleMerit(b, cap, context, config) - staticVehicleMerit(a, cap, context, config) || a.id.localeCompare(b.id));
      pruned += Math.max(0, pool.length - roleCandidateLimit);
      return [role, pool.slice(0, roleCandidateLimit)];
    }));
    const variants = [];
    for (const [profile, order] of Object.entries(config.search.profile_orders).slice(0, config.search.variants_per_br)) {
      const selected = [anchors[0]], selectedIds = new Set([anchors[0].id]);
      const environment = matchEnvironment(all, selected, context.version, config, context);
      let index = 0;
      while (selected.length < context.slots) {
        const desired = order[index % order.length];
        index++;
        let pool = byRole[desired].filter(v => !selectedIds.has(v.id));
        if (!pool.length) pool = Object.values(byRole).flat().filter(v => !selectedIds.has(v.id));
        pool = pool.filter(v => !(v.kind !== 'ground' && selected.filter(s => s.kind !== 'ground').length >= Math.floor(context.slots * config.search.max_air_fraction)) && (!v.system_id || !selected.some(s => s.system_id === v.system_id)));
        if (!pool.length) break;
        const scoreCandidate = candidate => {
          let mass = 0, value = 0;
          for (const enemy of environment.opponents) {
            const p = evaluator(candidate, enemy), weight = environment.opponent_weights[enemy.id];
            const old = selected.map(v => evaluator(v, enemy)).map(v => v.score ?? config.pair.unknown_prior);
            const marginal = Math.max(...old, p.score ?? config.pair.unknown_prior) - Math.max(...old);
            value += marginal * weight * p.aggregation_weight; mass += weight;
          }
          const role = Number(!selected.some(v => roleOf(v, config) === roleOf(candidate, config)));
          const proximity = clamp(1 - (cap - getBR(candidate, context.version, context)) / config.search.lookback_br);
          return weighted({coverage: mass ? value / mass : null, role, br_proximity: proximity}, config.search.selection_weights, config.pair.unknown_prior).value;
        };
        const next = pool.map(v => ({v, score: scoreCandidate(v)})).sort((a, b) => b.score - a.score || staticVehicleMerit(b.v, cap, context, config) - staticVehicleMerit(a.v, cap, context, config) || a.v.id.localeCompare(b.v.id))[0].v;
        selected.push(next); selectedIds.add(next.id);
      }
      const evaluation = evaluateLineup(all, selected.map(v => v.id), cachedContext, config, probabilities);
      tested++;
      variants.push({...slimEvaluation(evaluation, config), candidate_cap: cap, profile});
    }
    variants.sort((a, b) => (b.score ?? -Infinity) - (a.score ?? -Infinity) || b.coverage - a.coverage || a.lineup_ids.join('|').localeCompare(b.lineup_ids.join('|')));
    candidates.push({...variants[0], alternatives_at_br: variants.slice(1).map(v => ({...v,alternatives_at_br:undefined}))});
  }
  const sorted = candidates.slice().sort((a, b) => (b.score ?? -Infinity) - (a.score ?? -Infinity) || b.coverage - a.coverage || a.actual_br - b.actual_br);
  return {nation, version: context.version, best: sorted[0] ?? null, alternatives: sorted.slice(1, config.search.alternatives + 1), candidates, search: {algorithm: config.search.algorithm, global_optimum_guaranteed: false, br_caps_scanned: caps, candidates_evaluated: tested, pruned_role_candidates: pruned, eligible_vehicle_count: eligible.length, all_effective_caps_scanned: unselected_rating_configurations.length === 0,
    unselected_rating_configurations, automatic_configuration_search: false, constraints: {slots: context.slots, includeSpecial: context.includeSpecial, availableIds: context.availableIds ?? null}}, model_version: config.model_version, calibration_status: config.calibration_status, conditions: {skill: context.skill, physical_distance_m: context.physical_distance_m, distance_m: context.distance_m, angle_deg: context.angle_deg}};
}

/** Rank every searched composition, including role variants; order does not
 * make the same set of members a different lineup. BR is not a ranking filter. */
export function topLineups(recommendations, limit=3) {
  const rows=(recommendations?.candidates||[]).flatMap(c=>[c,...(c.alternatives_at_br||[])]);
  const key=c=>[...c.lineup_ids].sort().join('|');
  rows.sort((a,b)=>(b.score??-Infinity)-(a.score??-Infinity)||(b.coverage??0)-(a.coverage??0)||(a.actual_br??Infinity)-(b.actual_br??Infinity)||key(a).localeCompare(key(b)));
  const seen=new Set(),result=[];
  for(const row of rows){
    if(row.valid===false||!finite(row.score)||!row.lineup_ids?.length)continue;
    const id=key(row);if(seen.has(id))continue;seen.add(id);
    const {alternatives_at_br,...candidate}=row;result.push(candidate);
    if(result.length>=limit)break;
  }
  return result;
}
function lineupSplit(all,applied,ids,config,context = {}) {
  const before=new Map(all.map(v=>[v.id,v])),after=new Map(applied.vehicles.map(v=>[v.id,v]));
  const member_deltas=ids.map(id=>({id,before:getBR(before.get(id)??{},'current',context),after:getBR(after.get(id)??{},'planned',context)})).filter(row=>finite(row.before)&&finite(row.after)).map(row=>({...row,delta:row.after-row.before}));
  const changed=applied.applied.some(change=>ids.includes(change.vehicle_id)&&change.effective_field==='br.planned');
  const spread=member_deltas.length?Math.max(...member_deltas.map(row=>row.delta))-Math.min(...member_deltas.map(row=>row.delta)):0;
  return {is_split:changed&&spread>config.matching.epsilon,member_deltas};
}
/** Version-page lists: new compositions by new score, disrupted old
 * compositions by OLD score, with their unchanged members evaluated anew. */
export function buildVersionOverview(vehicles,nation,scenario,suppliedContext,config,probabilities={},recommendations={}) {
  const all=list(vehicles),context=contextOf(suppliedContext,config),applied=applyScenario(all,scenario,'planned');
  const current=recommendations.current??recommendNation(all,nation,{...context,version:'current'},config,probabilities);
  const planned=recommendations.planned??recommendNation(applied.vehicles,nation,{...context,version:'planned'},config,probabilities);
  const affected=topLineups(current,Infinity).map(before=>({before,...lineupSplit(all,applied,before.lineup_ids,config,context)})).filter(row=>row.is_split);
  const split_top=affected.slice(0,3).map(({before,member_deltas})=>({before,member_deltas,after_fixed:slimEvaluation(evaluateLineup(applied.vehicles,before.lineup_ids,{...context,version:'planned',includeMatrix:false},config,probabilities),config)}));
  return {nation,planned_top:topLineups(planned),split_top,split_candidate_count:affected.length,split_rank_by:'previous_score',split_definition:'成员 BR 变化不同步，按旧版评分排序；仍可一起编队。',model_version:config.model_version,conditions:planned.conditions,slots:context.slots,simulated:true};
}

export function compareVersions(vehicles, lineupIds, scenario, suppliedContext = {}, config, probabilities = {}) {
  const all = list(vehicles), applied = applyScenario(all, scenario, 'planned');
  const before = evaluateLineup(all, lineupIds, {...suppliedContext, version: 'current'}, config, probabilities);
  const afterFixed = evaluateLineup(applied.vehicles, lineupIds, {...suppliedContext, version: 'planned'}, config, probabilities);
  const nation = before.nation ?? suppliedContext.nation;
  const afterRecommendations = recommendNation(applied.vehicles, nation, {...suppliedContext, version: 'planned'}, config, probabilities);
  const optimized = afterRecommendations.candidates.filter(v => Math.abs(v.candidate_cap - (afterFixed.actual_br ?? suppliedContext.br)) <= config.matching.epsilon).sort((a, b) => (b.score ?? -Infinity) - (a.score ?? -Infinity))[0] ?? afterRecommendations.best;
  const oldIds = new Set(before.environment.opponent_ids), newIds = new Set(afterFixed.environment.opponent_ids);
  const entered = [...newIds].filter(id => !oldIds.has(id)), left = [...oldIds].filter(id => !newIds.has(id));
  const announcements = [];
  if (finite(before.actual_br) && finite(afterFixed.actual_br) && before.actual_br !== afterFixed.actual_br) announcements.push({type: 'lineup_br_changed', vehicle_ids: lineupIds, nation, old_br: before.actual_br, new_br: afterFixed.actual_br, message: `原成员组合最高 BR 由 ${before.actual_br} 变为 ${afterFixed.actual_br}。成员仍可一起编队，但匹配成本发生变化。`, action: 'compare_keep_members_or_reoptimize'});
  const changes = applied.applied.filter(change => lineupIds.includes(change.vehicle_id));
  const split=lineupSplit(all,applied,lineupIds,config,suppliedContext);
  if (split.is_split) announcements.push({type: 'lineup_split', nation, vehicle_ids: split.member_deltas.map(item=>item.id), member_deltas: split.member_deltas, message: '原队列成员 BR 变化不同步；可保留全组合或替换上调幅度较大的成员。', action: 'compare_ground_only_or_replace_aircraft'});
  if (entered.length || left.length) announcements.push({type: 'opponent_boundary_change', nation, vehicle_ids: [...entered, ...left], entered, left, message: '敌我双方按方案版重新计算，主力匹配对手发生进出。离开主力区间的低 BR 载具仍可能被更高队列携带。', action: 'inspect_matrix'});
  const oldAA = new Set(before.air.enemy_aa_ids), newAA = new Set(afterFixed.air.enemy_aa_ids);
  if (stable([...oldAA].sort()) !== stable([...newAA].sort())) announcements.push({type: 'air_defence_environment_change', nation, vehicle_ids: unique([...oldAA, ...newAA]), message: '主力匹配范围内的敌方防空构成改变；航空攻击窗口按同版数据重算。', action: 'inspect_air_window'});
  const byId = new Map(all.map(v => [v.id, v]));
  const withoutAir = lineupIds.filter(id => byId.get(id)?.kind === 'ground');
  const groundAlternative = withoutAir.length && withoutAir.length < lineupIds.length ? slimEvaluation(evaluateLineup(applied.vehicles, withoutAir, {...suppliedContext, version: 'planned', includeMatrix: false}, config, probabilities), config) : null;
  const changedThreats = afterFixed.threats.filter(row => before.threats.find(old => old.vehicle_id === row.vehicle_id)?.category !== row.category);
  return {before, after_fixed: afterFixed, after_optimized: optimized, ground_only_alternative: groundAlternative, entered, left, changed_threats: changedThreats, announcements, changes: applied.applied, lineup_changes: changes, audit: applied.audit, rejected: applied.rejected, source_status: applied.source_status, simulated: true, model_version: config.model_version, same_algorithm_parameters: true, delta: {score: finite(before.score) && finite(afterFixed.score) ? afterFixed.score - before.score : null, highest_br: finite(before.actual_br) && finite(afterFixed.actual_br) ? afterFixed.actual_br - before.actual_br : null, air_window: finite(before.metrics.air_window) && finite(afterFixed.metrics.air_window) ? afterFixed.metrics.air_window - before.metrics.air_window : null}};
}

function erf(value, settings) {
  const sign = value < 0 ? -1 : 1, absolute = Math.abs(value), t = 1 / (1 + settings.p * absolute);
  let polynomial = 0;
  for (const coefficient of settings.coefficients.slice().reverse()) polynomial = (polynomial + coefficient) * t;
  return sign * (1 - polynomial * Math.exp(-absolute * absolute));
}
const normalCDF = (value, settings) => (1 + erf(value / Math.sqrt(2), settings)) / 2;
function nearestValidCenter(indices, width) {
  const x = mean(indices.map(index => index % width)), y = mean(indices.map(index => Math.floor(index / width)));
  let best = indices[0], distance = Infinity;
  for (const index of indices) {
    const candidate = (index % width - x) ** 2 + (Math.floor(index / width) - y) ** 2;
    if (candidate < distance || candidate === distance && index < best) {best = index; distance = candidate;}
  }
  return best;
}
function neighbors(index, width, height) {
  const x = index % width, y = Math.floor(index / width), result = [];
  if (x) result.push(index - 1);
  if (x + 1 < width) result.push(index + 1);
  if (y) result.push(index - width);
  if (y + 1 < height) result.push(index + width);
  return result;
}
function robustCenter(indices, width, height) {
  const members = new Set(indices), distances = new Map(), queue = [];
  for (const index of indices) {
    const around = neighbors(index, width, height);
    const x = index % width, y = Math.floor(index / width);
    if (!x || x + 1 === width || !y || y + 1 === height || around.some(next => !members.has(next))) {distances.set(index, 0); queue.push(index);}
  }
  // A one-pixel canvas/entire canvas still has an edge, independent of shape.
  if (!queue.length) {distances.set(indices[0], 0); queue.push(indices[0]);}
  for (let head = 0; head < queue.length; head++) {
    const index = queue[head];
    for (const next of neighbors(index, width, height)) if (members.has(next) && !distances.has(next)) {distances.set(next, distances.get(index) + 1); queue.push(next);}
  }
  const depth = [...distances.values()].reduce((best, value) => Math.max(best, value), 0);
  return nearestValidCenter(indices.filter(index => distances.get(index) === depth), width);
}
function connectedComponents(mask, width, height) {
  const seen = new Uint8Array(mask.length), result = [];
  for (let start = 0; start < mask.length; start++) {
    if (!mask[start] || seen[start]) continue;
    const queue = [start]; seen[start] = 1;
    for (let head = 0; head < queue.length; head++) for (const next of neighbors(queue[head], width, height)) if (mask[next] && !seen[next]) {seen[next] = 1; queue.push(next);}
    result.push(queue);
  }
  return result;
}

/** Integrate actual Gaussian impact mass over every pixel. Target selection
 * and impact error are distinct; neither off-target mass nor unknown material
 * is discarded or renormalized into known/penetrable pixels. */
export function integrateAim(shard, suppliedContext = {}, config) {
  const context = contextOf(suppliedContext, config), width = shard.width ?? shard.metadata?.width, height = shard.height ?? shard.metadata?.height;
  const pitch = shard.pixel_pitch_m ?? shard.metadata?.pixel_pitch_m;
  const probability = shard.probabilities ?? shard.penetration_probability, status = shard.status;
  if (!Number.isInteger(width) || !Number.isInteger(height) || width <= 0 || height <= 0 || !finite(pitch) || pitch <= 0 || probability?.length !== width * height || status?.length !== width * height) throw new Error('Invalid dynamic probability grid dimensions, pitch, or array lengths');
  const target = [], weak = new Uint8Array(width * height);
  for (let index = 0; index < status.length; index++) {
    if (status[index] !== 0) target.push(index);
    if (status[index] === 3 && finite(probability[index]) && probability[index] >= config.aim.weak_probability_threshold) weak[index] = 1;
    if (![0, 1, 2, 3].includes(status[index])) throw new Error(`Unsupported status ${status[index]}`);
    if (finite(probability[index]) && (probability[index] < 0 || probability[index] > 1)) throw new Error('Probability outside [0,1]');
  }
  if (!target.length) return {mean: 0, lower: 0, upper: 0, known_mass: 0, unknown_mass: 0, no_protected_target_mass: 0, miss_mass: 1, aim_points: [], coverage: 0, empty_target: true, model_version: config.model_version};
  const components = connectedComponents(weak, width, height).map(indices => ({indices, value: mean(indices.map(index => probability[index])), weight: indices.length * mean(indices.map(index => probability[index]))})).sort((a, b) => b.weight - a.weight || a.indices[0] - b.indices[0]).slice(0, config.aim.max_weak_components);
  const weakPreference = components.length ? logistic(config.aim.weak_preference_intercept + context.skill * config.aim.weak_preference_skill_slope) : 0;
  const center = nearestValidCenter(target, width);
  const points = [{index: center, weight: 1 - weakPreference, type: 'general_center'}];
  const weakTotal = components.reduce((sum, component) => sum + component.weight, 0);
  for (const component of components) points.push({index: robustCenter(component.indices, width, height), weight: weakPreference * component.weight / weakTotal, type: 'robust_weak_region', region_samples: component.indices.length});
  const distanceFactor = Math.max(config.aim.minimum_distance_factor, context.distance_m / config.aim.distance_scale_m);
  const skillFactor = Math.exp(-context.skill * config.aim.skill_error_slope);
  const control = finite(context.gun_control_deg_s) ? Math.exp(config.aim.gun_control_sensitivity * (config.aim.gun_control_reference_deg_s - context.gun_control_deg_s) / config.aim.gun_control_reference_deg_s) : 1;
  const ownMotion = (context.own_motion_mps ?? config.defaults.own_motion_mps) * config.aim.own_motion_sigma_m_per_mps;
  const targetMotion = (context.target_motion_mps ?? config.defaults.target_motion_mps) * config.aim.target_motion_sigma_m_per_mps;
  const sigmaX = Math.max(pitch * config.aim.minimum_sigma_pixels, context.sigma_x_m ?? Math.hypot(config.aim.base_sigma_x_m * distanceFactor * skillFactor * control, ownMotion, targetMotion));
  const sigmaY = Math.max(pitch * config.aim.minimum_sigma_pixels, context.sigma_y_m ?? Math.hypot(config.aim.base_sigma_y_m * distanceFactor * skillFactor * control, ownMotion));
  const mass = new Float64Array(width * height);
  const aimPoints = [];
  for (const point of points) {
    const centerX = point.index % width + 1 / 2, centerY = Math.floor(point.index / width) + 1 / 2;
    const xMass = new Float64Array(width), yMass = new Float64Array(height);
    for (let x = 0; x < width; x++) xMass[x] = Math.max(0, normalCDF((x + 1 - centerX) * pitch / sigmaX, config.aim.cdf) - normalCDF((x - centerX) * pitch / sigmaX, config.aim.cdf));
    for (let y = 0; y < height; y++) yMass[y] = Math.max(0, normalCDF((y + 1 - centerY) * pitch / sigmaY, config.aim.cdf) - normalCDF((y - centerY) * pitch / sigmaY, config.aim.cdf));
    for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) mass[y * width + x] += point.weight * xMass[x] * yMass[y];
    aimPoints.push({...point, x: centerX - 1 / 2, y: centerY - 1 / 2, inside_target: status[point.index] !== 0});
  }
  let known = 0, unknown = 0, noTarget = 0, background = 0, knownSum = 0, inCanvas = 0;
  for (let index = 0; index < mass.length; index++) {
    const weight = mass[index]; inCanvas += weight;
    if (status[index] === 0) background += weight;
    else if (status[index] === 1) noTarget += weight;
    else if (status[index] === 2 || !finite(probability[index])) unknown += weight;
    else {known += weight; knownSum += weight * probability[index];}
  }
  const outside = Math.max(0, 1 - inCanvas), lower = clamp(knownSum), upper = clamp(knownSum + unknown);
  return {mean: clamp(knownSum + unknown * config.pair.unknown_prior), lower, upper, known_mass: known, unknown_mass: unknown, no_protected_target_mass: noTarget, miss_mass: background + outside, background_mass: background, outside_canvas_mass: outside, coverage: known, conditional_known_region_mean: known > 0 ? knownSum / known : null, aim_points: aimPoints, sigma_x_m: sigmaX, sigma_y_m: sigmaY, skill: context.skill, aiming_distance_m: context.distance_m, physical_distance_m: shard.physical_distance_m ?? shard.metadata?.physical_distance_m ?? null, angle_deg: shard.angle_deg ?? shard.metadata?.angle_deg ?? null, width, height, pixel_pitch_m: pitch, physical_probability_unchanged: true, model_version: config.model_version, calibration_status: config.aim.calibration_status, semantics: 'expected_conditional_protected_target_penetration_utility', notes: ['未知区域保留上下界与明确先验；状态 1 仅表示本射线没有当前受保护内部目标，不等于车辆不可受损。', config.aim.note, '未模拟碎片、爆压、乘员毁伤和游戏胜负。']};
}

/** Offline parameter fit import. Aggregate K/D or win rate is deliberately not
 * mapped directly to aim error; the producer supplies a documented fit and
 * validation report, and import alone does not mark the model validated. */
export function applyCalibration(baseConfig, calibration) {
  const audit = [], result = copy(baseConfig);
  for (const field of baseConfig.calibration.required_metadata) if (!present(calibration?.[field])) audit.push({field, reason: 'missing_calibration_metadata'});
  if (calibration?.mode !== 'ground_realistic') audit.push({field: 'mode', reason: 'wrong_battle_mode'});
  if (calibration?.base_model_version !== baseConfig.model_version) audit.push({field: 'base_model_version', reason: 'model_version_conflict'});
  for (const item of calibration?.parameters ?? []) {
    const path = item.path ?? '';
    if (!baseConfig.calibration.allowed_parameter_roots.includes(path.split('.')[0]) || path.split('.').some(part => ['__proto__', 'constructor', 'prototype'].includes(part))) audit.push({field: path, reason: 'unsupported_parameter'});
    else if (!finite(pathGet(baseConfig, path)) || !finite(item.value)) audit.push({field: path, reason: 'numeric_existing_parameter_required'});
  }
  if (audit.length) return {applied: false, config: result, audit};
  for (const item of calibration.parameters ?? []) pathSet(result, item.path, item.value);
  result.model_version = calibration.fitted_model_version ?? `${baseConfig.model_version}+offline-fit`;
  result.calibration_status = baseConfig.calibration.result_status;
  result.calibration_evidence = Object.fromEntries(baseConfig.calibration.required_metadata.map(key => [key, calibration[key]]));
  const validated = validateModelConfig(result);
  if (!validated.valid) return {applied: false, config: copy(baseConfig), audit: validated.errors};
  return {applied: true, config: result, audit};
}

export function validateModelConfig(config) {
  const errors = [];
  if (!config || typeof config !== 'object') return {valid: false, errors: [{field: 'config', reason: 'object_required'}]};
  for (const section of ['defaults', 'matching', 'roles', 'search', 'pair', 'lineup', 'air', 'aim', 'cache', 'calibration', 'ui']) if (!config[section] || typeof config[section] !== 'object') errors.push({field: section, reason: 'missing_section'});
  if (typeof config.model_version !== 'string' || !config.model_version) errors.push({field: 'model_version', reason: 'version_required'});
  const visit = (value, path = '') => {
    if (typeof value === 'number' && !finite(value)) errors.push({field: path, reason: 'finite_number_required'});
    if (value && typeof value === 'object') {
      if (path.endsWith('weights') || path.endsWith('scenario_prior')) {
        const weights = Object.values(value);
        if (!weights.length || weights.some(weight => !finite(weight) || weight < 0) || weights.reduce((a, b) => a + b, 0) <= 0) errors.push({field: path, reason: 'nonnegative_nonempty_weights_required'});
      }
      for (const [key, item] of Object.entries(value)) visit(item, path ? `${path}.${key}` : key);
    }
  };
  visit(config);
  for (const field of ['matching.max_br_spread', 'matching.epsilon', 'aim.base_sigma_x_m', 'aim.base_sigma_y_m', 'aim.distance_scale_m', 'aim.minimum_sigma_pixels', 'search.lookback_br', 'search.candidates_per_role', 'search.variants_per_br', 'search.max_pair_cache_entries', 'pair.sampled_layers.max_cached_profiles_per_view', 'pair.sampled_layers.minimum_slope_ratio']) if (!finite(pathGet(config, field)) || pathGet(config, field) <= 0) errors.push({field, reason: 'positive_number_required'});
  for (const field of ['pair.unknown_prior', 'pair.minimum_evidence_fraction', 'pair.aa_aa_aggregation_weight', 'aim.weak_probability_threshold', 'lineup.worst_quantile', 'air.unknown_sp_availability', 'pair.sampled_layers.penetration_transition_fraction', 'pair.sampled_layers.model_uncertainty']) if (!finite(pathGet(config, field)) || pathGet(config, field) < 0 || pathGet(config, field) > 1) errors.push({field, reason: 'fraction_required'});
  if (!finite(config.lineup?.duel_epsilon) || config.lineup.duel_epsilon<=0 || config.lineup.duel_epsilon>=0.5) errors.push({field:'lineup.duel_epsilon',reason:'positive_below_half_required'});
  if (!Number.isInteger(config.defaults?.slots) || config.defaults.slots <= 0) errors.push({field: 'defaults.slots', reason: 'positive_integer_required'});
  if (!finite(config.defaults?.skill)) errors.push({field: 'defaults.skill', reason: 'finite_number_required'});
  for (const [name, limits] of Object.entries(config.ui ?? {})) if (limits && typeof limits === 'object' && (!finite(limits.min) || !finite(limits.max) || limits.max < limits.min || !finite(limits.step) || limits.step <= 0)) errors.push({field: `ui.${name}`, reason: 'invalid_ui_range'});
  return {valid: !errors.length, errors};
}
