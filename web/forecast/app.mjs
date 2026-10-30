import {selectRows, tableRows, filterMovements} from './views.mjs';
const $=id=>document.getElementById(id);
const el=(tag,value='',cls='')=>{const e=document.createElement(tag);e.textContent=String(value??'—');e.className=cls;return e;};
const fmt=x=>Number.isFinite(x)?x.toFixed(2):'未知';
export const localeKeys={zh:{current:'当前快照',planned:'预测情景',missing:'暂无合格队列'},en:{current:'Current snapshot',planned:'Forecast scenario',missing:'No eligible lineups'}};
const reasons={snapshot_identity_missing:"目录快照或游戏版本身份不完整",snapshot_physics_version_mismatch:'目录版本与物理提交不一致',loadout_source_unverified:'挂载来源尚未核验',vehicle_spawn_provenance_incomplete:'逐车重生点来源不完整',rating_configuration_version_unverified:'BR 配置版本尚未核验',physical_inputs_missing_or_ambiguous:'缺少明确的同版本输入',synthetic_fixture_not_production:'合成测试数据，仅供验证程序',sample_subset:'只有样例子集',baseline_release_status_unverified:'尚未核验正式版本状态',source_version_timeline_incomplete:'来源提交或生效时间不完整',independent_target_geometry_not_verified:'目标几何来源尚未独立核验',physical_arrays_not_fully_verified:'物理数组尚未全部通过核验',search_budget_truncated:'搜索预算已耗尽',no_candidates_meet_bidirectional_coverage:'双向物理覆盖不足',scope_is_limited_ground_research:'当前范围限定地面与已提供的物理距离',full_mode_mission_evidence_missing:'缺少完整空地任务证据',required_tasks_not_modeled:'完整模式所需任务尚未进入实际模型',ranked_lineup_mission_evidence_incomplete:'入榜队列在当前任务与环境中的来源证据不足',spawn_rules_assumed:'重生点规则使用显式假设',sensitivity_not_verified:'尚未完成此输入的敏感性验证',damage_evidence_unvalidated:'穿后毁伤证据未核验',rating_version_provenance_incomplete:'BR 来源与游戏版本尚未完整绑定'};
let index,report,worlds={},moves=[],sequence=0,controller,page=0,movementPage=0;
const cache=new Map(),pageSize=12;
async function read(descriptor,signal){
  const name=typeof descriptor==='string'?descriptor:descriptor.url,url=new URL(name,location.href);
  if(url.origin!==location.origin||!url.pathname.startsWith(new URL('./',location.href).pathname))throw new Error('数据路径超出当前站点');
  const key=url.href+':'+(descriptor.sha256||'');if(cache.has(key))return cache.get(key);
  const response=await fetch(url,{signal,cache:descriptor.sha256?'force-cache':'no-cache'});if(!response.ok)throw new Error(`读取失败 (${response.status})`);
  const bytes=await response.arrayBuffer();
  if(descriptor.sha256){if(bytes.byteLength!==descriptor.bytes)throw new Error('数据长度不符');const hash=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(x=>x.toString(16).padStart(2,'0')).join('');if(hash!==descriptor.sha256)throw new Error('快照哈希校验失败');}
  const value=JSON.parse(new TextDecoder().decode(bytes));if(bytes.byteLength<2*1024*1024){if(cache.size>=16)cache.delete(cache.keys().next().value);cache.set(key,value);}return value;
}
function select(id,values,selected){$(id).replaceChildren();for(const [value,label]of values){const o=el('option',label);o.value=value;$(id).append(o);}if(values.some(([v])=>v===selected))$(id).value=selected;}
function table(headers,rows){const wrap=el('div','','scroll'),t=el('table'),thead=el('thead'),head=el('tr'),body=el('tbody');for(const h of headers){const th=el('th',h);th.scope='col';head.append(th);}thead.append(head);for(const row of rows){const tr=el('tr');for(const v of row)tr.append(el('td',v));body.append(tr);}t.append(thead,body);wrap.append(t);return wrap;}
function researchLink(member,view='vehicle'){const url=new URL(index.research?.url||'research/index.html',location.href);url.searchParams.set('nation',member.nation||$('nation').value);url.searchParams.set('br',member.br);url.searchParams.set('version',$('version').value);url.hash=`${view}/${encodeURIComponent(member.vehicle_id)}`;return url.href;}
function card(r,i){
  const article=el('article','','card'),head=el('div','','card-head');head.append(el('span',`#${i+1} · ${r.nation} · BR ${r.actual_br}`,'eyebrow'));const score=el('h3',fmt(r.score));score.append(el('small','条件效用 / 100','score-note'));head.append(score);article.append(head);
  const members=el('ul','','members');for(const m of r.members){const li=el('li'),a=el('a',m.name||m.vehicle_id);a.href=researchLink({...m,nation:r.nation});li.append(a,el('small',`BR ${m.br} · ${m.rating_configuration_id||'固定配置'}`),el('small',m.ammo_id||'无已核验弹药'),el('small',m.loadout_id?`挂载 ${m.loadout_id}：${(m.loadout?.weapons||[]).map(w=>`${w.id} × ${w.count}`).join('，')}`:'无航空挂载'));members.append(li);}article.append(members);
  article.append(el('p',`物理缺项区间 ${fmt(r.lower)}–${fmt(r.upper)}；证据覆盖 ${fmt(r.evidence_coverage*100)}%。模型误差与样本外置信区间：未知。`,'evidence'));const d=el('details');d.append(el('summary','出击策略、条件与来源'),el('pre',JSON.stringify({endpoint:r.endpoint,lineup_key:r.lineup_key,members:r.members,mission_coverage:r.mission_coverage,cases:r.cases,reserve_contributions:r.reserve_contributions,environment_threats:r.environment_threats,uncertainty:r.uncertainty,community:report.community?.annotations.filter(a=>r.members.some(m=>m.vehicle_id===a.vehicle_id))},null,2)));d.addEventListener('toggle',async()=>{if(!d.open||d.dataset.loaded||!r.detail)return;try{const t=await read(r.detail);const full=[...t.candidates,...Object.values(t.category_candidates||{}).flat()].find(c=>c.lineup_key===r.lineup_key);if(full)d.querySelector('pre').textContent=JSON.stringify(full,null,2);d.dataset.loaded='true';}catch(error){d.querySelector('pre').textContent=error.message;}});article.append(d);return article;
}
function coveragePanel(){const gate=report.release_readiness?.[$('version').value];$('coverage').replaceChildren();const ul=el('ul');for(const reason of gate?.why_not_publishable||[])ul.append(el('li',reasons[reason]||reason));$('coverage').append(ul);const counts=report.coverage?.counts;if(counts)$('coverage').append(el('p',`所需有向情景 ${counts.required_directed_pairs||0}，双向可计算 ${counts.available_bidirectional_pairs||0}；未报告的距离不会外推。`));const snapshot=report.snapshots?.[$('version').value];$('method').replaceChildren(el('pre',JSON.stringify({snapshot,endpoint:report.endpoint,scope:report.ranking_scope,calibration:report.calibration_status,source_bindings:report.source_bindings,population:report.population,matchmaking:report.matchmaking,spawn:report.spawn_assumptions,build_key:report.build_key},null,2)));}
function renderMovements(){
  const rows=filterMovements(moves,{nation:$('nation').value,br:$('br').value,board:$('board').value,version:$('version').value,direction:$('movement-direction').value});
  const target=$('movements');target.replaceChildren(el('h2',`版本变化与重组 (${rows.length})`));
  if(!rows.length){target.append(el('p','当前筛选下没有可核验的前后对照。当前榜不会复制为新版本榜。'));return;}
  movementPage=Math.min(movementPage,Math.max(0,Math.ceil(rows.length/pageSize)-1));
  for(const row of rows.slice(movementPage*pageSize,(movementPage+1)*pageSize)){
    const article=el('article','','card movement'),content=el('div','','movement-content');
    content.append(el('h3',`${row.nation} · ${row.slots} 槽 · ${row.acquisition==='all'?'全部合法':'常规研发'}`));
    const states=[['原版本队列',row.before],['新版本保留成员',row.after_fixed],['新版本同 BR 重组',row.after_optimized]];
    content.append(table(['对照','BR','条件效用','完整成员'],states.map(([name,r])=>[name,r?.actual_br??'—',fmt(r?.score),r?.members.map(m=>`${m.name||m.vehicle_id} · ${m.ammo_id||m.loadout_id||m.rating_configuration_id}`).join('；')||'不可用'])));
    content.append(el('p',`固定队列变化：${fmt(row.fixed_lineup_delta)}；同 BR 重组收益：${fmt(row.adaptation_gain_same_new_BR)}。`));
    if(row.status==='configuration_removed_or_illegal')content.append(el('p','原配置已移除或不再合法，不能按原成员迁移。'));
    if(row.status==='search_truncated')content.append(el('p','原版本搜索已截断，不将该结果作为已核验的最优队列变化。'));
    const detail=el('details');detail.append(el('summary','改动来源、房间变化与固定成员核对'),el('pre',JSON.stringify(row.attribution||{status:row.status},null,2)));content.append(detail);article.append(content);target.append(article);
  }
  if(rows.length>pageSize){const pager=el('div','','pager');for(const [label,delta] of [['上一组变化',-1],['下一组变化',1]]){const b=el('button',label);b.disabled=movementPage+delta<0||(movementPage+delta)*pageSize>=rows.length;b.onclick=()=>{movementPage+=delta;renderMovements();};pager.append(b);}pager.append(el('span',`${movementPage+1} / ${Math.ceil(rows.length/pageSize)}`));target.append(pager);}
}
function statusPanel(){
  $('status').className=report.published_rankings?'':'blocked';$('status').replaceChildren(el('strong',report.synthetic_fixture?'合成世界 · 算法验证':report.sample_subset?'样例诊断 · 正式榜未开放':report.published_rankings?'条件预测已通过发布检查':'资料诊断与限定研究 · 正式榜未开放'),el('p',`${report.catalog_vehicles} 辆目录载具，${report.metadata_count} 组可用物理地图。原生距离：${report.physical_distances_m.join(', ')||'未提供'} m。模型：${report.calibration_status||'uncalibrated'}。`));const snap=report.snapshots?.[$('version').value];$('status').append(el('p',`物理客户端版本：${report.client_versions?.join(', ')||snap?.game_version||'未核验'}；目录采集：${snap?.wiki_collected_at||'未提供'}；截断搜索表：${worlds[$('version').value].truncated_searches||0}。`));
}
async function render(){
  const request=++sequence;controller?.abort();controller=new AbortController();const signal=controller.signal;$('rankings').setAttribute('aria-busy','true');const version=$('version').value,world=worlds[version],board=$('board').value,nation=$('nation').value,br=$('br').value;const gate=report.release_readiness?.[version],formal=gate?.publishable===true;let rows=(formal?world.boards:world.research_boards)?.[board]||[];
  const view=$('view').value;
  if(nation||br||(!world.views?.[view]?.[board]&&view!=='global')){
    const [slots,acquisition]=board.split('/');
    const files=world.tables.filter(t=>t.slots===Number(slots)&&t.acquisition===acquisition&&(!nation||t.nation===nation)&&(!br||String(t.br)===br));
    const data=[];
    for(let i=0;i<files.length;i+=4)data.push(...await Promise.all(files.slice(i,i+4).map(d=>read(d,signal))));
    if(request!==sequence)return;
    rows=data.flatMap(t=>tableRows(t,view));
  }else if(view!=='global'){
    rows=await read(world.views[view][board],signal);
    if(request!==sequence)return;
  }
  rows=selectRows(formal||report.research_rankings?rows:[],view);
  $('rankings').replaceChildren(el('h2',`${version==='current'?'当前':'预测情景'} · ${formal?'合格候选':'限定研究候选'} (${rows.length})`));if(!rows.length){const box=el('div','','notice');box.append(el('strong','当前没有满足条件的队列'),el('p','请查看覆盖缺口与版本状态。资料不足时保留空榜，不用未知数据凑满 Top 100。'));$('rankings').append(box);}page=Math.min(page,Math.max(0,Math.ceil(rows.length/pageSize)-1));rows.slice(page*pageSize,(page+1)*pageSize).forEach((r,i)=>$('rankings').append(card(r,page*pageSize+i)));
  if(rows.length>pageSize){const pager=el('div','','pager');for(const [label,delta]of [['上一页',-1],['下一页',1]]){const b=el('button',label);b.disabled=page+delta<0||(page+delta)*pageSize>=rows.length;b.onclick=()=>{page+=delta;render().catch(fail);};pager.append(b);}pager.append(el('span',`${page+1} / ${Math.ceil(rows.length/pageSize)}`));$('rankings').append(pager);}
  renderMovements();statusPanel();coveragePanel();$('rankings').setAttribute('aria-busy','false');
}
async function boot(){
  index=await read('index.json');report=await read(index.report);moves=await read(index.migrations);worlds={};await Promise.all(Object.entries(index.worlds).map(async([k,d])=>worlds[k]=await read(d)));select('version',Object.keys(worlds).map(v=>[v,localeKeys.zh[v]||v]),worlds.planned?'planned':'current');
  const update=()=>{const w=worlds[$('version').value];select('board',Object.keys(w.boards).map(k=>[k,k.replace('/researchable',' 槽 · 常规研发').replace('/all',' 槽 · 全部合法')]),$('board').value);select('nation',[['','全部国家'],...[...new Set(w.tables.map(t=>t.nation))].sort().map(n=>[n,n])],$('nation').value);select('br',[['','全部 BR'],...[...new Set(w.tables.map(t=>t.br))].sort((a,b)=>a-b).map(b=>[String(b),String(b)])],$('br').value);};update();
  const cohorts=index.cohorts||[{skill:report.skill_profile||'normal',preference:report.task_preference||'ground',url:'index.html'}];
  select('skill',[...new Set(cohorts.map(c=>c.skill))].map(v=>[v,({novice:'新手先验',normal:'普通先验',expert:'高手先验'})[v]]),report.skill_profile||'normal');select('preference',[...new Set(cohorts.map(c=>c.preference))].map(v=>[v,({ground:'纯地面',balanced:'空地协同',air_support:'航空支援'})[v]]),report.task_preference||'ground');
  const switchProfile=()=>{const choice=cohorts.find(c=>c.skill===$('skill').value&&c.preference===$('preference').value);if(!choice)return;const url=new URL(choice.url,location.href);if(url.origin!==location.origin)throw new Error('非法情景路径');for(const id of ['version','board','nation','br','view'])url.searchParams.set(id,$(id).value);location.assign(url);};$('skill').onchange=switchProfile;$('preference').onchange=switchProfile;
  const route=new URLSearchParams(location.search);if(route.has('version')&&worlds[route.get('version')]){$('version').value=route.get('version');update();}for(const id of ['board','nation','br','view'])if([...$(id).options].some(o=>o.value===route.get(id)))$(id).value=route.get(id);
  for(const link of $('research-nav').querySelectorAll('a')){const destination=new URL(index.research?.url||'research/index.html',location.href);destination.hash=new URL(link.href).hash;link.href=destination.href;}
  if(index.research?.status!=='available')$('research-nav').title='研究快照尚未组合到此构建';$('diagnostics').replaceChildren(table(['目标','角度','技能','条件穿透','未知质量','落空质量'],(report.diagnostics||[]).slice(0,240).map(d=>[d.target_id,d.yaw_deg,d.skill,fmt(d.mean),fmt(d.unknown_mass),fmt(d.miss_mass)])));
  $('version').onchange=()=>{page=0;movementPage=0;update();render().catch(fail);};for(const id of ['board','nation','br','view'])$(id).onchange=()=>{page=0;movementPage=0;render().catch(fail);};$('movement-direction').onchange=()=>{movementPage=0;renderMovements();};await render();document.documentElement.dataset.ready='true';
}
function fail(error){if(error.name==='AbortError')return;const retry=el('button','重试加载');retry.onclick=()=>{cache.clear();boot().catch(fail);};$('status').replaceChildren(el('strong','预测数据加载失败'),el('p',error.message),retry);$('rankings').setAttribute('aria-busy','false');}
boot().catch(fail);
