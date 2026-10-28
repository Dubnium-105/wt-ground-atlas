import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {applyScenario, getBRs, getBR, matchEnvironment, evaluatePair, evaluateLineup, recommendNation, compareVersions, validateLoadout, foldEquivalents, integrateAim, validateModelConfig, applyCalibration, aimContextKey, sampledAmmunitionResponse, duelBalance, topLineups, buildVersionOverview} from '../shared/engine.mjs';

// These are synthetic acceptance fixtures, never production vehicle facts.
const config = JSON.parse(fs.readFileSync(new URL('../config/model.json', import.meta.url)));
const fixtureAmmo = (id, mm = 400) => ({id, name: id, type: 'APFSDS', ammo_profile_sha256: `fixture-profile-${id}`, penetration: [{distance_m: 0, angle_deg: 0, mm: mm + 50}, {distance_m: 1000, angle_deg: 0, mm}, {distance_m: 2000, angle_deg: 0, mm: mm - 50}]});
const vehicle = (id, nation, br, more = {}) => ({id, name: id, nation, kind: 'ground', role: 'mbt', rank: 1, special: false, br: {current: br, planned: br}, configuration_id: id, geometry_version: 'fixture-geometry', configuration_version: 'fixture-configuration', source_version: 'fixture-source', mobility: {forward_kmh: 60, reverse_kmh: 20, power_to_weight_hp_t: 20, neutral_steering: true}, firepower: {reload_type: 'manual', reload_ace_s: 6, traverse_deg_s: 30, vertical_deg_s: 15}, protection: {front_mm: 300, homogeneous_verified: true}, sensors: {thermal_generation: 2}, auxiliary: {smoke_grenades: true, engine_smoke: true}, ammo: [fixtureAmmo(`${id}-shell`)], loadouts: [], ...more});
const configCopy = () => structuredClone(config);

test('AT01 / AT11 asynchronous ground-air changes keep fixed lineup, optimize, and retain ground-only option', () => {
  const ground = vehicle('fixture-ground', 'israel', 10.0), air = vehicle('fixture-ayit', 'israel', 10.0, {kind: 'aircraft', role: 'cas'}), enemy = vehicle('fixture-enemy', 'ussr', 11.7);
  const scenario = {status: 'test_fixture', entries: [{vehicle_id: ground.id, field: 'br', old: 10, new: 10.3}, {vehicle_id: air.id, field: 'br', old: 10, new: 10.7}]};
  const result = compareVersions([ground, air, enemy], [ground.id, air.id], scenario, {slots: 2}, config);
  assert.equal(result.before.actual_br, 10);
  assert.equal(result.after_fixed.actual_br, 10.7);
  assert.deepEqual(result.after_fixed.lineup_ids, [ground.id, air.id]);
  assert.equal(result.ground_only_alternative.actual_br, 10.3);
  assert.ok(result.after_optimized);
  assert.ok(result.announcements.some(item => item.type === 'lineup_split'));
  assert.equal(result.same_algorithm_parameters, true);
  assert.equal(getBR(ground, 'current'), 10, 'baseline not mutated');
});

test('AT02 actual lineup maximum and inclusive configurable match boundary', () => {
  const members = [vehicle('a', 'usa', 10.7), vehicle('inside', 'ussr', 11.7), vehicle('outside', 'ussr', 12.0)];
  const result = matchEnvironment(members, ['a'], 'current', config);
  assert.equal(result.actual_br, 10.7);
  assert.deepEqual(result.opponent_ids, ['inside']);
  const c = configCopy(); c.matching.max_br_spread = 1.3;
  assert.deepEqual(matchEnvironment(members, ['a'], 'current', c).opponent_ids, ['inside', 'outside']);
  const evaluated = evaluateLineup(members, ['a'], {br: 13}, config);
  assert.equal(evaluated.actual_br, 10.7, 'arbitrary requested cap must not inflate actual BR');
});

test('AT03 changing only enemy BR changes environment and announcements', () => {
  const members = [vehicle('a', 'usa', 10.7), vehicle('enemy', 'ussr', 12)];
  const scenario = {entries: [{vehicle_id: 'enemy', field: 'br', old: 12, new: 11.7}]};
  const result = compareVersions(members, ['a'], scenario, {}, config);
  assert.deepEqual(result.entered, ['enemy']);
  assert.equal(result.before.environment.opponents.length, 0);
  assert.equal(result.after_fixed.environment.opponents.length, 1);
});

