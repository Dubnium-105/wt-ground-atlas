#!/usr/bin/env node
/** Integrate real exported maps into a compact decision-probability bundle.
 * Generated records are ALWAYS research/reference records. This command does
 * not infer ammunition availability or promote an unverified catalog binding.
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import zlib from 'node:zlib';
import {fileURLToPath} from 'node:url';
import {Worker, isMainThread, parentPort, workerData} from 'node:worker_threads';
import {aimContextKey, integrateAim, validateModelConfig} from '../shared/engine.mjs';
import {decodeProbability} from '../shared/probability-codec.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const finite = value => typeof value === 'number' && Number.isFinite(value);
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
const encode = value => Buffer.from(JSON.stringify(value, (_, item) => item && typeof item === 'object' && !Array.isArray(item)
  ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]])) : item));
const readJSON = async filename => JSON.parse(await fs.readFile(filename, 'utf8'));
const requiredIdentity = ['target_id','configuration_id','ammo_id','ammo_profile_sha256','physical_distance_m','geometry_version','configuration_version','model_version','source_version'];

function localShard(base, relative) {
  if (typeof relative !== 'string' || !relative) throw new Error('Missing local probability shard URL');
  const file = path.resolve(base, relative), rel = path.relative(base, file);
  if (rel.startsWith(`..${path.sep}`) || rel === '..' || path.isAbsolute(rel)) throw new Error('Probability shard escapes its atlas directory');
  return file;
}

async function readShard(base, shard) {
  let data;
  if (shard.gzip_url) {
    const compressed = await fs.readFile(localShard(base, shard.gzip_url));
    if (shard.gzip_sha256 && hash(compressed) !== shard.gzip_sha256) throw new Error(`Compressed probability hash mismatch: ${shard.gzip_url}`);
    data = zlib.gunzipSync(compressed);
  } else data = await fs.readFile(localShard(base, shard.url));
  if (!shard.sha256 || hash(data) !== shard.sha256) throw new Error(`Probability hash mismatch: ${shard.url}`);
  if (data.length !== shard.bytes) throw new Error(`Probability byte-count mismatch: ${shard.url}`);
  return data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength);
}

export async function buildProbabilitySummaries(options = {}) {
  if (Number(options.workers) > 1 && !options.partition) return buildParallelSummaries(options);
  const started = performance.now();
  const paths = {
    index: path.resolve(options.index ?? path.join(ROOT, 'data/exports/probability/index.json')),
    config: path.resolve(options.config ?? path.join(ROOT, 'config/model.json')),
    context: options.context ? path.resolve(options.context) : null,
    out: path.resolve(options.out ?? path.join(ROOT, 'data/snapshots/decision-probabilities.json')),
    report: path.resolve(options.report ?? path.join(ROOT, 'reports/probability-summaries.json')),
  };
  const indexBytes = await fs.readFile(paths.index), configBytes = await fs.readFile(paths.config);
  const index = JSON.parse(indexBytes), config = JSON.parse(configBytes);
  const validation = validateModelConfig(config);
  if (!validation.valid) throw new Error(`Invalid decision model: ${JSON.stringify(validation.errors)}`);
  const override = paths.context ? await readJSON(paths.context) : {};
  if (!override || Array.isArray(override) || typeof override !== 'object') throw new Error('Aim context must be a JSON object');
  const context = {...config.defaults, ...override};
  for (const key of ['skill','distance_m','own_motion_mps','target_motion_mps']) if (!finite(context[key])) throw new Error(`Missing/nonfinite aim context: ${key}`);
  const contextKey = aimContextKey(context, config);
  const provenance = {
    index_sha256: hash(indexBytes), config_sha256: hash(configBytes),
    engine_sha256: hash(await fs.readFile(path.join(ROOT, 'shared/engine.mjs'))),
    decoder_sha256: hash(await fs.readFile(path.join(ROOT, 'shared/probability-codec.mjs'))),
    summarizer_sha256: hash(await fs.readFile(fileURLToPath(import.meta.url))),
    source_version: index.source_version ?? null, datamine_commit: index.datamine_commit ?? null,
    aim_context_key: contextKey,
    integration_source: 'quantized_published_probability_shards',
    verification_policy: 'reference_only; no automatic ownership or catalog identity inference',
    game_probability_validated: false,
  };
  if (options.partition) provenance.partition = options.partition;
  const buildKey = hash(encode(provenance));
  if (!options.force) {
    try {
      const previous = await readJSON(paths.out), report = await readJSON(paths.report);
      const bytes = await fs.readFile(paths.out);
      if (previous.build_key === buildKey && report.bundle_sha256 === hash(bytes)) return {...report, status: 'unchanged', elapsed_ms: performance.now() - started};
    } catch {}
  }
  if (!Array.isArray(index.entries) || !index.entries.length) throw new Error('No probability entries');
  if (options.partition) index.entries = index.entries.filter((_, i) => i % options.partition.count === options.partition.index);
  const records = [], entries = [];
  const seenEntries = new Set(), seenRecords = new Set();
  let sampleCount = 0, readBytes = 0;
  for (const entry of index.entries) {
    if (!entry.id || seenEntries.has(entry.id)) throw new Error('Missing or duplicate probability entry ID');
    seenEntries.add(entry.id);
    const missing = requiredIdentity.filter(key => entry[key] === null || entry[key] === undefined);
    if (missing.length) throw new Error(`Incomplete physical identity ${entry.id}: ${missing.join(', ')}`);
    if (!Array.isArray(entry.angles_deg) || entry.angles_deg.length !== entry.shards?.length || new Set(entry.angles_deg).size !== entry.angles_deg.length) throw new Error('Angles / shards metadata mismatch');
    entries.push({id:entry.id,target_id:entry.target_id,ammo_id:entry.ammo_id,model_version:entry.model_version,
      source_version:entry.source_version,physical_distance_m:entry.physical_distance_m,width:entry.width,height:entry.height,
      pixel_pitch_m:entry.pixel_pitch_m,views:entry.shards.length,reference_only:true});
    for (const shard of entry.shards) {
      if (!Number.isInteger(shard.angle_index) || entry.angles_deg[shard.angle_index] !== shard.angle_deg) throw new Error('Shard angle/index mismatch');
      const bytes = await readShard(path.dirname(paths.index), shard);
      const grid = {...decodeProbability(bytes, entry, index.encoding),angle_deg:shard.angle_deg};
      const physicalContext = {...context, physical_distance_m:entry.physical_distance_m, angle_deg:shard.angle_deg};
      const result = integrateAim(grid, physicalContext, config);
      const record = {
        ...Object.fromEntries(requiredIdentity.map(key=>[key,entry[key]])),
        angle_deg:shard.angle_deg,aim_context_key:aimContextKey(physicalContext,config),
        mean:result.mean,lower:result.lower,upper:result.upper,coverage:result.coverage,
        known_mass:result.known_mass,unknown_mass:result.unknown_mass,
        no_protected_target_mass:result.no_protected_target_mass,miss_mass:result.miss_mass,
        reference_only:true,source_entry_id:entry.id,source_shard_sha256:shard.sha256,
        source_npz_sha256:entry.source_npz_sha256 ?? null,
        decision_model_version:config.model_version,calibration_status:config.calibration_status,
        probability_semantics:'expected_conditional_protected_target_penetration_utility',
        proves_vehicle_ammunition_availability:false,
      };
      for (const key of ['mean','lower','upper','coverage','known_mass','unknown_mass','miss_mass']) if (!finite(record[key]) || record[key]<0 || record[key]>1) throw new Error(`Invalid integrated ${key}`);
      if (record.lower > record.mean || record.mean > record.upper) throw new Error('Invalid probability bounds');
      record.id = hash(encode({source:entry.id,shard:shard.sha256,aim_context_key:record.aim_context_key}));
      if (seenRecords.has(record.id)) throw new Error('Duplicate probability summary record');
      seenRecords.add(record.id); records.push(record);
      sampleCount += entry.width * entry.height; readBytes += shard.gzip_bytes ?? shard.bytes;
    }
  }
  const models = [...new Set(records.map(record=>record.model_version))];
  if (models.length !== 1) throw new Error('Export each physical model version as a separate decision bundle');
  const bundle = {schema_version:'1.0.0',model_version:models[0],decision_model_version:config.model_version,
    build_key:buildKey,records,entries,provenance,
    calibrated_game_probability:false,
    reference_only_default:true,
    external_verified_records_contract:'docs/DECISION_PROBABILITIES.md'};
  const encoded = encode(bundle), compressed = zlib.gzipSync(encoded);
  await fs.mkdir(path.dirname(paths.out),{recursive:true});
  await fs.writeFile(paths.out,encoded);await fs.writeFile(`${paths.out}.gz`,compressed);
  const report = {schema_version:'1.0.0',build_key:buildKey,bundle_sha256:hash(encoded),
    entry_count:entries.length,records:records.length,reference_records:records.filter(r=>r.reference_only).length,
    verified_records:records.filter(r=>!r.reference_only).length,integrated_samples:sampleCount,
    source_shard_transfer_bytes:readBytes,bundle_bytes:encoded.length,gzip_bytes:compressed.length,
    source_index_sha256:provenance.index_sha256,physical_model_version:models[0],decision_model_version:config.model_version,
    all_shard_hashes_verified:true,model_physics_recalculated:false,aim_integration_computed:true,
    inferred_vehicle_ammunition_ownership:false};
  await fs.mkdir(path.dirname(paths.report),{recursive:true});await fs.writeFile(paths.report,encode(report));
  return {...report,status:'built',elapsed_ms:performance.now()-started};
}

async function buildParallelSummaries(options) {
  const started = performance.now();
  const indexFile = path.resolve(options.index ?? path.join(ROOT, 'data/exports/probability/index.json'));
  const index = await readJSON(indexFile);
  const count = Math.min(Number(options.workers), index.entries.length);
  if (!Number.isInteger(count) || count < 1) throw new Error('workers must be a positive integer');
  const out = path.resolve(options.out ?? path.join(ROOT, 'data/snapshots/decision-probabilities.json'));
  const reportFile = path.resolve(options.report ?? path.join(ROOT, 'reports/probability-summaries.json'));
  const parts = options.parts ?? path.join(ROOT, 'data/work/probability-summaries', hash(out));
  await fs.mkdir(parts, {recursive:true});
  const workers = [];
  let reports;
  try {
    reports = await Promise.all(Array.from({length:count}, (_, i) => new Promise((resolve, reject) => {
      const worker = new Worker(fileURLToPath(import.meta.url), {workerData:{...options,workers:1,
        partition:{index:i,count},out:path.join(parts,`${i}.json`),report:path.join(parts,`${i}.report.json`)}});
      workers.push(worker);
      worker.once('message', message => message.error ? reject(new Error(message.error)) : resolve(message.report));
      worker.once('error', reject);
      worker.once('exit', code => {if(code)reject(new Error(`Summary worker exited: ${code}`));});
    })));
  } catch (error) {
    await Promise.all(workers.map(worker => worker.terminate()));
    throw error;
  }
  const bundles = await Promise.all(Array.from({length:count},(_,i)=>readJSON(path.join(parts,`${i}.json`))));
  const bundle = {...bundles[0], provenance:{...bundles[0].provenance}};
  delete bundle.provenance.partition;
  bundle.build_key = hash(encode(bundle.provenance));
  const entryOrder = new Map(index.entries.map((entry,i)=>[entry.id,i]));
  const angleOrder = new Map(index.entries.flatMap(entry=>entry.angles_deg.map((angle,i)=>[`${entry.id}/${angle}`,i])));
  bundle.entries = bundles.flatMap(part=>part.entries).sort((a,b)=>entryOrder.get(a.id)-entryOrder.get(b.id));
  bundle.records = bundles.flatMap(part=>part.records).sort((a,b)=>entryOrder.get(a.source_entry_id)-entryOrder.get(b.source_entry_id)
    || angleOrder.get(`${a.source_entry_id}/${a.angle_deg}`)-angleOrder.get(`${b.source_entry_id}/${b.angle_deg}`));
  if(new Set(bundle.entries.map(entry=>entry.id)).size!==index.entries.length || new Set(bundle.records.map(record=>record.id)).size!==bundle.records.length) throw new Error('Incomplete or duplicate parallel summaries');
  const encoded = encode(bundle), compressed = zlib.gzipSync(encoded);
  await fs.mkdir(path.dirname(out),{recursive:true});
  await fs.writeFile(out,encoded);await fs.writeFile(`${out}.gz`,compressed);
  const report = {...reports[0],build_key:bundle.build_key,bundle_sha256:hash(encoded),entry_count:bundle.entries.length,
    records:bundle.records.length,reference_records:bundle.records.filter(r=>r.reference_only).length,
    verified_records:bundle.records.filter(r=>!r.reference_only).length,bundle_bytes:encoded.length,gzip_bytes:compressed.length,
    integrated_samples:reports.reduce((sum,r)=>sum+r.integrated_samples,0),
    source_shard_transfer_bytes:reports.reduce((sum,r)=>sum+r.source_shard_transfer_bytes,0)};
  delete report.elapsed_ms;delete report.status;
  await fs.mkdir(path.dirname(reportFile),{recursive:true});await fs.writeFile(reportFile,encode(report));
  return {...report,status:'built',elapsed_ms:performance.now()-started};
}

function parseArgs(argv) {
  const options={};
  for(let i=0;i<argv.length;i++){
    const arg=argv[i];
    if(arg==='--force'){options.force=true;continue;}
    if(!['--index','--config','--context','--out','--report','--workers'].includes(arg)||!argv[i+1])throw new Error('Usage: node scripts/build_probability_summaries.mjs [--index path] [--config path] [--context json] [--out path] [--report path] [--workers count] [--force]');
    options[arg.slice(2)]=argv[++i];
  }
  return options;
}
if (!isMainThread && workerData?.partition) {
  buildProbabilitySummaries(workerData).then(report=>parentPort.postMessage({report})).catch(error=>parentPort.postMessage({error:error.stack}));
} else if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  buildProbabilitySummaries(parseArgs(process.argv.slice(2))).then(report=>console.log(JSON.stringify(report))).catch(error=>{console.error(error.stack);process.exitCode=1;});
}
