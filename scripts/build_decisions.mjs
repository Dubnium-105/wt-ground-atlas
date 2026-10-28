#!/usr/bin/env node
/** Offline, reproducible, country/version-sharded recommendation build. */
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import zlib from 'node:zlib';
import {fileURLToPath} from 'node:url';
import {applyScenario, recommendNation, getBR, evaluateLineup, validateModelConfig, buildVersionOverview} from '../shared/engine.mjs';

const args = Object.fromEntries(process.argv.slice(2).reduce((pairs, item, index, all) => item.startsWith('--') ? [...pairs, [item.slice(2), all[index + 1]?.startsWith('--') ? true : all[index + 1] ?? true]] : pairs, []));
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const paths = {
  catalog: path.resolve(args.catalog ?? path.join(root, 'data/snapshots/catalog.json')),
  scenario: path.resolve(args.scenario ?? path.join(root, 'data/snapshots/changes.json')),
  config: path.resolve(args.config ?? path.join(root, 'config/model.json')),
  probabilities: path.resolve(args.probabilities ?? path.join(root, 'data/snapshots/decision-probabilities.json')),
  layers: path.resolve(args['target-layers'] ?? path.join(root, 'data/snapshots/target-layers.json')),
  out: path.resolve(args.out ?? path.join(root, 'web/data/recommendations')),
};
const started = performance.now();
const [catalogText, scenarioText, configText] = await Promise.all([fs.readFile(paths.catalog, 'utf8'), fs.readFile(paths.scenario, 'utf8'), fs.readFile(paths.config, 'utf8')]);
const catalog = JSON.parse(catalogText), scenario = JSON.parse(scenarioText), config = JSON.parse(configText);
const validation = validateModelConfig(config);
if (!validation.valid) throw new Error(`Invalid model configuration: ${JSON.stringify(validation.errors)}`);
let probabilitiesText;
try {probabilitiesText = await fs.readFile(paths.probabilities, 'utf8');}
catch (error) {if (args.probabilities || error.code !== 'ENOENT') throw error; probabilitiesText = JSON.stringify({schema_version: '1.0.0', records: [], status: 'no_summary_bundle'});}
const layersText = await fs.readFile(paths.layers, 'utf8');
const probabilities = {...JSON.parse(probabilitiesText), target_layers: JSON.parse(layersText)};
// Offline recommendations use only the configured view; keep other views on disk.
for(const target of probabilities.target_layers.targets)target.views=target.views.filter(view=>view.angle_deg===((config.defaults.angle_deg%360)+360)%360);
if (!Array.isArray(probabilities.records)) throw new Error('Probability summary bundle requires a records array');
const digest = data => crypto.createHash('sha256').update(data).digest('hex');
const inputHash = digest(catalogText + scenarioText + configText + probabilitiesText + layersText);
const identity = {target_layers_sha256: digest(layersText), catalog_sha256: digest(catalogText), scenario_sha256: digest(scenarioText), config_sha256: digest(configText), probability_summaries_sha256: digest(probabilitiesText), engine_sha256: digest(await fs.readFile(path.join(root, 'shared/engine.mjs'))), snapshot_id: catalog.snapshot.id, model_version: config.model_version, requested_nations: typeof args.nations === 'string' ? args.nations.split(',').sort().join(',') : 'all'};
const buildKey = digest(JSON.stringify(identity));
await fs.mkdir(paths.out, {recursive: true});
let prior = null;
try {prior = JSON.parse(await fs.readFile(path.join(paths.out, 'index.json'), 'utf8'));} catch {}
if (!args.force && prior?.build_key === buildKey) {
  console.log(JSON.stringify({status: 'unchanged', build_key: buildKey, index: path.join(paths.out, 'index.json')}));
  process.exit(0);
}
const applied = applyScenario(catalog, scenario, 'planned');
const requestedNations = typeof args.nations === 'string' ? new Set(args.nations.split(',')) : null;
const nations = catalog.nations.filter(nation => !requestedNations || requestedNations.has(nation.id));
const index = {schema_version: '1.0.0', build_key: buildKey, input_hash: inputHash, ...identity, scenario_status: scenario.status, scenario_simulated: true, applied_change_count: applied.applied.length, rejected_change_count: applied.rejected.length, target_layer_count: probabilities.target_layers.targets.length, probability_summary_count: probabilities.records.length, reference_only_probability_count: probabilities.records.filter(record => record.reference_only !== false).length, files: [], impact: [], audit: applied.audit, version_labels: {current: '当前采集快照', planned: '调整后模拟方案'}, baseline_game_version: catalog.snapshot.game_version ?? null, calibration_status: config.calibration_status};
async function writeFile(relative, payload) {
  const text = JSON.stringify(payload), bytes = Buffer.from(text), hash = digest(bytes), filename = relative.replace('.json', `.${hash.slice(0, 16)}.json`);
  await fs.writeFile(path.join(paths.out, filename), bytes);
  const gzip = zlib.gzipSync(bytes);
  await fs.writeFile(path.join(paths.out, `${filename}.gz`), gzip);
  // Stable alias is useful for ordinary static hosting and incremental callers.
  await fs.writeFile(path.join(paths.out, relative), bytes);
  return {url: filename, alias: relative, sha256: hash, bytes: bytes.length, gzip_bytes: gzip.length};
}
for (const nation of nations) {
  const current = recommendNation(catalog.vehicles, nation.id, {version: 'current', includeMatrix: false}, config, probabilities);
  const planned = recommendNation(applied.vehicles, nation.id, {version: 'planned', includeMatrix: false}, config, probabilities);
  planned.version_overview=buildVersionOverview(catalog.vehicles,nation.id,scenario,{},config,probabilities,{current,planned});
  for (const result of [current, planned]) {
    const file = await writeFile(`${nation.id}.${result.version}.json`, {...result, build_key: buildKey, snapshot: catalog.snapshot, data_provenance: identity, simulated: result.version === 'planned', scenario_status: scenario.status});
    index.files.push({nation: nation.id, version: result.version, ...file, caps: result.search.br_caps_scanned, candidate_count: result.candidates.length});
  }
  const byCap = new Map(planned.candidates.map(candidate => [candidate.candidate_cap, candidate]));
  for (const candidate of current.candidates) {
    const next = byCap.get(candidate.candidate_cap), fixed = evaluateLineup(applied.vehicles, candidate.lineup_ids, {version: 'planned', includeMatrix: false}, config, probabilities);
    const oldEnv = new Set(catalog.vehicles.filter(v => v.nation !== nation.id && getBR(v, 'current') !== null && getBR(v, 'current') >= candidate.environment.minimum - config.matching.epsilon && getBR(v, 'current') <= candidate.environment.maximum + config.matching.epsilon).map(v => v.id));
    const newEnv = new Set(fixed.environment.opponent_ids);
    const members = applied.applied.filter(change => candidate.lineup_ids.includes(change.vehicle_id));
    index.impact.push({nation: nation.id, br: candidate.candidate_cap, previous_actual_br: candidate.actual_br, carried_actual_br: fixed.actual_br, previous_score: candidate.score, carried_score: fixed.score, optimized_score: next?.score ?? null, previous_lineup_ids: candidate.lineup_ids, optimized_lineup_ids: next?.lineup_ids ?? null, entered: [...newEnv].filter(id => !oldEnv.has(id)), left: [...oldEnv].filter(id => !newEnv.has(id)), member_changes: members, carried_air_window: fixed.metrics.air_window, previous_air_window: candidate.metrics.air_window, optimized_air_window: next?.metrics.air_window ?? null, simulated: true});
  }
  const previousCaps = new Set(current.candidates.map(candidate => candidate.candidate_cap));
  for (const candidate of planned.candidates.filter(candidate => !previousCaps.has(candidate.candidate_cap))) index.impact.push({nation: nation.id, br: candidate.candidate_cap, previous_actual_br: null, carried_actual_br: null, previous_score: null, carried_score: null, optimized_score: candidate.score, previous_lineup_ids: [], optimized_lineup_ids: candidate.lineup_ids, entered: [], left: [], member_changes: applied.applied.filter(change => candidate.lineup_ids.includes(change.vehicle_id)), carried_air_window: null, previous_air_window: null, optimized_air_window: candidate.metrics.air_window, newly_valid_cap: true, simulated: true});
  console.log(JSON.stringify({nation: nation.id, current_caps: current.candidates.length, planned_caps: planned.candidates.length, elapsed_s: (performance.now() - started) / 1000}));
}
await fs.writeFile(path.join(paths.out, 'index.json'), JSON.stringify(index));
// Only remove superseded generated recommendation shards in this output
// directory. Raw snapshots and previously archived builds remain untouched.
const published = new Set(index.files.flatMap(file => [file.url, `${file.url}.gz`, file.alias]));
for (const name of await fs.readdir(paths.out)) if (/^[a-z_]+\.(?:current|planned)\.[a-f0-9]+\.json(?:\.gz)?$/.test(name) && !published.has(name)) await fs.unlink(path.join(paths.out, name));
const report = {status: 'built', build_key: buildKey, elapsed_ms: performance.now() - started, nation_count: nations.length, file_count: index.files.length, uncompressed_bytes: index.files.reduce((sum, file) => sum + file.bytes, 0), gzip_bytes: index.files.reduce((sum, file) => sum + file.gzip_bytes, 0), version_environment_count: index.impact.length, deterministic_payloads: true, full_matrices_materialized: false, cache_invalidated_by: !prior ? ['initial_build'] : Object.keys(identity).filter(key => prior[key] !== identity[key]), prior_build_key: prior?.build_key ?? null};
await fs.writeFile(path.join(paths.out, 'build-report.json'), JSON.stringify(report, null, 2));
console.log(JSON.stringify(report));