test('AT04 exact M829A2 data is not reused for XM885 or unmatched physical/source conditions', () => {
  const a = vehicle('fixture-M1A2', 'usa', 12, {ammo: [fixtureAmmo('M829A2')]}), b = vehicle('fixture-HSTVL', 'usa', 12, {ammo: [fixtureAmmo('XM885', 250)]}), target = vehicle('fixture-T90M', 'ussr', 12, {protection: {physical_thickness_only: true}});
  const record = {target_id: target.id, configuration_id: target.configuration_id, ammo_id: 'M829A2', ammo_profile_sha256: a.ammo[0].ammo_profile_sha256, physical_distance_m: 1000, angle_deg: 0, geometry_version: target.geometry_version, configuration_version: target.configuration_version, source_version: target.source_version, model_version: 'fixture-armor-model', aim_context_key: aimContextKey({}, config), reference_only: false, mean: 0.8, lower: 0.7, upper: 0.9, coverage: 1};
  const data = {model_version: 'fixture-armor-model', records: [record]};
  const first = evaluatePair(a, target, {}, config, data), second = evaluatePair(b, target, {}, config, data);
  assert.equal(first.quality, 'conditional_layered_model');
  assert.equal(first.components.armor_pressure.value, 0.8);
  assert.notEqual(second.quality, first.quality);
  assert.equal(second.components.armor_pressure.value, null);
  assert.notEqual(first.ammo_id, second.ammo_id);
  assert.notEqual(evaluatePair(a, target, {physical_distance_m: 2000}, config, data).quality, 'conditional_layered_model');
  assert.notEqual(evaluatePair(a, {...target, source_version: null}, {}, config, data).quality, 'conditional_layered_model');
  assert.notEqual(evaluatePair({...a, ammo: [{...a.ammo[0], ammo_profile_sha256: 'different-profile'}]}, target, {}, config, data).quality, 'conditional_layered_model');
  const modified = applyScenario([a, target], {entries: [{vehicle_id: target.id, field: 'protection.front_mm', old: null, new: 999}]}).vehicles;
  assert.equal(modified[1].probability_cache_invalidated_by_scenario, true);
  assert.notEqual(evaluatePair(modified[0], modified[1], {version: 'planned'}, config, data).quality, 'conditional_layered_model');
  assert.notEqual(evaluatePair(a, target, {skill: 2}, config, data).quality, 'conditional_layered_model');
  assert.notEqual(evaluatePair(a, target, {distance_m: 2000}, config, data).quality, 'conditional_layered_model');
});

test('reference-only heatmaps and ammunition name resemblance cannot establish availability', () => {
  const a = vehicle('a', 'usa', 10), t = vehicle('t', 'ussr', 10, {protection: {}});
  const result = evaluatePair(a, t, {ammo_id: 'M829A2'}, config, {entries: [{target_id: t.id, ammo_id: 'M829A2'}]});
  assert.equal(result.ammo_id, null);
  assert.equal(result.components.armor_pressure.value, null);
  assert.ok(result.missing.includes('attacker.ammunition'));
});

test('AT05 only confirmed equivalent configurations fold, display fold does not mutate aggregate', () => {
  const members = [vehicle('a', 'usa', 10), vehicle('verified-copy1', 'ussr', 10, {equivalence_group: 'same', equivalence_verified: true}), vehicle('verified-copy2', 'germany', 10, {equivalence_group: 'same', equivalence_verified: true}), vehicle('fixture-bmpt', 'ussr', 10, {family: 't72'}), vehicle('fixture-t72', 'ussr', 10, {family: 't72'}), vehicle('fixture-a4', 'germany', 10, {family: 'leopard2'}), vehicle('fixture-a5', 'germany', 10, {family: 'leopard2'})];
  const before = evaluateLineup(members, ['a'], {}, config);
  const grouped = foldEquivalents(members);
  assert.equal(grouped.length, members.length - 1);
  assert.equal(grouped.find(group => group.id === 'same').members.length, 2);
  assert.equal(evaluateLineup(members, ['a'], {}, config).score, before.score);
  assert.equal(before.environment.opponent_weights['verified-copy1'] + before.environment.opponent_weights['verified-copy2'], before.environment.opponent_weights['fixture-bmpt']);
});

