import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
import {evaluatePair} from '../shared/engine.mjs';
const read=async path=>JSON.parse(await fs.readFile(new URL('../'+path,import.meta.url),'utf8'));
const [catalog,config,layers]=await Promise.all([read('data/snapshots/catalog.json'),read('config/model.json'),read('data/snapshots/target-layers.json')]);
const a=catalog.vehicles.find(v=>v.id==='ussr_t_72b'),target=catalog.vehicles.find(v=>v.id==='ussr_t_90m_2020');
const probabilities={target_layers:layers};
const rows=a.ammo.map(ammo=>{
  const pair=evaluatePair(a,target,{ammo_id:ammo.id},config,probabilities),response=pair.components.armor_pressure.sampled;
  return {ammo_id:ammo.id,type:ammo.type,quality:pair.quality,pair_score:pair.score,penetration_mm:response?.penetration_mm??null,protected_projection_response:response?.value??null,mechanism:response?.mechanism??null};
});
const modeled=rows.filter(r=>r.quality==='sampled_layered_ammunition');
assert.ok(modeled.length>=2);assert.ok(new Set(modeled.map(r=>r.protected_projection_response)).size>1);
const selected=evaluatePair(a,target,{},config,probabilities);
assert.ok(a.ammo.some(ammo=>ammo.id===selected.ammo_id));
const report={passed:true,model:config.model_version,attacker:a.id,target:target.id,physical_distance_m:config.defaults.physical_distance_m,angle_deg:config.defaults.angle_deg,automatically_selected_ammo:selected.ammo_id,rows,scope:layers.scope};
await fs.writeFile(new URL('../reports/owned-ammunition-validation.json',import.meta.url),JSON.stringify(report,null,2));
console.log(JSON.stringify(report,null,2));
