import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {
  resolveRatingConfiguration, resolveVehicleRating, validateRatingLoadout,
  enumerateRatingConfigurations,
} from '../shared/rating-configurations.mjs';
import {getBR, getBRs, matchEnvironment, validateLoadout, evaluateLineup,
  applyScenario, recommendNation} from '../shared/engine.mjs';

const config = JSON.parse(fs.readFileSync(new URL('../config/model.json', import.meta.url)));
const basic = {id: 'basic', legal: true, weapons: [{id: 'training-weapon', type: 'bomb', count: 1}]};
const advanced = {id: 'advanced', legal: true, weapons: [{id: 'advanced-weapon', type: 'agm', count: 1}]};
const variant = (id = 'plane', nation = 'usa') => ({
  id, nation, kind: 'aircraft', role: 'cas', special: false, br: {current: 99},
  loadouts: [basic, advanced], ammo: [], mobility: {}, firepower: {},
  protection: {}, sensors: {}, air_defence: {},
  rating_configurations: {
    ground_rb: {status: 'official_verified', source: {url: 'https://example.test/fixture'},
      options: [
        {id: 'low', br: {current: 10.0}, required_modification_ids: [],
          allowed_loadout_ids: ['basic']},
        {id: 'high', br: {current: 11.0}, required_modification_ids: ['research-weapon'],
          allowed_loadout_ids: ['basic', 'advanced'], allowed_weapon_ids: ['training-weapon', 'advanced-weapon']},
      ]},
    air_rb: {status: 'official_verified', source: {url: 'https://example.test/fixture'},
      options: [{id: 'air-low', br: {current: 12.0}, required_modification_ids: [],
        allowed_loadout_ids: ['basic']}]},
  },
});
const ground = (id, nation, br) => ({
  id, nation, kind: 'ground', role: 'mbt', special: false,
  br: {current: br, planned: br}, loadouts: [], ammo: [], mobility: {},
  firepower: {}, protection: {}, sensors: {}, air_defence: {},
});

test('CFG01: fixed-BR legacy records remain compatible with two-argument getBR', () => {
  const vehicle = ground('tank', 'usa', 10.3);
  assert.equal(getBR(vehicle), 10.3);
  assert.equal(getBR(vehicle, 'planned'), 10.3);
  assert.deepEqual(getBRs([vehicle], 'usa'), [10.3]);
  assert.equal(resolveVehicleRating(vehicle).status, 'legacy');
});

test('CFG02: profile selection is explicit; unlocked weapons do not raise or lower BR automatically', () => {
  const vehicle = variant();
  assert.equal(getBR(vehicle), null, 'must not use stale vehicle.br');
  assert.equal(resolveRatingConfiguration(vehicle).errors[0], 'rating_configuration_selection_required');
  const unlocked = {configuration_by_vehicle: {plane: 'low'},
    unlocked_modifications_by_vehicle: {plane: ['research-weapon']}};
  assert.equal(getBR(vehicle, 'current', unlocked), 10.0);
  assert.equal(getBR(vehicle, 'current', {...unlocked, configuration_by_vehicle: {plane: 'high'}}), 11.0);
  assert.equal(getBR(vehicle, 'current', unlocked), 10.0, 'high-tier research is reversible');
});

test('CFG03: mode-specific profiles cannot leak Air RB figures into Ground RB', () => {
  const vehicle = variant();
  assert.equal(resolveRatingConfiguration(vehicle, {mode: 'air_rb', configuration_id: 'air-low'}).br, 12);
  assert.equal(resolveRatingConfiguration(vehicle, {mode: 'ground_rb', configuration_id: 'air-low'}).br, null);
  assert.equal(resolveRatingConfiguration(vehicle, {mode: 'ground_rb', configuration_id: 'low'}).br, 10);
});

test('CFG04: advanced payload is forbidden in low tier, legal in selected high tier', () => {
  const vehicle = variant();
  const low = {configuration_by_vehicle: {plane: 'low'}};
  const high = {configuration_by_vehicle: {plane: 'high'}};
  assert.equal(validateLoadout(vehicle, basic, low, config).valid, true);
  assert.ok(validateLoadout(vehicle, advanced, low, config).errors.includes('loadout_forbidden_by_rating_configuration'));
  assert.equal(validateLoadout(vehicle, advanced, high, config).valid, true);
  assert.equal(validateRatingLoadout(vehicle, advanced, {}).valid, false);
});