test('AT06 AA-AA aggregation is reduced while AA-air remains unweakened', () => {
  const aa = vehicle('aa', 'usa', 10, {role: 'spaa', air_defence: {missile_range_m: 10000, missile_overload_g: 30, ready_missiles: 4, scan_period_s: 2, gun_rate_rpm: 2500}}), enemyAA = vehicle('enemy-aa', 'ussr', 10, {role: 'spaa'}), plane = vehicle('plane', 'ussr', 10, {kind: 'aircraft', role: 'cas'});
  assert.equal(evaluatePair(aa, enemyAA, {}, config).aggregation_weight, config.pair.aa_aa_aggregation_weight);
  const aerial = evaluatePair(aa, plane, {}, config);
  assert.equal(aerial.aggregation_weight, config.pair.default_aggregation_weight);
  assert.ok(aerial.score > 0);
});

test('AT07 mutually exclusive stores and anti-radiation emission conditions', () => {
  const illegal = {id: 'bad', legal: true, weapons: [{id: 'agm', type: 'agm', count: 1, station: 'left'}, {id: 'arm', type: 'arm', count: 1, station: 'left'}]};
  const legal = {id: 'legal', legal: true, weapons: [{id: 'agm', type: 'agm', count: 2, station: 'left', mass_kg: 100}, {id: 'arm', type: 'arm', count: 1, station: 'right', mass_kg: 100}], max_payload_kg: 1000};
  const plane = vehicle('plane', 'usa', 10, {kind: 'aircraft', loadouts: [illegal, legal]});
  assert.equal(validateLoadout(plane, illegal, {}, config).valid, false);
  assert.deepEqual(validateLoadout(plane, legal, {target_radar_emitting: false}, config).active_weapons.map(w => w.id), ['agm']);
  assert.deepEqual(validateLoadout(plane, legal, {target_radar_emitting: true}, config).active_weapons.map(w => w.id), ['agm', 'arm']);
  const fabricated = {...legal, weapons: [...legal.weapons, {id: 'extra', type: 'bomb', count: 3}]};
  assert.equal(validateLoadout(plane, fabricated, {}, config).valid, false);
});

test('AT08 unknown/NaN/background/no-inner-target and off-canvas mass stay distinct', () => {
  const shard = {width: 3, height: 3, pixel_pitch_m: 0.1, probabilities: [NaN, NaN, NaN, NaN, 0, 0.5, NaN, 1, NaN], status: [0, 0, 0, 1, 3, 2, 0, 3, 0]};
  const result = integrateAim(shard, {sigma_x_m: 0.25, sigma_y_m: 0.2}, config);
  assert.ok(result.unknown_mass > 0);
  assert.ok(result.no_protected_target_mass > 0);
  assert.ok(result.miss_mass > 0);
  assert.ok(result.upper > result.lower);
  assert.ok(Math.abs(result.known_mass + result.unknown_mass + result.no_protected_target_mass + result.miss_mass - 1) < 1e-9);
  assert.ok(result.aim_points.every(point => point.inside_target));
});

test('AT09 skill changes impact selection/error without altering armor samples', () => {
  const shard = {width: 5, height: 3, pixel_pitch_m: 0.1, probabilities: new Float32Array([0, 0, 0, 0, 0, 0, 0.2, 0.1, 1, 0, 0, 0, 0, 0, 0]), status: new Uint8Array([0, 0, 0, 0, 0, 0, 3, 3, 3, 0, 0, 0, 0, 0, 0])};
  const copy = [...shard.probabilities], normal = integrateAim(shard, {}, config), skilled = integrateAim(shard, {skill: 2}, config);
  assert.equal(normal.skill, 0);
  assert.ok(skilled.sigma_x_m < normal.sigma_x_m);
  assert.ok(skilled.aim_points.find(p => p.type === 'robust_weak_region').weight > normal.aim_points.find(p => p.type === 'robust_weak_region').weight);
  assert.deepEqual([...shard.probabilities], copy);
});

