import {fetchJSON} from './data-client.mjs';
import {applyScenario,evaluateLineup,recommendNation,compareVersions,integrateAim,buildVersionOverview} from './shared/engine.mjs';

let catalogPromise;
let catalogURL;
async function getCatalog(manifest) {
  if(catalogURL!==manifest.engine_catalog.url){catalogURL=manifest.engine_catalog.url;catalogPromise=fetchJSON(manifest.engine_catalog);}
  return catalogPromise;
}
const probabilityBundles=new Map();
async function getProbabilities(manifest,context) {
  const angle=((context.angle_deg??0)%360+360)%360, layers=manifest.target_layers?.[angle];
  const key=JSON.stringify([manifest.decision_probabilities?.url,layers?.url]);
  if(!probabilityBundles.has(key))probabilityBundles.set(key,Promise.all([manifest.decision_probabilities?fetchJSON(manifest.decision_probabilities):{},layers?fetchJSON(layers):null]).then(([summaries,target_layers])=>({...summaries,target_layers})).catch(error=>{probabilityBundles.delete(key);throw error;}));
  return probabilityBundles.get(key);
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