test('CFG05: catalog and loadout policies fail closed when unverified', () => {
  const vehicle = variant();
  vehicle.rating_configurations.ground_rb.status = 'proposed';
  assert.equal(getBR(vehicle, 'current', {configuration_by_vehicle: {plane: 'low'}}), null);
  vehicle.rating_configurations.ground_rb.status = 'official_verified';
  vehicle.rating_configurations.ground_rb.options[0].allowed_loadout_ids = undefined;
  assert.ok(validateLoadout(vehicle, basic, {configuration_by_vehicle: {plane: 'low'}}, config)
    .errors.includes('configuration_loadout_policy_unverified'));
  vehicle.rating_configurations.ground_rb.options[1].id = 'low';
  assert.equal(resolveRatingConfiguration(vehicle, {configuration_id: 'low'}).status, 'invalid');
});

test('CFG06: strict ownership checks are separate from theoretical availability', () => {
  const vehicle = variant();
  const option = {configuration_id: 'high', require_unlocked_modifications: true};
  assert.equal(resolveRatingConfiguration(vehicle, option).br, null);
  assert.equal(resolveRatingConfiguration(vehicle,
    {...option, unlocked_modification_ids: []}).br, null);
  assert.equal(resolveRatingConfiguration(vehicle,
    {...option, unlocked_modification_ids: ['research-weapon']}).br, 11);
});

test('CFG07: profile enumeration produces candidates, never a silent selection', () => {
  const vehicle = variant();
  assert.deepEqual(enumerateRatingConfigurations(vehicle).map(v => [v.configuration_id,v.br]),
    [['low',10],['high',11]]);
  assert.deepEqual(enumerateRatingConfigurations(vehicle,
    {require_unlocked_modifications: true, unlocked_modification_ids: []})
    .map(v => v.configuration_id), ['low']);
  assert.equal(getBR(vehicle), null);
});

test('CFG08: selected high-tier aircraft raises the Ground RB lineup matchmaking environment', () => {
  const tank = ground('tank', 'usa', 10), jet = variant(), foe = ground('foe', 'ussr', 11.7);
  const all = [tank, jet, foe];
  const low = {configuration_by_vehicle: {plane: 'low'}};
  const high = {configuration_by_vehicle: {plane: 'high'}};
  const base = matchEnvironment(all, ['tank', 'plane'], 'current', config, low);
  const raised = matchEnvironment(all, ['tank', 'plane'], 'current', config, high);
  assert.equal(base.actual_br, 10);
  assert.equal(raised.actual_br, 11);
  assert.equal(base.opponent_ids.includes('foe'), false);
  assert.equal(raised.opponent_ids.includes('foe'), true);
  assert.equal(evaluateLineup(all, ['tank','plane'], low, config).valid, true);
  assert.equal(evaluateLineup(all, ['tank','plane'], {}, config).valid, false);
});

test('CFG09: unresolved enemy configurations are not silently removed from valid matchmaking', () => {
  const tank = ground('tank', 'usa', 10), enemy = variant('enemy','ussr');
  const unresolved = matchEnvironment([tank,enemy], ['tank'], 'current', config);
  assert.ok(unresolved.invalid.includes('opponent_rating_configuration_unresolved'));
  assert.deepEqual(unresolved.unresolved_opponent_configurations,['enemy']);
  const explicit = matchEnvironment([tank,enemy], ['tank'], 'current', config,
    {configuration_by_vehicle: {enemy: 'low'}});
  assert.equal(explicit.valid, true);
  assert.deepEqual(explicit.opponent_ids,['enemy']);
});

test('CFG10: a legacy single-value BR update cannot overwrite a multi-BR profile', () => {
  const vehicle = variant();
  const scenario = {entries: [{vehicle_id: vehicle.id, field: 'br', old: 99, new: 12}]};
  const output = applyScenario([vehicle], scenario);
  assert.equal(output.applied.length, 0);
  assert.equal(output.rejected[0].reason, 'configuration_specific_br_change_required');
  assert.equal(output.vehicles[0].rating_configurations.ground_rb.options[0].br.current, 10);
});

test('CFG11: recommendation search states that unselected variant search is unsupported', () => {
  const tank = ground('tank', 'usa', 10);
  const jet = variant();
  const result = recommendNation([tank,jet,ground('foe','ussr',10)], 'usa',
    {slots: 1}, config);
  assert.equal(result.search.automatic_configuration_search, false);
  assert.deepEqual(result.search.unselected_rating_configurations,['plane']);
  assert.equal(result.search.all_effective_caps_scanned, false);
});