test('AT10 aiming distance is independent of physical penetration range', () => {
  const shard = {width: 2, height: 2, pixel_pitch_m: 0.1, probabilities: [0.2, 0.3, 0.4, 0.5], status: [3, 3, 3, 3], physical_distance_m: 0};
  const near = integrateAim(shard, {distance_m: 500}, config), far = integrateAim(shard, {distance_m: 2000}, config);
  assert.equal(far.physical_distance_m, 0);
  assert.ok(far.sigma_x_m > near.sigma_x_m);
  assert.equal(far.physical_probability_unchanged, true);
  assert.deepEqual(shard.probabilities, [0.2, 0.3, 0.4, 0.5]);
});

test('aim centers cannot land in the hole of a connected ring', () => {
  const status = [3, 3, 3, 3, 0, 3, 3, 3, 3], shard = {width: 3, height: 3, pixel_pitch_m: 0.1, probabilities: status.map(s => s ? 0.9 : NaN), status};
  const result = integrateAim(shard, {}, config);
  assert.ok(result.aim_points.every(point => point.index !== 4 && status[point.index] !== 0));
});

test('AT13 deterministic repeated recommendations scan all eligible BR caps', () => {
  const members = [vehicle('a', 'usa', 9), vehicle('b', 'usa', 10, {role: 'light'}), vehicle('aa', 'usa', 10, {role: 'spaa'}), vehicle('c', 'ussr', 10)];
  const first = recommendNation(members, 'usa', {slots: 2}, config), second = recommendNation(members, 'usa', {slots: 2}, config);
  assert.deepEqual(first, second);
  assert.deepEqual(first.search.br_caps_scanned, [9, 10]);
  assert.equal(first.search.global_optimum_guaranteed, false);
  assert.ok(first.candidates.every(c => c.lineup_ids.length <= 2 && c.actual_br <= c.candidate_cap));
});

test('conflicts, duplicate rows, unknown IDs and text-only rows are audited without mutation', () => {
  const original = vehicle('a', 'usa', 10);
  const result = applyScenario([original], {entries: [{vehicle_id: 'a', field: 'br', old: 10, new: 11}, {vehicle_id: 'a', field: 'br', old: 10, new: 12}, {vehicle_id: 'missing', field: 'br', old: 10, new: 11}, {vehicle_id: 'a', field: 'mobility.forward_kmh', old: 99, new: 80}, {vehicle_id: 'a', field: 'firepower.reload_ace_s', description: 'faster'}]}, 'planned');
  assert.equal(result.applied.length, 0);
  assert.equal(result.rejected.length, 5);
  assert.equal(result.vehicles[0].br.planned, 10);
  assert.equal(original.mobility.forward_kmh, 60);
});

test('non-BR physical changes are applied to both sides only in planned snapshot', () => {
  const a = vehicle('a', 'usa', 10), b = vehicle('b', 'ussr', 10);
  const planned = applyScenario([a, b], {entries: [{vehicle_id: 'b', field: 'firepower.reload_ace_s', old: 6, new: 3}]}, 'planned').vehicles;
  assert.equal(planned[1].firepower.reload_ace_s, 3);
  const before = evaluatePair(a, b, {}, config), after = evaluatePair(planned[0], planned[1], {version: 'planned'}, config);
  assert.ok(after.components.tempo.value < before.components.tempo.value);
  assert.equal(b.firepower.reload_ace_s, 6);
});

test('unverified Ground RB, unavailable vehicles, mixed nations and duplicated systems are rejected', () => {
  const a = vehicle('a', 'usa', 10), air = vehicle('air', 'usa', null, {kind: 'aircraft'}), special = vehicle('special', 'usa', 10, {special: true}), enemy = vehicle('enemy', 'ussr', 10);
  assert.ok(evaluateLineup([a, air], ['a', 'air'], {}, config).invalid.includes('unknown_ground_rb'));
  assert.ok(evaluateLineup([a, special], ['a', 'special'], {}, config).invalid.includes('special_vehicles_excluded'));
  assert.ok(evaluateLineup([a, enemy], ['a', 'enemy'], {}, config).invalid.includes('mixed_nations'));
  const rec = recommendNation([a, air, special, enemy], 'usa', {availableIds: ['a']}, config);
  assert.deepEqual(rec.best.lineup_ids, ['a']);
});

