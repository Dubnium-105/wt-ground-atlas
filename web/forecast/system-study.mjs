const $=id=>document.getElementById(id);
const node=(tag,text)=>{const e=document.createElement(tag);e.textContent=String(text);return e;};
const percent=x=>`${(x*100).toFixed(2)}%`;
let study,batch,loadSequence=0;
function options(id,values,label=x=>x){const select=$(id);select.replaceChildren();for(const value of values){const option=node('option',label(value));option.value=String(value);select.append(option);}}
function render(refreshScenario=true){
  const system=study.systems.find(x=>x.system_id===$('system').value);
  const matches=system.rows.filter(x=>x.distance_m===Number($('distance').value)&&x.yaw_deg===Number($('yaw').value));
  if(refreshScenario){const previous=$('scenario').value;options('scenario',matches.map(x=>x.scenario_id));if(matches.some(x=>x.scenario_id===previous))$('scenario').value=previous;}
  $('scenario-control').hidden=matches.length<=1;
  const row=matches.find(x=>x.scenario_id===$('scenario').value);
  const result=$('result');result.replaceChildren();
  if(!row){result.append(node('p','该情景没有研究结果。'));return;}
  result.append(node('h2',`目标选择未知：${percent(row.lower)}–${percent(row.upper)}`));
  result.append(node('p','区间描述选择不同成员时的条件穿透范围，不能解释为系统击毁率。'));
  const table=node('table',''),head=node('thead',''),tr=node('tr',''),labels=['成员','数量','条件穿透区间','数据'];table.className='study-table';
  for(const label of labels)tr.append(node('th',label));head.append(tr);table.append(head);
  const body=node('tbody','');for(const m of row.members){const r=node('tr','');
    for(const [i,value] of [m.name,m.count,`${percent(m.lower)}–${percent(m.upper)}`,m.status==='source_bound_map'?'已核验物理图':'未知'].entries()){const cell=node('td',value);cell.dataset.label=labels[i];r.append(cell);}body.append(r);}
  table.append(body);result.append(table);
  $('provenance').textContent=JSON.stringify({source_version:study.source_version,catalog_sha256:study.catalog_sha256,
    attacker_id:study.attacker_id,ammo_id:study.ammo_id,skill_profile:study.skill_profile,system_id:system.system_id,
    report_sha256:system.report_sha256,scenario_id:row.scenario_id,profile_id:row.profile_id,members:row.members},null,2);
}
async function verified(descriptor){
  const url=new URL(descriptor.url,location.href);if(url.origin!==location.origin||!url.pathname.startsWith(new URL('./',location.href).pathname))throw new Error('研究数据路径无效');
  const response=await fetch(url,{cache:'force-cache'});if(!response.ok)throw new Error('无法读取研究数据');const bytes=await response.arrayBuffer();
  const hash=async data=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',data))].map(x=>x.toString(16).padStart(2,'0')).join('');
  const actual=await hash(bytes);let decoded=bytes;
  if(descriptor.encoding==='gzip'&&bytes.byteLength===descriptor.decoded_bytes&&actual===descriptor.decoded_sha256){/* HTTP already decoded gzip. */}
  else{
    if(bytes.byteLength!==descriptor.bytes||actual!==descriptor.sha256)throw new Error('研究数据校验失败');
    if(descriptor.encoding==='gzip'){
      decoded=await new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer();
      if(decoded.byteLength!==descriptor.decoded_bytes||await hash(decoded)!==descriptor.decoded_sha256)throw new Error('研究数据解压校验失败');
    }
  }
  return JSON.parse(new TextDecoder().decode(decoded));
}
function showStudy(){
  $('study-status').textContent=`游戏版本 ${study.source_version} · ${study.attacker_name} · ${study.ammo_name} · ${study.skill_profile} 技能情景。研究数据独立于当前预测版本。`;
  options('system',study.systems.map(x=>x.system_id),id=>study.systems.find(x=>x.system_id===id).name);const rows=study.systems[0].rows;
  options('distance',[...new Set(rows.map(x=>x.distance_m))].sort((a,b)=>a-b),x=>`${x} 米`);
  options('yaw',[...new Set(rows.map(x=>x.yaw_deg))].sort((a,b)=>a-b),x=>`${x}°`);
  for(const id of ['system','distance','yaw'])$(id).onchange=()=>render(true);$('scenario').onchange=()=>render(false);render();document.documentElement.dataset.ready='true';
}
async function selectAmmo(){
  const choice=batch.choices.find(x=>x.attacker_id===$('attacker').value&&x.ammo_id===$('ammo').value);
  const sequence=++loadSequence;document.documentElement.dataset.ready='false';$('study-status').textContent='正在校验所选研究数据…';
  try{const loaded=await verified(choice.data);if(sequence!==loadSequence)return;study=loaded;showStudy();}
  catch(error){if(sequence===loadSequence){$('result').replaceChildren();$('study-status').textContent=`研究数据加载失败：${error.message}`;}}
}
function selectAttacker(){
  const choices=batch.choices.filter(x=>x.attacker_id===$('attacker').value);
  options('ammo',choices.map(x=>x.ammo_id),id=>choices.find(x=>x.ammo_id===id).ammo_name);selectAmmo();
}
async function boot(){
  const indexResponse=await fetch('index.json',{cache:'no-cache'});if(!indexResponse.ok)throw new Error('无法读取站点索引');
  const index=await indexResponse.json(),descriptor=index.system_study?.data;if(!descriptor)throw new Error('此站点没有组合多车系统研究数据');
  const loaded=await verified(descriptor);
  if(loaded.kind==='batch'){
    batch=loaded;if(!batch.choices?.length)throw new Error('批量研究为空');
    $('attacker-control').hidden=false;$('ammo-control').hidden=false;
    const attackers=[...new Set(batch.choices.map(x=>x.attacker_id))];
    options('attacker',attackers,id=>batch.choices.find(x=>x.attacker_id===id).attacker_name);
    $('attacker').onchange=selectAttacker;$('ammo').onchange=selectAmmo;selectAttacker();
  }else{study=loaded;showStudy();}
}
boot().catch(error=>{$('study-status').textContent=`研究数据加载失败：${error.message}`;});
