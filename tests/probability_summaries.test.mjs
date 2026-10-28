import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import zlib from 'node:zlib';
import {fileURLToPath} from 'node:url';
import {buildProbabilitySummaries} from '../scripts/build_probability_summaries.mjs';
import {aimContextKey,findExactProbability,evaluatePair} from '../shared/engine.mjs';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const config=JSON.parse(await fs.readFile(path.join(root,'config/model.json'),'utf8'));
const pipeline=JSON.parse(await fs.readFile(path.join(root,'config/pipeline.json'),'utf8'));
const digest=value=>crypto.createHash('sha256').update(value).digest('hex');

async function fixture() {
  const directory=await fs.mkdtemp(path.join(os.tmpdir(),'wt-summary-fixture-'));
  const width=3,height=2,n=width*height,q=pipeline.export.quantization_max,unknown=pipeline.export.unknown_value;
  const values=[0,q/4,unknown,unknown,unknown,q],states=[3,3,2,0,1,3];
  const buffer=Buffer.alloc(n*(Uint16Array.BYTES_PER_ELEMENT+Uint8Array.BYTES_PER_ELEMENT*2));
  values.forEach((value,index)=>buffer.writeUInt16LE(Math.round(value),index*Uint16Array.BYTES_PER_ELEMENT));
  Buffer.from(states).copy(buffer,n*Uint16Array.BYTES_PER_ELEMENT);
  const compressed=zlib.gzipSync(buffer);
  await fs.writeFile(path.join(directory,'sample.bin'),buffer);await fs.writeFile(path.join(directory,'sample.bin.gz'),compressed);
  const entry={id:'fixture-entry',target_id:'fixture_target',configuration_id:'fixture_configuration',ammo_id:'fixture_round',
    ammo_profile_sha256:digest('fixture-round-profile'),physical_distance_m:400,geometry_version:digest('fixture-geometry'),
    configuration_version:digest('fixture-materials'),model_version:'fixture-physics',source_version:'fixture-source',
    width,height,pixel_pitch_m:.04,angles_deg:[17],shards:[{angle_index:0,angle_deg:17,url:'sample.bin',gzip_url:'sample.bin.gz',
      sha256:digest(buffer),gzip_sha256:digest(compressed),bytes:buffer.length,gzip_bytes:compressed.length}]};
  const index={entries:[entry],source_version:'fixture-source',datamine_commit:'fixture-commit',encoding:{
    probability_dtype:'uint16-le',quantization_max:q,unknown_value:unknown,layout:['probability','status','material_response_flags']}};
  await fs.writeFile(path.join(directory,'index.json'),JSON.stringify(index));
  return {directory,entry,index,options:{index:path.join(directory,'index.json'),config:path.join(root,'config/model.json'),
    out:path.join(directory,'bundle.json'),report:path.join(directory,'report.json')}};
}

test('real binary integration preserves unknown, full impact mass and reference-only identity',async()=>{
  const f=await fixture();try{
    const report=await buildProbabilitySummaries(f.options),bundle=JSON.parse(await fs.readFile(f.options.out,'utf8'));
    assert.equal(report.records,1);assert.equal(report.integrated_samples,f.entry.width*f.entry.height);
    const record=bundle.records[0];assert.equal(record.angle_deg,f.entry.angles_deg[0]);
    assert.equal(record.physical_distance_m,f.entry.physical_distance_m);assert.equal(record.reference_only,true);
    assert.equal(record.proves_vehicle_ammunition_availability,false);assert.ok(record.unknown_mass>0);
    assert.ok(record.upper>record.lower);assert.ok(record.miss_mass>0);
    assert.ok(Math.abs(record.known_mass+record.unknown_mass+record.no_protected_target_mass+record.miss_mass-1)<1e-10);
    assert.equal(record.aim_context_key,aimContextKey(config.defaults,config));
  }finally{await fs.rm(f.directory,{recursive:true,force:true});}
});

test('only explicitly verified fixture with every real identity matches; skill/range/ownership changes reject',async()=>{
  const f=await fixture();try{
    await buildProbabilitySummaries(f.options);const bundle=JSON.parse(await fs.readFile(f.options.out,'utf8'));
    const reference=bundle.records[0];
    const ammo={id:reference.ammo_id,ammo_profile_sha256:reference.ammo_profile_sha256,
      penetration:[{distance_m:reference.physical_distance_m,angle_deg:reference.angle_deg,mm:150}]};
    const attacker={id:'fixture_attacker',kind:'ground',role:'mbt',ammo:[ammo]},target={id:reference.target_id,kind:'ground',role:'mbt',
      configuration_id:reference.configuration_id,geometry_version:reference.geometry_version,
      configuration_version:reference.configuration_version,source_version:reference.source_version};
    const context={...config.defaults,physical_distance_m:reference.physical_distance_m,angle_deg:reference.angle_deg};
    assert.equal(findExactProbability(attacker,target,ammo,context,config,bundle),null);
    const verified={...reference,reference_only:false,provenance:[{source:'explicit independent test fixture',verification:'all fixture identities matched'}]};
    const verifiedBundle={...bundle,records:[verified]};
    assert.equal(findExactProbability(attacker,target,ammo,context,config,verifiedBundle),verified);
    assert.equal(evaluatePair(attacker,target,context,config,verifiedBundle).quality,'conditional_layered_model');
    assert.equal(findExactProbability(attacker,target,ammo,{...context,skill:context.skill+config.ui.skill.step},config,verifiedBundle),null);
    assert.equal(findExactProbability(attacker,target,ammo,{...context,physical_distance_m:context.physical_distance_m+config.ui.distance_m.step},config,verifiedBundle),null);
    assert.equal(findExactProbability({...attacker,ammo:[]},target,ammo,context,config,verifiedBundle),null);
  }finally{await fs.rm(f.directory,{recursive:true,force:true});}
});

test('summaries reproduce exactly and validate compressed source hashes',async()=>{
  const f=await fixture();try{
    await buildProbabilitySummaries({...f.options,force:true});const original=await fs.readFile(f.options.out);
    await buildProbabilitySummaries({...f.options,force:true});assert.deepEqual(await fs.readFile(f.options.out),original);
    assert.equal((await buildProbabilitySummaries(f.options)).status,'unchanged');
    await fs.writeFile(path.join(f.directory,'sample.bin.gz'),Buffer.from('tampered'));
    await assert.rejects(buildProbabilitySummaries({...f.options,force:true}),/hash mismatch/);
    assert.deepEqual(await fs.readFile(f.options.out),original);
  }finally{await fs.rm(f.directory,{recursive:true,force:true});}
});

test('parallel target partitions are byte-identical to serial output',async()=>{
  const f=await fixture();try{
    f.index.entries.push({...f.entry,id:'second-entry',target_id:'second-target'});
    await fs.writeFile(f.options.index,JSON.stringify(f.index));
    await buildProbabilitySummaries({...f.options,force:true});const original=await fs.readFile(f.options.out);
    await buildProbabilitySummaries({...f.options,workers:2,parts:path.join(f.directory,'parts')});
    assert.deepEqual(await fs.readFile(f.options.out),original);
    f.index.entries.reverse();await fs.writeFile(f.options.index,JSON.stringify(f.index));
    await buildProbabilitySummaries({...f.options,force:true});const reversed=await fs.readFile(f.options.out);
    await buildProbabilitySummaries({...f.options,workers:2,parts:path.join(f.directory,'parts')});
    assert.deepEqual(await fs.readFile(f.options.out),reversed);
  }finally{await fs.rm(f.directory,{recursive:true,force:true});}
});