test('unknown input and physical hull thickness are never converted to zero or fictitious RHAe', () => {
  const a = vehicle('a', 'usa', 10, {ammo: [], mobility: {}, firepower: {}, sensors: {}, auxiliary: {}}), b = vehicle('b', 'ussr', 10, {protection: {front_mm: 500, physical_thickness_only: true}, mobility: {}, firepower: {}, sensors: {}, auxiliary: {}});
  const p = evaluatePair(a, b, {}, config);
  assert.equal(p.score, null);
  assert.equal(p.components.armor_pressure.value, null);
  assert.equal(p.lower, 0);
  assert.equal(p.upper, 1);
  assert.equal(p.coverage, 0);
});

test('reverse response is independent and cannot be 1 minus forward response', () => {
  const a = vehicle('a', 'usa', 10, {ammo: [fixtureAmmo('high-a', 900)]}), b = vehicle('b', 'ussr', 10, {ammo: [fixtureAmmo('high-b', 900)]});
  const result = evaluateLineup([a, b], ['a'], {}, config);
  const f = result.matrix[0], r = result.reverse_matrix[0];
  assert.equal(f.score, r.score);
  assert.notEqual(r.score, 1 - f.score);
  assert.notEqual(f.ammo_id, r.ammo_id);
});

test('unchanged member is part of asynchronous split comparison', () => {
  const members = [vehicle('ground', 'usa', 10), vehicle('plane', 'usa', 10, {kind: 'aircraft', role: 'cas'}), vehicle('enemy', 'ussr', 11)];
  const result = compareVersions(members, ['ground', 'plane'], {entries: [{vehicle_id: 'plane', field: 'br', old: 10, new: 10.7}]}, {}, config);
  const split = result.announcements.find(item => item.type === 'lineup_split');
  assert.ok(split);
  assert.equal(split.member_deltas.find(item => item.id === 'ground').delta, 0);
});

test('invalid numeric config and scenario values cannot become silent NaN scores', () => {
  assert.equal(validateModelConfig(config).valid, true);
  const broken = configCopy(); broken.aim.base_sigma_x_m = NaN; broken.pair.weights.tempo = -1;
  assert.equal(validateModelConfig(broken).valid, false);
  const a = vehicle('a', 'usa', 10);
  const result = applyScenario([a], {entries: [{vehicle_id: 'a', field: 'firepower.reload_ace_s', old: 6, new: Infinity}]});
  assert.equal(result.applied.length, 0);
  assert.equal(result.rejected[0].reason, 'nonfinite_numeric_value');
});

test('calibration imports require evidence metadata and do not assert validation', () => {
  assert.equal(applyCalibration(config, {parameters: [{path: 'aim.base_sigma_x_m', value: 0.1}]}).applied, false);
  const evidence = {source: 'fixture-statistics', mode: 'ground_realistic', time_window: {start: 'fixture-start', end: 'fixture-end'}, sample_count: 123, bias_notes: ['fixture'], fit_method: 'fixture fit', validation_report: 'fixture held-out report', base_model_version: config.model_version, fitted_model_version: 'fixture-calibrated', parameters: [{path: 'aim.base_sigma_x_m', value: 0.1}]};
  const result = applyCalibration(config, evidence);
  assert.equal(result.applied, true);
  assert.equal(result.config.aim.base_sigma_x_m, 0.1);
  assert.notEqual(result.config.calibration_status, 'validated');
  assert.equal(config.aim.base_sigma_x_m, 0.25);
});

test('angle-only chemical penetration does not turn penetration millimetres into distance', () => {
  const ammo = {id: 'chemical', name: 'chemical', type: 'HEAT_FS', range_invariant: true, penetration: [{distance_m: null, angle_deg: 0, mm: 480}, {distance_m: null, angle_deg: 30, mm: 416}]};
  const a = vehicle('a', 'usa', 10, {ammo: [ammo]}), b = vehicle('b', 'ussr', 10);
  const result = evaluatePair(a, b, {physical_distance_m: 1000}, config);
  assert.equal(result.penetration.mm, 480);
  assert.equal(result.penetration.source_distance_m, null);
  assert.equal(result.penetration.distance_m, 1000);
});

