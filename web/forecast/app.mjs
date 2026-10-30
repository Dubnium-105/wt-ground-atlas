const $ = id => document.getElementById(id);
const text = (tag, value, cls='') => {const e=document.createElement(tag);e.textContent=String(value??'—');e.className=cls;return e;};
const fmt = value => Number.isFinite(value)?value.toFixed(2):'资料不足';
async function read(descriptor) {
  const url=new URL(typeof descriptor==='string'?descriptor:descriptor.url,location.href);
  if(url.origin!==location.origin)throw new Error('拒绝跨源数据描述');
  const response=await fetch(url);if(!response.ok)throw new Error(`读取失败 ${response.status}`);
  const bytes=await response.arrayBuffer();
  if(descriptor.sha256){
    if(bytes.byteLength!==descriptor.bytes)throw new Error('数据长度不符');
    const hash=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(x=>x.toString(16).padStart(2,'0')).join('');
    if(hash!==descriptor.sha256)throw new Error('快照完整性校验失败');
  }
  return JSON.parse(new TextDecoder().decode(bytes));
}
function table(headers, rows) {
  const wrap=text('div','','scroll'),t=document.createElement('table'),head=document.createElement('tr');
  headers.forEach(v=>head.append(text('th',v)));t.append(head);
  rows.forEach(row=>{const tr=document.createElement('tr');row.forEach(v=>tr.append(text('td',v)));t.append(tr);});
  wrap.append(t);return wrap;
}
let index,report,worlds={},moves=[],sequence=0;
function select(id,values,selected){$(id).replaceChildren();values.forEach(([value,label])=>{const o=text('option',label);o.value=value;$(id).append(o);});if(values.some(([v])=>v===selected))$(id).value=selected;}
async function render(){
  const request=++sequence,version=$('version').value,world=worlds[version];
  const board=$('board').value,nation=$('nation').value,br=$('br').value;
  let rows=world.boards[board]||[];
  if(nation||br){
    const [slots,acquisition]=board.split('/');
    const files=world.tables.filter(t=>t.slots===Number(slots)&&t.acquisition===acquisition&&(!nation||t.nation===nation)&&(!br||String(t.br)===br));
    const data=await Promise.all(files.map(read));if(request!==sequence)return;
    rows=data.flatMap(t=>t.candidates).filter(r=>r.rankable&&report.published_rankings).sort((a,b)=>b.score-a.score);
  }
  $('rankings').replaceChildren(text('h2',version==='planned'?'调整后 · 条件强势候选':'当前快照 · 条件强势候选'));
  if(!rows.length)$('rankings').append(text('p','没有足够的双向交战证据形成这个范围的排名。请查看数据边界；不会把未知载具计为零分。','notice'));
  rows.forEach((r,i)=>{
    const card=text('article','','card');card.append(text('span',`#${i+1} · ${r.nation} · BR ${r.actual_br}`,'eyebrow'));
    card.append(text('h3',`${fmt(r.score)} / 100`),text('p',`缺项上下界 ${fmt(r.lower)}–${fmt(r.upper)} · 数据覆盖 ${fmt(r.evidence_coverage*100)}%`));
    const list=document.createElement('ul');r.members.forEach(m=>list.append(text('li',`${m.vehicle_id} · ${m.rating_configuration_id||'固定 BR'} · ${m.ammo_id||m.loadout_id||'挂载未知'}`)));card.append(list);
    card.append(text('p','评分对象为声明场景下的条件交战 / 出击效用，并非真实击毁率。','muted'));$('rankings').append(card);
  });
  const valid=moves.filter(m=>Number.isFinite(m.fixed_lineup_delta)).sort((a,b)=>b.fixed_lineup_delta-a.fixed_lineup_delta);
  $('movements').replaceChildren(text('h2','旧队列迁移与重新组队收益'));
  if(valid.length)$('movements').append(table(['国家','旧 BR → 新 BR','固定成员变化','同新版 BR 重组收益'],valid.map(m=>[m.nation,`${m.before.actual_br} → ${m.after_fixed.actual_br}`,fmt(m.fixed_lineup_delta),fmt(m.adaptation_gain_same_new_BR)])));
  else $('movements').append(text('p','尚无可验证的前后对照。没有调整方案时，不复制当前榜冒充下版本榜。'));
}
async function boot(){
  index=await read('index.json');report=await read(index.report);moves=await read(index.migrations);
  await Promise.all(Object.entries(index.worlds).map(async([k,d])=>worlds[k]=await read(d)));
  const versions=Object.keys(worlds);select('version',versions.map(v=>[v,v==='planned'?'调整后预测':'当前快照']),versions.includes('planned')?'planned':'current');
  const updateFilters=()=>{
    const w=worlds[$('version').value];
    select('board',Object.keys(w.boards).map(k=>[k,k.replace('/researchable',' 槽 · 常规研发').replace('/all',' 槽 · 包含特种')]),$('board').value);
    select('nation',[['','全部国家'],...[...new Set(w.tables.map(t=>t.nation))].sort().map(n=>[n,n])],$('nation').value);
    select('br',[['','全部 BR'],...[...new Set(w.tables.map(t=>t.br))].sort((a,b)=>a-b).map(b=>[String(b),String(b)])],$('br').value);
  };updateFilters();
  $('status').replaceChildren(text('strong',report.sample_subset?'样例诊断：不发布全服排名':'未校准条件模型：请结合数据覆盖阅读'),text('p',`${report.metadata_count} 组物理地图 · ${report.catalog_vehicles} 辆目录载具 · 原生物理距离 ${report.physical_distances_m.join(', ')} m · 调整状态 ${report.scenario_status}`));
  $('method').append(text('pre',JSON.stringify({endpoint:report.endpoint,scope:report.ranking_scope,source_bindings:report.source_bindings,population:report.population,matchmaking:report.matchmaking,spawn:report.spawn_assumptions,limitations:report.limitations,build_key:report.build_key},null,2)));
  $('diagnostics').append(table(['目标','物理角度','技能 s','单发条件效用','未知质量','无内部目标质量','落空质量'],report.diagnostics.map(d=>[d.target_id,d.yaw_deg,d.skill,fmt(d.mean),fmt(d.unknown_mass),fmt(d.no_protected_target_mass),fmt(d.miss_mass)])));
  $('version').onchange=()=>{updateFilters();render().catch(fail);};['board','nation','br'].forEach(id=>$(id).onchange=()=>render().catch(fail));
  await render();document.documentElement.dataset.ready='true';
}
function fail(error){$('status').replaceChildren(text('strong','预测数据载入失败'),text('p',error.message));console.error(error);}
boot().catch(fail);
