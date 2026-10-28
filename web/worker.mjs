import {createDataClient} from './data-client.mjs';
import {applyScenario,evaluateLineup,recommendNation,compareVersions,integrateAim,buildVersionOverview} from '../shared/engine.mjs';

// Catalog, summary and only the recently used target angles stay in memory.
// Failed requests leave no sticky rejected promise and can be retried.
const {fetchJSON}=createDataClient({maxEntries:4,maxBytes:32*1024*1024});
const getCatalog=manifest=>fetchJSON(manifest.engine_catalog);
async function getProbabilities(manifest,context) {
  const angle=((context.angle_deg??0)%360+360)%360, layers=manifest.target_layers?.[angle];
  const [summaries,target_layers]=await Promise.all([manifest.decision_probabilities?fetchJSON(manifest.decision_probabilities):{},layers?fetchJSON(layers):null]);
  return {...summaries,target_layers};
}

self.onmessage=async event=>{
  const {id,type,payload,manifest,context,scenario,config}=event.data;
  try{
    if(type==='aim') {
      const result=integrateAim(payload.grid,payload.context,config);
      self.postMessage({id,result});return;
    }
    const [baseline,probabilities]=await Promise.all([getCatalog(manifest),getProbabilities(manifest,context)]);
    const vehicles=context.version==='planned'?applyScenario(baseline,scenario,'planned').vehicles:baseline.vehicles;
    let result;
    if(type==='evaluate')result=evaluateLineup(vehicles,payload.lineup_ids,context,config,probabilities);
    else if(type==='recommend')result=recommendNation(vehicles,payload.nation,context,config,probabilities);
    else if(type==='version-overview')result=buildVersionOverview(baseline.vehicles,payload.nation,scenario,context,config,probabilities);
    else if(type==='compare')result=compareVersions(baseline.vehicles,payload.lineup_ids,scenario,context,config,probabilities);
    else throw new Error(`不支持的本地计算任务: ${type}`);
    self.postMessage({id,result});
  }catch(error){self.postMessage({id,error:error.message});}
};