test('selected missile launch interval is never borrowed as a human reload cycle', () => {
  const missile = {...fixtureAmmo('missile'), weapon_profile: {id: 'launcher', reload_type: 'mechanism_unspecified', listed_reload_s: 0.5, reload_ace_s: null, autoload_s: null}};
  const a = vehicle('a', 'usa', 10, {ammo: [missile]}), b = vehicle('b', 'ussr', 10);
  const result = evaluatePair(a, b, {}, config);
  assert.equal(result.components.tempo.reload_s, null);
  assert.equal(result.components.tempo.reload_basis, 'mechanism_unspecified');
});

test('high aircraft-only cap can still use a lower ground anchor and reports real maximum', () => {
  const members = [vehicle('ground', 'usa', 1), vehicle('plane', 'usa', 5, {kind: 'aircraft', role: 'cas'}), vehicle('enemy', 'ussr', 5)];
  const result = recommendNation(members, 'usa', {slots: 5}, config);
  assert.ok(result.candidates.some(candidate => candidate.candidate_cap === 5));
  assert.ok(result.candidates.every(candidate => candidate.lineup_ids.includes('ground')));
});

test('aim identity covers every error control and is stable across property order', () => {
  const baseline = aimContextKey({}, config);
  assert.equal(baseline, aimContextKey({skill: config.defaults.skill, distance_m: config.defaults.distance_m}, config));
  assert.equal(aimContextKey({skill: 1, distance_m: 2000}, config), aimContextKey({distance_m: 2000, skill: 1}, config));
  for (const [key, value] of Object.entries({skill: 1, distance_m: 2000, own_motion_mps: 3, target_motion_mps: 4, gun_control_deg_s: 30, sigma_x_m: 0.2, sigma_y_m: 0.3})) assert.notEqual(baseline, aimContextKey({[key]: value}, config), key);
  const changed = configCopy(); changed.aim.base_sigma_x_m *= 2;
  assert.notEqual(baseline, aimContextKey({}, changed));
  changed.aim.base_sigma_x_m = config.aim.base_sigma_x_m; changed.pair.unknown_prior = 0.3;
  assert.notEqual(baseline, aimContextKey({}, changed));
});

test('narrow uncertainty bounds use exactly the same tier priors despite unequal opponent counts', () => {
  const c = configCopy();
  c.pair.weights = {armor_pressure: 1, tempo: 0, mobility: 0, observation: 0, concealment: 0};
  c.pair.coarse_proxy_uncertainty = 0;
  c.lineup.weights = {mean: 1};
  const own = vehicle('own', 'usa', 10);
  const targets = [vehicle('down', 'ussr', 9, {protection: {front_mm: 100, homogeneous_verified: true}}), vehicle('same', 'ussr', 10, {protection: {front_mm: 900, homogeneous_verified: true}}), ...Array.from({length: 8}, (_, index) => vehicle(`up${index}`, 'ussr', 11, {protection: {front_mm: 900, homogeneous_verified: true}}))];
  const result = evaluateLineup([own, ...targets], ['own'], {includeMatrix: false}, c);
  assert.ok(result.metrics.down_tier > result.metrics.up_tier);
  assert.ok(Math.abs(result.lower - result.score) < 1e-12);
  assert.ok(Math.abs(result.upper - result.score) < 1e-12);
  const fewerOpponents = evaluateLineup([own, ...targets.slice(0, 3)], ['own'], {includeMatrix: false}, c);
  assert.ok(Math.abs(fewerOpponents.score - result.score) < 1e-12);
  assert.ok(Math.abs(fewerOpponents.lower - result.lower) < 1e-12);
});

// Simple, auditable armor paths: these are synthetic millimetres, not game facts.
function layerFixture(target, ke=250, ce=600, yaw=0) {
  const identity={geometry_sha256:'fixture-grp',configuration_sha256:'fixture-config',source_version:'fixture-version'};
  target.client_collision=identity;
  return {target_layers:{model:'fixture-layers',targets:[{target_id:target.id,...identity,views:[{angle_deg:yaw,rays:Array.from({length:64},()=>[[ke,ke,1e7,0,ce,ce,1e7,0,1,0]])}]}]}};
}
test('Owned ammunition changes pair and lineup scores against the actual target layers',()=>{
  const weak=fixtureAmmo('own-weak',100),strong=fixtureAmmo('own-strong',400);
  const a=vehicle('a','usa',10,{ammo:[weak,strong]}),t=vehicle('t','ussr',10,{protection:{}}),data=layerFixture(t);
  const low=evaluatePair(a,t,{ammo_id:weak.id},config,data),high=evaluatePair(a,t,{ammo_id:strong.id},config,data);
  assert.equal(low.quality,'sampled_layered_ammunition');
  assert.equal(low.components.armor_pressure.value,0);assert.equal(high.components.armor_pressure.value,1);
  assert.ok(high.score-low.score>.3,'real input changes must produce meaningful pair separation');
  const l=evaluateLineup([a,t],[a.id],{ammo_by_vehicle:{a:weak.id}},config,data);
  const h=evaluateLineup([a,t],[a.id],{ammo_by_vehicle:{a:strong.id}},config,data);
  assert.ok(h.score>l.score);assert.equal(h.ammo_choices.a,strong.id);
  assert.equal(evaluatePair(a,t,{ammo_id:'foreign-shell'},config,data).ammo_id,null);
});
test('Automatic selection uses target KE/CE response rather than the largest nominal penetration',()=>{
  const kinetic=fixtureAmmo('own-ap',400),chemical={...fixtureAmmo('own-heat',500),type:'HEATFS'};
  const a=vehicle('a','usa',10,{ammo:[chemical,kinetic]}),t=vehicle('t','ussr',10,{protection:{}}),data=layerFixture(t,250,600);
  assert.equal(evaluatePair(a,t,{},config,data).ammo_id,kinetic.id);
  assert.equal(evaluatePair(a,t,{ammo_id:chemical.id},config,data).components.armor_pressure.value,0);
  const reverse=evaluatePair(t,a,{},config,data);assert.equal(reverse.ammo_id,'t-shell');
});
test('Own penetration follows physical distance while target yaw selects geometry',()=>{
  const shell={...fixtureAmmo('own-ap'),penetration:[{distance_m:100,angle_deg:0,mm:400},{distance_m:2000,angle_deg:0,mm:200}]};
  const a=vehicle('a','usa',10,{ammo:[shell]}),t=vehicle('t','ussr',10,{protection:{}}),data=layerFixture(t,300,300,180);
  const near=sampledAmmunitionResponse(a,t,shell,{physical_distance_m:100,angle_deg:180},config,data);
  const far=sampledAmmunitionResponse(a,t,shell,{physical_distance_m:2000,angle_deg:180},config,data);
  assert.equal(near.value,1);assert.equal(far.value,0);
  assert.equal(sampledAmmunitionResponse(a,t,shell,{angle_deg:15},config,data),null);
});
test('Geometry identity, source coverage and unsupported projectile types remain explicit',()=>{
  const a=vehicle('a','usa',10),t=vehicle('t','ussr',10,{protection:{}}),data=layerFixture(t);
  data.target_layers.targets[0].views[0].rays[0]=null;
  const response=sampledAmmunitionResponse(a,t,a.ammo[0],{},config,data);
  assert.equal(response.coverage,63/64);assert.equal(response.known_samples,63);
  assert.equal(sampledAmmunitionResponse(a,{...t,client_collision:{...t.client_collision,configuration_sha256:'different'}},a.ammo[0],{},config,data),null);
  assert.equal(sampledAmmunitionResponse(a,{...t,probability_cache_invalidated_by_scenario:true},a.ammo[0],{},config,data),null);
  const he={...a.ammo[0],type:'HE'};
  assert.equal(sampledAmmunitionResponse({...a,ammo:[he]},t,he,{},config,data),null);
});
test('Slope source table is applied once to normal plates, solid paths use their LOS cost',()=>{
  const shell={...fixtureAmmo('own-ap',400),penetration:[{distance_m:1000,angle_deg:0,mm:400},{distance_m:1000,angle_deg:60,mm:250}]};
  const a=vehicle('a','usa',10,{ammo:[shell]}),t=vehicle('t','ussr',10,{protection:{}}),data=layerFixture(t,240);
  for(const ray of data.target_layers.targets[0].views[0].rays){ray[0][1]=480;ray[0][8]=.5;}
  assert.ok(sampledAmmunitionResponse(a,t,shell,{},config,data).value>.8);
  const solid=structuredClone(data);for(const ray of solid.target_layers.targets[0].views[0].rays){ray[0][9]=1;ray[0][3]=800;ray[0][1]=200;}
  assert.equal(sampledAmmunitionResponse(a,t,shell,{},config,solid).value,1);
});

test('Directional pressure contrast stays symmetric and increases separation without changing ordering',()=>{
  assert.equal(duelBalance(.5,.5,config),.5);
  assert.equal(duelBalance(null,null,config),.5);
  const advantage=duelBalance(.7,.6,config),reverse=duelBalance(.6,.7,config);
  assert.ok(Math.abs(advantage-0.608695652173913)<1e-10);
  assert.ok(Math.abs(advantage+reverse-1)<1e-10);
  assert.ok(duelBalance(.8,.6,config)>advantage);
  assert.ok(duelBalance(.6,.6,config)<advantage);
});

test('Top three scan every BR and role variant, removing duplicate member orders and invalid scores',()=>{
  const row=(ids,score,br=10)=>({lineup_ids:ids,score,actual_br:br,valid:true,coverage:1});
  const recs={candidates:[{...row(['a','b'],.7,12),alternatives_at_br:[row(['c','d'],.9,12)]},row(['b','a'],.7,12),row(['e','f'],.8,3),row(['g','h'],.6,7),row(['no-score'],null),{...row(['invalid'],1),valid:false}]};
  assert.deepEqual(topLineups(recs).map(c=>c.lineup_ids),[['c','d'],['e','f'],['a','b']]);
  assert.equal(topLineups({candidates:[]}).length,0);
});
test('Version lists include asynchronous unchanged members, rank old scores and exclude uniform changes',()=>{
  const members=['a','b','c','d','e','f','g','h'].map(id=>vehicle(id,'usa',10));
  members.push(vehicle('enemy','ussr',10));
  const scenario={entries:['a','c','e','g','h'].map(vehicle_id=>({vehicle_id,field:'br',old:10,new:11}))};
  const row=(ids,score,more={})=>({lineup_ids:ids,score,valid:true,actual_br:10,coverage:1,version:'current',...more});
  const current={candidates:[{...row(['a','b'],.9),alternatives_at_br:[row(['c','d'],.95)]},row(['b','a'],.9),row(['e','f'],.8),row(['g','h'],.99)]};
  const planned={candidates:[row(['e','f'],.98,{version:'planned'}),row(['a','b'],.6,{version:'planned'})],conditions:{}};
  const result=buildVersionOverview(members,'usa',scenario,{slots:2},config,{}, {current,planned});
  assert.deepEqual(result.split_top.map(c=>c.before.lineup_ids),[['c','d'],['a','b'],['e','f']]);
  assert.equal(result.split_candidate_count,3);assert.equal(result.split_rank_by,'previous_score');
  assert.equal(result.split_top[0].member_deltas.find(v=>v.id==='d').delta,0);
  assert.equal(result.split_top[0].after_fixed.actual_br,11);
  assert.equal(result.planned_top[0].score,.98);
  const unchanged=buildVersionOverview(members,'usa',{entries:[]},{slots:2},config,{}, {current,planned});
  assert.deepEqual(unchanged.split_top,[]);
  const rejected=buildVersionOverview(members,'usa',{entries:[{vehicle_id:'a',field:'br',old:99,new:11}]},{slots:2},config,{}, {current,planned});
  assert.deepEqual(rejected.split_top,[]);
});
test('Default capacity is ten while smaller available pools remain valid',()=>{
  assert.equal(config.defaults.slots,10);
  const members=Array.from({length:12},(_,i)=>vehicle(`our-${i}`,'usa',10));members.push(vehicle('enemy','ussr',10));
  const full=recommendNation(members,'usa',{},config);
  assert.equal(full.candidates[0].lineup_ids.length,10);
  const scarce=recommendNation(members.slice(0,3),'usa',{},config);
  assert.equal(scarce.candidates[0].lineup_ids.length,3);
});
