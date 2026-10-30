import {fetchJSON, fetchBytes, decodeProbability, probabilityBaseURL} from './data-client.mjs';
import {topLineups} from '../shared/engine.mjs';

const $ = (query, root = document) => root.querySelector(query);
const $$ = (query, root = document) => [...root.querySelectorAll(query)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const finite = value => typeof value === 'number' && Number.isFinite(value);
const safeURL = value => {try {const url = new URL(value, location.href); return ['http:','https:'].includes(url.protocol) ? url.href : '';} catch {return '';}};
const unique = list => [...new Set(list)];
const byId = id => state.vehicles.get(id);
const get = (obj, key) => key.split('.').reduce((value, part) => value?.[part], obj);
const readSaved = () => {try {return JSON.parse(localStorage.getItem('groundops.preferences') || '{}');} catch {return {};}};
const saved = readSaved();
const state = {
  manifest:null, config:null, ui:null, catalog:null, scenario:null, sourceScenario:null, audit:null,
  vehicles:new Map(), baseVehicles:new Map(), nationData:new Map(), recommendations:new Map(), versionOverviews:new Map(), probabilityIndex:null,
  nation:saved.nation, br:saved.br, version:saved.version, view:null, context:{}, lineups:saved.lineups || {},
  owned:saved.owned || {}, query:'', kind:'all', treeChanged:false, treeMatching:false, treePage:0,
  viewToken:0, result:null, comparison:null, busy:false, worker:null, pending:new Map(), jobCounter:0,
  armorEntry:null, armorAngle:null, armorGrid:null, armorAim:null, armorSkill:null, armorDistance:null, armorError:null, armorRequest:null,
  matrixFold:true, matrixTarget:null, matrixPage:0, scenarioCustom:false, impact:null, analysisGeneration:0, changeScope:'nation', changeFilter:'all', changePage:0
};

const icons = {
  overview:'M3 3h7v7H3zm11 0h7v7h-7zM3 14h7v7H3zm11 0h7v7h-7z',
  versions:'M5 4v16m14-16v16M5 8h8l6 5M5 16h8l6-5M2 4h6m8 16h6',
  tree:'M12 3v7M5 10h14M5 10v7m14-7v7M9 3h6v4H9zM2 17h6v4H2zm14 0h6v4h-6z',
  lineup:'M3 5h18M3 12h18M3 19h18M7 3v4m10 3v4m-6 3v4',
  matrix:'M3 3h18v18H3zM3 9h18M3 15h18M9 3v18m6-18v18',
  armor:'m12 3 9 4v6c0 5-9 9-9 9S3 18 3 13V7zM8 12h8m-4-4v8',
  audit:'M5 3h10l4 4v14H5zM14 3v5h5M8 12h8m-8 4h8',
  arrow:'M4 12h16m-6-6 6 6-6 6',
  refresh:'M20 7v5h-5M4 17v-5h5M6 7a7 7 0 0 1 12-2l2 3M4 16l2 3a7 7 0 0 0 12-2',
  plus:'M12 4v16M4 12h16',
  download:'M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5'
};
const views = [
  ['overview','队列推荐'],['versions','版本变化'],['lineup','自定义队列'],
  ['tree','载具图鉴'],['matrix','逐车对抗'],['armor','防护研究'],['audit','数据与方法']
];
const icon = name => `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${icons[name] || icons.overview}"/></svg>`;
const fmt = (value, digits = state.ui?.display_digits) => finite(value) ? value.toLocaleString('zh-CN', {maximumFractionDigits:digits}) : '—';
const pct = value => finite(value) ? `${fmt(value * 100)}%` : '—';
const indexScore = value => finite(value) ? fmt(value * 100) : '—';
const nameOf = id => byId(id)?.name || id || '未知载具';
const nationName = nation => state.ui.nation_labels[nation] || state.catalog.nations.find(x => x.id === nation)?.label || nation;
const roleName = role => state.ui.role_labels[role] || role || '任务未分类';
const kindName = kind => state.ui.kind_labels[kind] || kind;
const activeBR = vehicle => Object.prototype.hasOwnProperty.call(vehicle?.br||{},state.version)?vehicle.br[state.version]:vehicle?.br?.current;
const versionName = () => state.version === 'current' ? '当前版本' : '调整后 · 模拟';
const chosenIds = () => (state.lineups[state.nation] || []).filter(id => byId(id));
const recommendationOptions = context => Object.fromEntries(['slots','skill','distance_m','physical_distance_m','angle_deg','includeSpecial','availableIds','available_spawn_points','target_radar_emitting','ammo_by_vehicle','loadout_by_vehicle','own_motion_mps','target_motion_mps','gun_control_deg_s'].map(key=>[key,context[key]??null]));
const recommendationKey = (version=state.version) => JSON.stringify([version,state.nation,recommendationOptions(state.context),state.scenarioCustom?state.scenario.entries:state.manifest.scenario.sha256]);
const defaultRecommendationConditions = () => JSON.stringify(recommendationOptions(state.context))===JSON.stringify(recommendationOptions(state.config.defaults));
const currentRecs = () => state.recommendations.get(recommendationKey());
const candidates = () => currentRecs()?.candidates || [];
function selectedCandidate() {
  const list = candidates();
  const exact = list.filter(x => x.br === state.br || x.actual_br === state.br);
  return (exact.length ? exact : list).slice().sort((a,b) => (finite(b.score) ? b.score : -Infinity) - (finite(a.score) ? a.score : -Infinity))[0];
}
const empty = (title, description, action = '') => `<div class="empty"><div class="empty-icon">◇</div><strong>${esc(title)}</strong>${esc(description)}${action}</div>`;
const busy = text => `<div class="busy"><span class="loader"></span>${esc(text)}</div>`;
const notice = (text, style = '', action = '') => `<div class="notice ${style}"><span aria-hidden="true">${style === 'warning' ? '△' : '◈'}</span><div>${text}</div>${action}</div>`;
const button = (action, label, style = '', symbol = '') => `<button class="button ${style}" data-action="${action}">${symbol ? icon(symbol) : ''}${label}</button>`;
const heading = (eyebrow, title, lead, actions = '') => `<div class="page-head"><div><h1>${title}</h1><p class="lead">${lead}</p></div><div class="page-head-actions">${actions}</div></div>`;
const section = (number, title, sub = '', actions = '') => `<div class="section-row"><div class="section-heading"><h2>${title}</h2>${sub ? `<span class="section-sub">${sub}</span>` : ''}</div>${actions}</div>`;
const tag = (text, style = '') => `<span class="tag ${style}">${esc(text)}</span>`;
function save() {
  try {localStorage.setItem('groundops.preferences', JSON.stringify({nation:state.nation,br:state.br,version:state.version,lineups:state.lineups,owned:state.owned}));}
  catch {toast('浏览器未允许保存偏好；本次分析仍然可用。');}
}
function invalidateAnalysis() {state.analysisGeneration++;state.result=null;state.comparison=null;}
function toast(message) {
  const element = $('#toast'); element.textContent = message; element.classList.add('visible');
  clearTimeout(toast.timer); toast.timer = setTimeout(() => element.classList.remove('visible'), state.ui?.toast_duration_ms);
}
function explain(items, fallback = '该项尚无足够的可追溯数据。') {
  const values = (items || []).map(item => typeof item === 'string' ? item : item.message || item.description || item.text || JSON.stringify(item));
  return values.length ? `<ul class="explanation-list">${values.map(item => `<li>${esc(item)}</li>`).join('')}</ul>` : `<div class="thin-empty">${esc(fallback)}</div>`;
}
function metricsView(metrics, mode) {
  return `<div class="metrics">${Object.entries(state.ui.metric_labels).filter(([key])=>mode!=='compact'||Object.hasOwn(metrics||{},key)).map(([key,label]) => {
    const value = metrics?.[key];
    return `<div class="metric-row"><span>${label}</span><div class="metric-track"><span style="width:${finite(value) ? Math.max(0,Math.min(1,value))*100 : 0}%"></span></div><strong>${indexScore(value)}</strong></div>`;
  }).join('')}</div>`;
}
function sourceNote() {
  return `<p class="model-note">${state.version==='planned'?'当前为调整后模拟方案。':''}模型指数用于条件比较，不代表游戏胜率。<a href="#audit">查看数据与方法</a></p>`;
}
function silhouette(kind = 'ground') {
  const path = kind === 'ground' ? 'M15 49h126l8 15-10 12H18L8 63Zm16-14h70l16 14H22Zm37-16h29l13 16H52Zm34 4h52v6h-52M26 58a6 6 0 1 0 0 12 6 6 0 0 0 0-12m23 0a6 6 0 1 0 0 12 6 6 0 0 0 0-12m23 0a6 6 0 1 0 0 12 6 6 0 0 0 0-12m23 0a6 6 0 1 0 0 12 6 6 0 0 0 0-12m23 0a6 6 0 1 0 0 12 6 6 0 0 0 0-12' : kind === 'helicopter' ? 'M17 43h83l28 12-8 12H27L17 54Zm43-20v20M17 23h112M96 48l48-14v21l-21 3M31 67v9h74M47 67v9M12 13v20' : 'M78 9h7l6 25 60 27v7L91 53l-4 21 18 9v4l-24-5-24 5v-4l18-9-4-21-60 15v-7l60-27Z';
  return `<svg class="silhouette" viewBox="0 0 160 95" aria-hidden="true"><path d="${path}" fill="currentColor" fill-opacity=".18" stroke="currentColor" stroke-width="1.5"/></svg>`;
}
function vehicleArt(vehicle, extras = '') {
  const url = safeURL(vehicle?.image_url);
  return `<div class="vehicle-art">${url ? `<img src="${esc(url)}" alt="" data-kind="${esc(vehicle?.kind||'ground')}" loading="lazy" decoding="async" referrerpolicy="no-referrer">` : silhouette(vehicle?.kind)}${extras}</div>`;
}
function vehicleCard(id, {remove=false,ammo=null,tree=false}={}) {
  const v = byId(id); if (!v) return '';
  const old = v.br?.current, next = v.br?.planned, changed = finite(old) && finite(next) && old !== next;
  const extra = `<span class="top-tag">${fmt(activeBR(v))}</span>${remove ? `<button class="icon-button vehicle-remove" data-remove="${esc(v.id)}" aria-label="移除 ${esc(v.name)}">×</button>` : ''}${changed && !remove ? `<span class="changed-ribbon">${fmt(old)} → ${fmt(next)}</span>` : ''}`;
  return `<article class="vehicle-card" data-vehicle="${esc(v.id)}" tabindex="0" role="button" aria-label="查看 ${esc(v.name)} 的档案">${vehicleArt(v, extra)}<div class="vehicle-caption"><div class="vehicle-name">${esc(v.name)}</div><div class="vehicle-role"><span>${esc(roleName(v.role))}</span><span>${v.special ? '特种' : '研发'}</span></div>${ammo ? `<div class="vehicle-ammo">${esc(typeof ammo === 'string' ? (v.ammo?.find(a=>a.id===ammo)?.name||ammo.split(/#|::/).at(-1)) : ammo.name || ammo.id)}</div>` : ''}</div>${tree && v.predecessors?.length ? `<div class="predecessor" title="${esc(v.predecessors.map(nameOf).join(' / '))}">↳ ${esc(v.predecessors.map(nameOf).join(' / '))}</div>` : ''}${tree && v.folder ? `<div class="predecessor">▣ 文件夹 · ${esc(nameOf(v.folder_members?.[0]) || v.folder)}</div>` : ''}</article>`;
}
function lineupCards(ids, candidate, removable=false) {
  return ids?.length ? `<div class="lineup-grid">${ids.map(id => vehicleCard(id,{remove:removable,ammo:candidate?.ammo_choices?.[id]})).join('')}</div>` : empty('尚未选择队列','采用一个推荐，或在我的队列手动加入载具。',button('auto-lineup','采用本档推荐','small'));
}
function candidateCards(list) {
  if (!list?.length) return empty('暂无满足条件的推荐','该国家当前可用资料或你的编队限制不足。可以调整槽位、BR 和可用载具。');
  return `<div class="candidate-list">${list.map((candidate,i) => `<article class="candidate-card ${candidate.br === state.br ? 'selected' : ''}"><div class="candidate-br"><small>实际 BR</small>${fmt(candidate.actual_br ?? candidate.br)}</div><div><div class="candidate-name">${esc(candidate.profile === 'ground' ? '地面稳健组合' : candidate.profile === 'air_support' ? '航空支援组合' : '综合任务组合')} ${i === 0 ? tag('备选') : ''}</div><div class="candidate-vehicles">${esc((candidate.lineup_ids || []).map(nameOf).join(' · '))}</div><div class="candidate-meta"><span>资料覆盖 ${pct(candidate.coverage)}</span><span>不利情景 ${indexScore(candidate.metrics?.worst_case)}</span></div></div><div class="candidate-score">${indexScore(candidate.score)}<small>模型指数</small><button class="button small ghost" data-candidate="${esc(JSON.stringify(candidate.lineup_ids || []))}" data-cap="${esc(candidate.actual_br ?? candidate.br)}">采用队列</button></div></article>`).join('')}</div>`;
}
function threatsView(threats, limit=state.ui.overview_threat_limit) {
  if (!threats?.length) return `<div class="thin-empty">尚无足够配对资料形成威胁排序。可查看匹配范围内的全部对手。</div>`;
  return threats.slice(0,limit).map((threat,i) => {
    const v=byId(threat.vehicle_id), cat=threat.category, score=threat.score ?? threat.enemy_best;
    return `<div class="threat-row"><span class="threat-rank">${String(i+1).padStart(2,'0')}</span><div class="threat-main"><button data-vehicle="${esc(threat.vehicle_id)}">${esc(nameOf(threat.vehicle_id))}</button><small>${esc(nationName(v?.nation || ''))} · BR ${fmt(activeBR(v))} · ${esc(roleName(v?.role))}</small></div><div class="threat-value">${tag(cat === 'high' ? '高威胁' : cat === 'medium' ? '需关注' : finite(score) ? '相对可应对' : '未知',cat === 'high' ? 'red' : cat === 'medium' ? 'warn' : 'neutral')}<small class="muted" style="display:block;margin-top:4px">${indexScore(score)}</small></div></div>`;
  }).join('');
}

function nationalRecommendations() {
  const rows=(state.manifest.national_recommendations||[]).filter(row=>row.version===state.version);
  if(state.scenarioCustom&&state.version==='planned')return notice('当前为本地自定义方案；全国对比图保留在原始离线构建中。当前国家可使用“重新扫描本国全 BR”计算本地方案结果。','warning');
  return `${!defaultRecommendationConditions()?notice('此表为离线默认条件的各国结果。当前自选条件仅作用于本国扫描与手动队列。','warning'):''}<section class="panel"><div class="table-wrap"><table class="data-table"><thead><tr><th>国家</th><th>推荐 BR</th><th>模型指数 / 区间</th><th>资料覆盖</th><th>已扫描档位</th><th>具体队列</th></tr></thead><tbody>${rows.map(row=>`<tr><td><button data-nation="${esc(row.nation)}" data-nation-br="${esc(row.best?.actual_br)}">${esc(nationName(row.nation))} ↗</button></td><td class="mono">${fmt(row.best?.actual_br)}</td><td class="mono">${indexScore(row.best?.score)} <small class="muted">${indexScore(row.best?.lower)}–${indexScore(row.best?.upper)}</small></td><td>${pct(row.best?.coverage)}</td><td class="mono">${row.br_caps}</td><td class="wrap">${esc((row.best?.lineup_ids||[]).map(nameOf).join(' · '))}</td></tr>`).join('')}</tbody></table></div></section>`;
}

function impactMap() {
  if(state.scenarioCustom)return notice('以下离线全国影响图对应采集的原始调整方案。当前本地方案请查看上方原队列推演和本国重新扫描结果。','warning');
  const rows=state.impact?.impact;if(!rows)return busy('按需载入各国 / BR 版本影响分片…');
  const caps=unique(rows.map(row=>row.br)).sort((a,b)=>a-b);
  const rowMap=new Map();for(const row of rows){const key=`${row.nation}/${row.br}`,prior=rowMap.get(key);if(!prior||(row.previous_score??-Infinity)>(prior.previous_score??-Infinity))rowMap.set(key,row);}
  return `<section class="panel"><div class="table-wrap"><table class="data-table matrix-table"><thead><tr><th>固定旧队列 Δ 指数</th>${caps.map(br=>`<th>${fmt(br)}</th>`).join('')}</tr></thead><tbody>${state.catalog.nations.map(n=>`<tr><td>${esc(nationName(n.id))}</td>${caps.map(br=>{const row=rowMap.get(`${n.id}/${br}`),delta=finite(row?.carried_score)&&finite(row?.previous_score)?row.carried_score-row.previous_score:null;return `<td class="matrix-cell"><button class="matrix-value ${!finite(delta)?'unknown':delta<0?'risk':''}" data-impact="${esc(`${n.id}/${br}`)}" title="${row?`原队列 ${fmt(row.previous_actual_br)} → ${fmt(row.carried_actual_br)} BR；对手进入 ${row.entered.length}，离开 ${row.left.length}`:'该档无合法地面队列'}">${finite(delta)?`${delta>0?'+':''}${fmt(delta*100)}`:'—'}</button></td>`}).join('')}</tr>`).join('')}</tbody></table></div><div class="panel-foot"><span>同一算法、固定原成员带入新版后的变化。点击定位国家 / BR 与原队列。</span><span>绿色 / 红色仅表示效用变化方向</span></div></section>`;
}

function changeRows() {
  return (state.scenario?.entries||[]).filter(e=>byId(e.vehicle_id)?.nation===state.nation);
}
function brChange(entry, compact=false) {
  const delta=finite(entry.new)&&finite(entry.old)?entry.new-entry.old:null;
  return `<span class="br-transition"><span>${esc(entry.old)}</span><span class="transition-arrow" aria-label="变为">→</span><strong>${esc(entry.new)}</strong></span>${!compact&&finite(delta)&&delta!==0?tag(`${delta>0?'上调':'下调'} ${fmt(Math.abs(delta))}`,delta>0?'warn':'blue'):''}`;
}
function versionBrief() {
  const entries=changeRows(), changes=entries.filter(e=>e.field?.startsWith('br')).slice(0,4);
  return `<aside class="version-brief"><div class="brief-heading">${icon('versions')}<h2>版本变化</h2>${tag('模拟','warn')}</div><p>本国 <strong>${unique(entries.map(e=>e.vehicle_id)).length}</strong> 辆载具涉及调整</p><div class="brief-changes">${changes.map(e=>`<button class="brief-change" data-vehicle="${esc(e.vehicle_id)}"><span>${esc(nameOf(e.vehicle_id))}</span>${brChange(e,true)}</button>`).join('')||'<div class="thin-empty">本国暂无 BR 调整。</div>'}</div><a class="button secondary" href="#versions">查看全部变化 ${icon('arrow')}</a><small>查看前后差异，再评估对队列的影响。</small></aside>`;
}
function overviewChoices() {return topLineups(currentRecs());}
function recommendationMembers(c,version,visibleCount) {
  const member=id=>{const v=byId(id),ammo=(v?.ammo||[]).find(a=>a.id===c.ammo_choices?.[id]);return `<li><button data-vehicle="${esc(id)}">${vehicleArt(v)}<span><strong>${esc(nameOf(id))}</strong><small>${esc(ammo?ammo.name||ammo.id:roleName(v?.role))}</small></span></button><span class="member-br">${fmt(v?.br?.[version])}</span></li>`;};
  return `<ul class="option-members">${c.lineup_ids.slice(0,visibleCount).map(member).join('')}</ul>${c.lineup_ids.length>visibleCount?`<details class="more-members"><summary>展开其余 ${c.lineup_ids.length-visibleCount} 辆 · 共 ${c.lineup_ids.length} 辆</summary><ul class="option-members">${c.lineup_ids.slice(visibleCount).map(member).join('')}</ul></details>`:`<p class="member-count">共 ${c.lineup_ids.length} 辆</p>`}`;
}
function recommendationChoice(c,index,{mode='overview',split=null}={}) {
  const title={balanced:'综合任务',ground:'地面作战',air_support:'航空支援'}[c.profile]||'综合任务';
  const version=split?'current':c.version||state.version,compact=mode!=='overview';
  const changed=split?.member_deltas.filter(row=>Math.abs(row.delta)>state.config.matching.epsilon)||[];
  return `<article class="lineup-option ${index===0?'preferred':''} ${compact?'compact-option':''} ${split?'split-option':''}" data-score="${c.score}" aria-label="第 ${index+1} 名，${esc(title)}，BR ${fmt(c.actual_br)}">
    <div class="option-heading"><div><span class="option-number">第 ${index+1} 名</span><h2>${title}</h2></div><span class="option-br">BR <strong>${fmt(c.actual_br)}</strong></span></div>
    <div class="option-score"><div><strong>${indexScore(c.score)}</strong><span>${split?'旧版评分':mode==='planned'?'新版评分':'队列评分'} <small>/ 100</small></span></div><span class="option-cap">${split?`保留原成员 · 新版 ${indexScore(split.after_fixed.score)}`:`${c.lineup_ids.length} / ${state.context.slots} 槽位`}</span></div>
    ${recommendationMembers(c,version,compact?4:6)}
    ${split?`<div class="split-reason"><p>最高 BR <strong>${fmt(c.actual_br)} → ${fmt(split.after_fixed.actual_br)}</strong></p>${changed.slice(0,2).map(row=>`<button data-vehicle="${esc(row.id)}">${esc(nameOf(row.id))}<span>${fmt(row.before)} → ${fmt(row.after)}</span></button>`).join('')}${changed.length>2?`<small>另有 ${changed.length-2} 辆调整，完整变化见队列影响。</small>`:''}</div>`:`<div class="option-metrics"><span>同档表现<strong>${indexScore(c.metrics?.same_tier)}</strong></span><span>升房表现<strong>${indexScore(c.metrics?.up_tier)}</strong></span><span>资料覆盖<strong>${pct(c.coverage)}</strong></span></div>`}
    ${split?`<button class="button secondary" data-split-review="${esc(JSON.stringify(c.lineup_ids))}" data-cap="${c.actual_br}">查看拆队影响 ${icon('arrow')}</button>`:`<button class="button ${index===0?'primary':'secondary'}" data-candidate="${esc(JSON.stringify(c.lineup_ids))}" data-candidate-version="${version}" data-cap="${c.actual_br}">选择这支队列 ${icon('arrow')}</button>`}
    <details class="option-explanation"><summary>评分与用弹依据</summary><p>按对手选择本车可用弹药；成员下方显示最常使用的弹种。交战距离 ${fmt(c.conditions?.physical_distance_m??state.context.physical_distance_m)} m。</p><p>估计区间 ${indexScore(c.lower)}–${indexScore(c.upper)} · GRP 路径抽样模型，尚未用实战数据校准。</p>${explain(c.explanations)}</details>
  </article>`;
}
function renderOverview() {
  const choices=overviewChoices();
  return `${heading('', '队列推荐','该系所有 BR 候选中的评分前三，按成员去重排序。')}
  <div class="choices-heading"><span class="selection-context">${esc(nationName(state.nation))}<span> / </span>${esc(versionName())}<span> / </span>全 BR 排名</span><span>${choices.length} 支推荐 · ${state.context.slots} 槽位</span></div>
  <section class="lineup-options" aria-label="推荐队列待选">${choices.map((c,i)=>recommendationChoice(c,i)).join('')||empty('当前条件没有可用队列','可在研究工具中的自定义队列设置条件。',button('scan','扫描可用队列','primary','refresh'))}</section>${sourceNote()}`;
}
function renderVersionRankings() {
  const overview=state.versionOverviews.get(recommendationKey('planned'));
  if(!overview)return busy('正在载入新版前三与被拆队列前三…');
  return `<section class="version-ranking-section"><div class="section-row"><div class="section-heading"><h2>新版评分最高的队列</h2><span class="ranking-description">${esc(nationName(state.nation))} · 全 BR 按新版评分排序</span></div>${tag(`${overview.planned_top.length} 支推荐`,'blue')}</div><div class="lineup-options" id="planned-top-lineups" aria-label="新版评分前三">${overview.planned_top.map((c,i)=>recommendationChoice(c,i,{mode:'planned'})).join('')||empty('新版暂无可用队列','检查当前限制与方案数据。')}</div></section>
  <section class="version-ranking-section"><div class="section-row"><div class="section-heading"><h2>被拆队列中的旧版前三</h2><span class="ranking-description">从 ${overview.split_candidate_count} 支受影响队列中，按旧版评分排序</span></div>${tag(`${overview.split_top.length} 支队列`,'warn')}</div><p class="split-definition">成员 BR 调整不同步，原有搭配受到影响；仍可一起编队。右侧分数为保留原成员后的新版评分。</p><div class="lineup-options" id="split-top-lineups" aria-label="被拆队列旧版评分前三">${overview.split_top.map((row,i)=>recommendationChoice(row.before,i,{mode:'split',split:row})).join('')||empty('该系暂无被拆队列','当前方案没有让已推荐队列的成员 BR 发生不同步调整。')}</div></section>`;
}
function brPicker() {
  return `<div class="custom-br-setting"><div class="br-picker"><label for="br-slider">目标权重 <span>BR</span><output id="br-value" for="br-slider"></output></label><div class="br-track"><span id="br-min"></span><input type="range" id="br-slider" min="0" max="0" step="1" value="0" aria-label="目标权重 BR"><span id="br-max"></span></div></div><small>设置自定义队列的成员 BR 上限</small></div>`;
}

function renderTree() {
  let vehicles=state.catalog.vehicles.filter(v=>v.nation===state.nation);
  if(state.kind!=='all')vehicles=vehicles.filter(v=>v.kind===state.kind);
  if(state.query)vehicles=vehicles.filter(v=>`${v.name} ${v.id} ${(v.ammo||[]).map(a=>a.name||a.id).join(' ')}`.toLowerCase().includes(state.query.toLowerCase()));
  if(state.treeChanged)vehicles=vehicles.filter(v=>v.br?.current!==v.br?.planned || (state.scenario?.entries||[]).some(e=>e.vehicle_id===v.id));
  if(state.treeMatching)vehicles=vehicles.filter(v=>finite(activeBR(v))&&activeBR(v)<=state.br);
  const ranks=unique(vehicles.map(v=>v.rank)).sort((a,b)=>(a??Infinity)-(b??Infinity));
  const rankSections=ranks.map(rank=>{
    const row=vehicles.filter(v=>v.rank===rank),branches=unique(row.map(v=>v.branch)).sort();
    return `<section class="tree-rank"><div class="rank-label"><small>RANK</small>${fmt(rank)}</div><div class="tree-vehicles">${branches.map(branch=>{const members=row.filter(v=>v.branch===branch).sort((a,b)=>(activeBR(a)??Infinity)-(activeBR(b)??Infinity)||a.name.localeCompare(b.name)),groups=new Map();for(const v of members){const key=v.folder||v.id;if(!groups.has(key))groups.set(key,[]);groups.get(key).push(v);}return `<div class="branch-label">${esc(branch==null?'分支未知':`分支 ${branch}`)} <span>· ${members.length} 辆</span></div>${[...groups.values()].map(group=>group.length===1?vehicleCard(group[0].id,{tree:true}):`<div class="tree-folder">${vehicleCard(group[0].id,{tree:true})}<details><summary>▣ 展开文件夹 · 另 ${group.length-1} 辆</summary><div class="folder-members">${group.slice(1).map(v=>vehicleCard(v.id,{tree:true})).join('')}</div></details></div>`).join('')}`}).join('')}</div></section>`;
  }).join('');
  return `${heading('TECH TREE / CATALOG',`${esc(nationName(state.nation))} · 科技树`,'等级、分支、文件夹与前置关系来自官方目录。地面和航空均使用陆战历史 BR；空缺值保留为未知。',button('go-lineup','编辑队列','primary','plus'))}
  <div class="toolbar"><div class="segmented" aria-label="载具类型">${[['all','全部'],...Object.entries(state.ui.kind_labels)].map(([id,name])=>`<button data-kind="${id}" class="${state.kind===id?'active':''}">${name}</button>`).join('')}</div><label><input type="checkbox" id="tree-matching" ${state.treeMatching?'checked':''}>不高于目标 BR</label><label><input type="checkbox" id="tree-changed" ${state.treeChanged?'checked':''}>仅看变更</label><input id="tree-search" type="search" value="${esc(state.query)}" placeholder="搜索名称 / 型号" aria-label="搜索科技树"><span class="count-chip">${vehicles.length} 辆</span></div>
  ${state.version==='planned'?sourceNote():''}
  <div class="legend-inline mb"><span><i></i>点击载具打开完整档案</span><span><i style="background:var(--amber)"></i>旧 BR → 新 BR</span><span>↳ 前置载具</span><span>▣ 官方文件夹</span><span>未核验构型不作换皮合并</span></div>
  ${rankSections||empty('没有符合筛选条件的载具','更换类型、BR 条件或搜索关键字。')}`;
}

function renderLineup() {
  const rec=selectedCandidate(),ids=chosenIds(),result=state.result;
  const eligible=state.catalog.vehicles.filter(v=>v.nation===state.nation&&finite(activeBR(v))&&activeBR(v)<=state.br&&(state.context.includeSpecial||!v.special)&&(!state.query||`${v.name} ${v.id}`.toLowerCase().includes(state.query.toLowerCase())));
  const page=eligible.slice(state.treePage*state.ui.table_page_size,(state.treePage+1)*state.ui.table_page_size);
  const owned=state.owned[state.nation]||[];
  return `${heading('LINEUP LAB / CONSTRAINTS', '把推荐变成你的队列','每个候选均由真实载具组成。限制槽位、特种载具与拥有列表后，用同一模型比较自动推荐和自选队列。',button('import-lineup','导入','secondary')+button('export-lineup','导出','secondary','download')+button('evaluate','计算队列','primary','arrow'))}
  <section class="controls-panel">${brPicker()}<div class="form-grid"><label class="form-field">可用槽位<input id="slots-input" type="number" min="${state.config.ui.slots.min}" max="${state.config.ui.slots.max}" step="${state.config.ui.slots.step}" value="${state.context.slots}"></label><label class="form-field">交战 / 弹道距离 m<input id="distance-input" type="number" min="${state.config.ui.distance_m.min}" max="${state.config.ui.distance_m.max}" step="${state.config.ui.distance_m.step}" value="${state.context.distance_m}"></label><label class="form-field">技能潜变量 s <span class="input-value">${fmt(state.context.skill)}</span><input id="skill-input" type="range" min="${state.config.ui.skill.min}" max="${state.config.ui.skill.max}" step="${state.config.ui.skill.step}" value="${state.context.skill}"></label></div><div class="toolbar"><label class="checkbox-label"><input id="special-input" type="checkbox" ${state.context.includeSpecial?'checked':''}>允许特种载具</label><label class="checkbox-label"><input id="owned-input" type="checkbox" ${state.context.availableIds?'checked':''}>只使用拥有列表 ${owned.length?`(${owned.length})`:''}</label>${button('edit-owned','管理拥有列表','small ghost')}${button('scan','按当前限制重新扫描本国全 BR','small secondary','refresh')}<span class="muted" style="font-size:10px">所有行为参数尚未校准 · 不改变装甲物理数据</span></div></section>
  ${section('01','我的队列',`${ids.length} / ${state.context.slots} 槽位`,button('auto-lineup','采用本档推荐','small')+button('clear-lineup','清空','small ghost'))}${lineupCards(ids,rec,true)}
  ${ids.length?`<details class="panel panel-pad mt"><summary>精确弹药、合法挂载与出击条件</summary><div class="form-grid">${ids.map(id=>{const v=byId(id),air=v.kind!=='ground',choices=air?(v.loadouts||[]).filter(p=>p.legal===true):(v.ammo||[]),selected=air?state.context.loadout_by_vehicle?.[id]:state.context.ammo_by_vehicle?.[id];return `<label class="form-field">${esc(v.name)} · ${air?'合法预设':'攻击弹药'}<select data-choice-id="${esc(id)}" data-choice-type="${air?'loadout':'ammo'}"><option value="">自动选择已知可用${air?'合法预设':'弹药'}</option>${choices.map(c=>`<option value="${esc(c.id)}" ${c.id===selected?'selected':''}>${esc(c.name||c.id)}</option>`).join('')}</select>${!choices.length?'<small>来源缺项，保持未知</small>':''}</label>`}).join('')}<label class="form-field">可用重生点（未知则留空）<input id="spawn-points" type="number" step="any" value="${state.context.available_spawn_points??''}" placeholder="使用已标注先验"></label><label class="form-field">目标雷达辐射状态<select id="radar-emitting"><option value="unknown" ${state.context.target_radar_emitting==null?'selected':''}>未知</option><option value="yes" ${state.context.target_radar_emitting===true?'selected':''}>正在辐射</option><option value="no" ${state.context.target_radar_emitting===false?'selected':''}>未辐射</option></select></label><label class="form-field">目标方位 / 防护姿态 °<input id="engagement-angle" type="number" min="${state.config.ui.angle_deg.min}" max="${state.config.ui.angle_deg.max}" step="${state.config.ui.angle_deg.step}" value="${state.context.angle_deg}"></label></div><p class="lead">选择的弹药必须属于当前载具。合法预设逐个分析，不将互斥预设的最大数量相加；反辐射武器按目标辐射条件处理。缺少对应距离或角度的物理结果时保持未知。</p></details>`:''}
  ${state.busy?busy('在 Web Worker 中计算相关配对…'):result?`<div class="stat-grid mt"><article class="stat-card"><div class="stat-label">实际队列最高 BR</div><div class="stat-value">${fmt(result.actual_br)}</div><div class="stat-foot">按成员计算，候选上限不会抬高低档队列</div></article><article class="stat-card"><div class="stat-label">综合决策指数</div><div class="stat-value positive">${indexScore(result.score)}</div><div class="stat-foot">区间 ${indexScore(result.lower)} – ${indexScore(result.upper)}</div></article><article class="stat-card"><div class="stat-label">主力匹配区间</div><div class="stat-value" style="font-size:23px">${fmt(result.environment?.minimum)} – ${fmt(result.environment?.maximum)}</div><div class="stat-foot">较低 BR 仍可被更高档队列携带</div></article><article class="stat-card"><div class="stat-label">资料覆盖</div><div class="stat-value">${pct(result.coverage)}</div><div class="stat-foot">${result.matrix?.length??'—'} 个方向独立的配对结果</div></article></div><div class="two-column"><section class="panel panel-pad">${metricsView(result.metrics)}</section><section class="panel panel-pad">${explain(result.explanations)}</section></div><div class="toolbar mt">${button('go-matrix','查看逐车矩阵','secondary','matrix')}${button('go-versions','比较固定队列的版本变化','secondary','versions')}</div>`:notice('加入或采用载具后点击“计算队列”。按本车弹药与敌车 GRP 抽样路径计算对抗；缺少资料的部分会保留未知。')}
  ${section('02','可加入的载具',`目标 BR ${fmt(state.br)} 及以下`,`<input type="search" id="lineup-search" value="${esc(state.query)}" placeholder="搜索本国载具" aria-label="搜索可加入载具">`)}
  <div class="available-list">${page.map(v=>`<div class="available-item"><button data-add="${esc(v.id)}" ${ids.includes(v.id)||ids.length>=state.context.slots?'disabled':''} aria-label="加入 ${esc(v.name)}">${ids.includes(v.id)?'✓':'+'}</button><div><strong data-vehicle="${esc(v.id)}">${esc(v.name)}</strong><small>${esc(roleName(v.role))} · ${v.special?'特种':'研发'}</small></div><span class="mono">${fmt(activeBR(v))}</span></div>`).join('')||empty('没有可加入的载具','检查 BR、搜索和特种载具筛选。')}</div><div class="pagination"><button class="button small ghost" data-page="${state.treePage-1}" ${state.treePage===0?'disabled':''}>上一页</button><span>${eligible.length?state.treePage+1:0} / ${Math.ceil(eligible.length/state.ui.table_page_size)}</span><button class="button small ghost" data-page="${state.treePage+1}" ${(state.treePage+1)*state.ui.table_page_size>=eligible.length?'disabled':''}>下一页</button></div>
  ${section('03','可比较的候选')}${candidateCards(candidates().filter(c=>c.br===state.br).slice(0,state.config.search.alternatives))}`;
}

function comparisonCard(title,result,style='') {
  return `<article class="comparison-card ${style}"><h3>${title}</h3><div class="comparison-number">${fmt(result?.actual_br)} <small class="muted" style="font-size:12px">BR</small></div><p>决策指数 ${indexScore(result?.score)} · 资料覆盖 ${pct(result?.coverage)}</p><div class="comparison-members">${(result?.lineup_ids||[]).map(id=>`<button data-vehicle="${esc(id)}">${esc(nameOf(id))}</button>`).join('')||'<span class="muted">尚未计算</span>'}</div>${result?.metrics?`<div class="mt">${metricsView(result.metrics)}</div>`:''}</article>`;
}
function announcementView(items) {
  if(!items?.length)return '<div class="thin-empty">此次方案没有改变该固定队列的成员 BR、匹配边界或已知属性。</div>';
  return items.map((item,index)=>`<div class="split-announcement"><span class="number-badge">${String(index+1).padStart(2,'0')}</span><div><h3>${esc(item.message||item.description||item.type)}</h3><div class="tags mt">${(item.vehicle_ids||[]).slice(0,state.config.lineup.explanation_limit).map(id=>`<button class="tag neutral" data-vehicle="${esc(id)}">${esc(nameOf(id))}</button>`).join('')}</div><div class="toolbar mt">${button('go-matrix','定位对抗矩阵','small ghost')}${button('go-lineup','调整队列','small ghost')}</div></div></div>`).join('');
}
function renderVersions() {
  const entries=state.scenario?.entries||[], comp=state.comparison;
  const own=state.catalog.vehicles.filter(v=>v.nation===state.nation), relevant=changeRows();
  const scoped=state.changeScope==='all'?entries:relevant;
  const isBR=e=>e.field?.startsWith('br');
  const up=scoped.filter(e=>isBR(e)&&e.new>e.old).length, down=scoped.filter(e=>isBR(e)&&e.new<e.old).length;
  const filtered=scoped.filter(e=>state.changeFilter==='up'?isBR(e)&&e.new>e.old:state.changeFilter==='down'?isBR(e)&&e.new<e.old:state.changeFilter==='other'?!isBR(e):true);
  const pageSize=20, pageCount=Math.ceil(filtered.length/pageSize);
  state.changePage=Math.min(state.changePage,Math.max(0,pageCount-1));
  const page=filtered.slice(state.changePage*pageSize,(state.changePage+1)*pageSize);
  const audits=state.scenario?.audit||[];
  const entered=(comp?.entered||[]).map(x=>typeof x==='string'?x:x.vehicle_id||x.id),left=(comp?.left||[]).map(x=>typeof x==='string'?x:x.vehicle_id||x.id);
  const fieldName=key=>key==='br'?'陆战历史 BR':state.ui.scenario_fields.find(f=>f.key===key)?.label||key;
  return `${heading('', '版本变化','查看新版最强候选，以及此次调整拆分的高分旧队列。',button('compare','比较队列影响','primary','versions'))}
  <section class="version-banner"><div class="version-banner-title">${icon('versions')}<div><h2>${state.scenarioCustom?'我的调整方案':'已采集的调整方案'}</h2><p>以当前目录为基线，预览调整后的陆战历史环境。</p></div></div><div class="version-flow"><span>当前版本</span>${icon('arrow')}<span>调整后 ${tag('模拟','warn')}</span></div></section>
  ${renderVersionRankings()}
  <div class="changes-heading"><div><h2>载具变化</h2><span class="muted">${scoped.length} 项调整</span></div><label class="scope-field">查看范围<select id="change-scope"><option value="nation" ${state.changeScope==='nation'?'selected':''}>${esc(nationName(state.nation))}</option><option value="all" ${state.changeScope==='all'?'selected':''}>全部国家</option></select></label></div>
  <section class="changes-panel"><div class="change-tabs" aria-label="变更类型">${[['all','全部',scoped.length],['up','BR 上调',up],['down','BR 下调',down],['other','性能调整',scoped.filter(e=>!isBR(e)).length]].map(([id,label,count])=>`<button data-change-filter="${id}" class="${state.changeFilter===id?'active':''}" aria-pressed="${state.changeFilter===id}">${label}<span>${count}</span></button>`).join('')}</div>
  <div class="change-list">${page.map(e=>`<article class="change-row"><button class="changed-vehicle" data-vehicle="${esc(e.vehicle_id)}">${vehicleArt(byId(e.vehicle_id))}<span><strong>${esc(nameOf(e.vehicle_id))}</strong><small>${esc(nationName(byId(e.vehicle_id)?.nation||''))} · ${esc(roleName(byId(e.vehicle_id)?.role))}</small></span></button><span class="change-field">${esc(fieldName(e.field))}</span><div class="change-values">${isBR(e)?brChange(e):`<span class="old-value">${esc(typeof e.old==='object'?JSON.stringify(e.old):e.old)}</span><span class="transition-arrow">→</span><strong>${esc(typeof e.new==='object'?JSON.stringify(e.new):e.new)}</strong>`}</div><button class="text-button change-detail" data-vehicle="${esc(e.vehicle_id)}">载具详情 ${icon('arrow')}</button></article>`).join('')||empty('没有符合条件的调整','切换变更类型或查看全部国家。')}</div>
  ${pageCount>1?`<div class="pagination"><button class="button small ghost" data-change-page="${state.changePage-1}" ${state.changePage===0?'disabled':''}>上一页</button><span>${state.changePage+1} / ${pageCount}</span><button class="button small ghost" data-change-page="${state.changePage+1}" ${state.changePage+1>=pageCount?'disabled':''}>下一页</button></div>`:''}</section>
  <section class="compare-callout"><div>${icon('lineup')}<div><h2>这些调整，会影响我的队列吗？</h2><p>${chosenIds().length?`用已选的 ${chosenIds().length} 辆载具比较。`:'将使用当前档位的推荐队列。'}查看原阵容与新版推荐的差别。</p></div></div>${button('compare',comp?'重新比较队列':'比较队列影响','primary','versions')}</section>
  ${state.busy?busy('正在比较原队列与调整后的推荐…'):''}
  ${comp?`<div class="comparison-grid" id="lineup-comparison">${comparisonCard('当前版本 · 原队列',comp.before)}${comparisonCard('调整后 · 保持成员',comp.after_fixed)}${comparisonCard('调整后 · 重新推荐',comp.after_optimized,'optimized')}</div>`:''}
  ${comp?.ground_only_alternative?`<section class="panel panel-pad mb"><div class="section-heading"><h3>保留地面成员，移除航空的对照方案</h3>${tag(`实际 BR ${fmt(comp.ground_only_alternative.actual_br)}`,'neutral')}${tag(`指数 ${indexScore(comp.ground_only_alternative.score)}`,'neutral')}</div><div class="tags mt">${comp.ground_only_alternative.lineup_ids.map(id=>`<button class="tag neutral" data-vehicle="${esc(id)}">${esc(nameOf(id))}</button>`).join('')}</div><p class="lead">用于区分保留全组合的匹配成本与移除航空的任务损失。此项与新版自动优化候选分别计算。</p></section>`:''}
  ${comp?`<div class="two-column"><section class="panel"><div class="panel-title"><h2>环境公告与队列拆分</h2>${tag('可追溯','neutral')}</div><div class="panel-body">${announcementView(comp.announcements)}</div></section><section class="panel"><div class="panel-title"><h2>主力匹配范围的变化</h2><span class="muted">进入 ${entered.length} / 离开 ${left.length}</span></div><div class="panel-body"><h3 class="positive">进入主力区间</h3><div class="tags mt">${entered.map(id=>`<button class="tag warn" data-vehicle="${esc(id)}">${esc(nameOf(id))}</button>`).join('')||'<span class="muted">无</span>'}</div><h3 class="muted mt">离开主力区间</h3><div class="tags mt">${left.map(id=>`<button class="tag neutral" data-vehicle="${esc(id)}">${esc(nameOf(id))}</button>`).join('')||'<span class="muted">无</span>'}</div><p class="lead">离开主力区间不表示绝不遇见：低档载具仍可能被高档队列携带。</p></div></section></div>`:''}

  <details class="disclosure"><summary><span>各国 / BR 影响图</span><small>比较原队列在新版中的表现</small></summary>${impactMap()}</details>
  <details class="disclosure"><summary><span>编辑或导入调整方案</span><small>自定义 BR 与性能变化</small></summary><div class="toolbar">${button('import-scenario','导入方案','secondary')}${button('export-scenario','导出方案','secondary','download')}</div>
  <section class="panel panel-pad"><div class="form-grid"><label class="form-field">载具<select id="scenario-vehicle">${own.sort((a,b)=>a.name.localeCompare(b.name)).map(v=>`<option value="${esc(v.id)}">${esc(v.name)} · ${fmt(v.br?.current)}</option>`).join('')}</select></label><label class="form-field">调整字段<select id="scenario-field">${state.ui.scenario_fields.map(f=>`<option value="${esc(f.key)}">${esc(f.label)}</option>`).join('')}</select></label><label class="form-field">方案新值<input id="scenario-value" type="number" step="any" placeholder="填写方案值"></label></div><div class="toolbar mt">${button('add-change','加入方案并重新计算','primary','plus')}${button('reset-scenario','恢复采集的原始方案','ghost')}<span class="muted" style="font-size:10px">新旧值、来源、操作时间与冲突均进入审计。不会改写当前快照。</span></div></section>
</details>
  <details class="disclosure"><summary><span>方案来源与变更记录</span><small>${audits.length} 条记录</small></summary><p class="lead">${state.scenarioCustom?'当前使用本地编辑或导入的模拟方案。':safeURL(state.scenario?.source_url)?`<a class="text-button" href="${esc(safeURL(state.scenario.source_url))}" target="_blank" rel="noopener">查看原始调整来源</a>`:'来源信息尚未取得。'} 所有调整均作为模拟方案显示。</p><section class="panel panel-pad mt">${explain([...(comp?.audit||[]),...audits])}</section></details>`;
}

function renderMatrix() {
  const result=state.result,pairs=result?.matrix||[];
  const own=chosenIds().length?chosenIds():result?.lineup_ids||[];
  let enemyIds=unique(pairs.map(p=>p.target_id).filter(id=>!own.includes(id)));
  if(state.matrixTarget)enemyIds=enemyIds.filter(id=>id===state.matrixTarget);
  let groups=enemyIds.map(id=>[id]);
  if(state.matrixFold){const map=new Map();for(const id of enemyIds){const v=byId(id),key=v?.equivalence_verified===true&&v.equivalence_group?v.equivalence_group:id;if(!map.has(key))map.set(key,[]);map.get(key).push(id);}groups=[...map.values()];}
  const start=state.matrixPage*state.ui.matrix_column_limit,visible=groups.slice(start,start+state.ui.matrix_column_limit);
  const lookup=new Map(pairs.map(p=>[`${p.attacker_id}/${p.target_id}`,p]));
  return `${heading('PAIRWISE / THREAT MATRIX', '每辆车，对每个具体对手','攻击方向独立计算；每个结果保留精确弹药、挂载与条件。换皮折叠仅改变显示，原始配对权重保持不变。',button('evaluate','按当前队列重算','primary','refresh'))}
  ${notice('矩阵数值是待校准的单向交战效用指数，不是击毁率或胜率。未知单元格保留资料缺项；防空互打只在整队汇总中降权，原始交战结果可展开。')}
  <div class="toolbar"><label><input id="matrix-fold" type="checkbox" ${state.matrixFold?'checked':''}>折叠已核验等价换皮</label><label>指定对手<select id="matrix-target"><option value="">全部相关对手</option>${unique(pairs.map(p=>p.target_id).filter(id=>!own.includes(id))).map(id=>`<option value="${esc(id)}" ${id===state.matrixTarget?'selected':''}>${esc(nameOf(id))}</option>`).join('')}</select></label><span class="count-chip">${own.length} 己方 × ${enemyIds.length} 对手</span>${button('export-matrix','导出完整配对结果','small secondary','download')}</div>
  ${state.busy?busy('正在逐车计算双方独立攻击方向…'):!pairs.length?empty('还没有队列分析结果','在我的队列选择载具并计算，或直接按当前推荐计算。',button('evaluate','使用推荐队列计算','small primary')):`<section class="panel"><div class="table-wrap"><table class="data-table matrix-table"><thead><tr><th>己方 ↓ / 目标 →</th>${visible.map(group=>`<th><button data-vehicle="${esc(group[0])}">${esc(nameOf(group[0]))}</button><br><small class="muted">${esc(nationName(byId(group[0])?.nation||''))} · ${fmt(activeBR(byId(group[0])))}</small>${group.length>1?`<details><summary>另 ${group.length-1} 个成员</summary>${group.slice(1).map(id=>`<button data-vehicle="${esc(id)}">${esc(nameOf(id))}</button>`).join('<br>')}</details>`:''}</th>`).join('')}</tr></thead><tbody>${own.map(id=>`<tr><td><button data-vehicle="${esc(id)}">${esc(nameOf(id))}</button><br><small class="muted">${esc(roleName(byId(id)?.role))}</small></td>${visible.map(group=>{const p=lookup.get(`${id}/${group[0]}`),score=p?.score;return `<td class="matrix-cell"><button class="matrix-value ${!finite(score)?'unknown':score<state.config.lineup.threat_thresholds.medium?'risk':score<state.config.lineup.threat_thresholds.high?'neutral':''}" data-pair="${esc(`${id}/${group[0]}`)}">${indexScore(score)}<small>${esc(p?.ammo_id||p?.loadout_id||p?.quality||'未知')}</small></button></td>`}).join('')}</tr>`).join('')}</tbody></table></div><div class="panel-foot"><span>点击单元格查看原始指数、汇总权重与缺项。</span><span>${esc(result?.model_version||state.config.model_version)}</span></div></section><div class="pagination"><button class="button small ghost" data-matrix-page="${state.matrixPage-1}" ${state.matrixPage===0?'disabled':''}>上一组</button><span>${state.matrixPage+1} / ${Math.ceil(groups.length/state.ui.matrix_column_limit)}</span><button class="button small ghost" data-matrix-page="${state.matrixPage+1}" ${start+state.ui.matrix_column_limit>=groups.length?'disabled':''}>下一组</button></div>`}
  ${result?`${section('01','危险对手与应对能力')}<div class="two-column"><section class="panel panel-pad">${threatsView(result.threats,state.config.lineup.explanation_limit)}</section><section class="panel panel-pad"><h3 class="mb">有利对手</h3>${threatsView(result.advantages,state.config.lineup.explanation_limit)}</section></div>${section('02','汇总解释')}<section class="panel panel-pad">${explain(result.explanations)}</section>`:''}`;
}

function renderArmor() {
  const title=heading('ARMOR LAB / CONDITIONAL', '防护研究','按精确弹药和采样视角查看条件穿透图。');
  if(!state.manifest.probability||state.probabilityIndex?.entries.length===0)return `${title}${empty('暂未提供防护图','当前数据版本没有可用的防护切片。队列推荐、载具图鉴和对抗分析仍可使用。')}`;
  if(state.armorError)return `${title}${empty('防护图暂时无法加载',state.armorError,button('armor-retry','重试加载','primary','refresh'))}`;
  if(!state.probabilityIndex)return `${heading('ARMOR LAB / CONDITIONAL', '防护研究','按精确弹药和采样视角查看条件穿透图。')}${busy('按需载入防护元数据…')}`;
  const entry=state.armorEntry,index=state.probabilityIndex;
  const targets=unique(index.entries.map(e=>e.target_id));
  const target=entry?.target_id;
  const skill=state.armorSkill??state.config.defaults.skill,distance=state.armorDistance??state.config.defaults.distance_m;
  return `${heading('ARMOR LAB / CONDITIONAL', '让未知也出现在防护图上','颜色表示分层直线路径模型的条件估计。背景、无受保护内部目标、未知和有效样本分别保留。',button('armor-metadata','下载当前元数据','secondary','download'))}
  ${notice(`当前防护资料版本 <strong>${esc(index.source_version)}</strong> · 模型 ${esc(entry?.model_version)}。它与 Wiki 目录快照分别追溯。参考弹药不代表选中目标能够使用该弹；这些图不自动套入其它弹药配对。`,'warning')}
  <div class="toolbar"><label>目标构型<select id="armor-target">${targets.map(id=>`<option value="${esc(id)}" ${id===target?'selected':''}>${esc(nameOf(id))}</option>`).join('')}</select></label><label>攻击用参考弹<select id="armor-ammo">${index.entries.filter(e=>e.target_id===target).map(e=>`<option value="${esc(e.id)}" ${e.id===entry?.id?'selected':''}>${esc(e.ammo_id)} · ${esc(e.mode)}</option>`).join('')}</select></label>${tag('分层直线路径条件估计','warn')}${tag('未做游戏概率校准','neutral')}</div>
  <div class="heatmap-layout"><section class="panel panel-pad"><div class="heatmap-stage"><span class="stage-label">CONDITIONAL PENETRATION / NATIVE SAMPLES</span><canvas id="armor-canvas" aria-label="按状态分类的条件穿透概率图"></canvas><span class="stage-scale">${entry?.width} × ${entry?.height} 真实采样 · 间距 ${fmt(entry?.pixel_pitch_m*1000)} mm</span></div><div class="angle-control" aria-label="采样视角">${(entry?.angles_deg||[]).map(angle=>`<button class="angle-button ${angle===state.armorAngle?'active':''}" data-angle="${angle}">${fmt(angle)}°</button>`).join('')}</div><div class="probability-gradient"></div><div class="gradient-labels"><span>0% 条件穿透</span><span>50%</span><span>100% 条件穿透</span></div><div class="heatmap-legend"><span><i style="background:rgb(${state.ui.heatmap.background.join(',')})"></i>背景</span><span><i style="background:rgb(${state.ui.heatmap.no_target.join(',')})"></i>无受保护内部目标</span><span><i style="background:rgb(${state.ui.heatmap.unknown.join(',')})"></i>未知 / NaN</span><span>斜纹：材料响应标记</span></div><div id="pixel-readout" class="pixel-readout">将指针移到图上查看像素、ray_id、状态和材料响应标记。<br>“无受保护内部目标”不代表该处不会受损。</div><details><summary>坐标方向、缓存条件与模型边界</summary><div class="prob-meta">${esc(entry?.coordinate_convention)}<br>物理射距 ${fmt(entry?.physical_distance_m)} m<br>几何 SHA256 ${esc(entry?.geometry_version)}<br>材料配置 SHA256 ${esc(entry?.configuration_version)}<br>条件 ${esc(entry?.conditions)}<br>model_required_charge_mm 是装甲栈中间消耗量，不是 RHAe，本站不将其展示为等效厚度。</div></details></section>
  <div class="heatmap-controls"><section class="panel panel-pad"><h3 class="mb">非均匀瞄准模型</h3><label class="form-field">技能潜变量 s <span class="input-value" id="armor-skill-value">${fmt(skill)}</span><input id="armor-skill" type="range" min="${state.config.ui.skill.min}" max="${state.config.ui.skill.max}" step="${state.config.ui.skill.step}" value="${skill}"></label><label class="form-field mt">瞄准距离 m<input id="armor-distance" type="number" min="${state.config.ui.distance_m.min}" max="${state.config.ui.distance_m.max}" step="${state.config.ui.distance_m.step}" value="${distance}"></label><details><summary>运动与炮控参考</summary><label class="form-field">自身速度 m/s<input id="armor-own-motion" type="number" min="${state.ui.motion_mps.min}" max="${state.ui.motion_mps.max}" step="${state.ui.motion_mps.step}" value="${state.context.own_motion_mps}"></label><label class="form-field mt">目标速度 m/s<input id="armor-target-motion" type="number" min="${state.ui.motion_mps.min}" max="${state.ui.motion_mps.max}" step="${state.ui.motion_mps.step}" value="${state.context.target_motion_mps}"></label><label class="form-field mt">炮控参考（仅影响误差）<select id="armor-gun-control"><option value="">未指定 / 使用模型先验</option>${state.catalog.vehicles.filter(v=>v.nation===state.nation&&finite(v.firepower?.traverse_deg_s)).map(v=>`<option value="${esc(v.id)}" ${state.armorGun===v.id?'selected':''}>${esc(v.name)} · ${fmt(v.firepower.traverse_deg_s)}°/s</option>`).join('')}</select></label></details><p class="lead">仅改变落点误差。图的弹道物理仍是 <strong>${fmt(entry?.physical_distance_m)} m</strong>；不会伪造新射距的装甲结果。</p><div class="aim-result" id="aim-result">${busy('积分实际落点分布…')}</div><p class="prob-meta">中心瞄准与弱点稳健中心混合。落点可位于未知区、厚甲和目标外；不会只保留可击穿区再归一化。</p></section><section class="panel panel-pad mt"><h3 class="mb">资料边界</h3><div class="tags mb">${tag('原始姿态','neutral')}${tag('未损伤装甲','neutral')}${tag('不含击毁模型','warn')}</div><p class="prob-meta">不包含碎片、爆压、乘员生命、ERA 消耗状态与完整 NERA 动态响应。放大显示没有新增射线样本。</p><a href="#audit" class="text-button">查看全部来源与量化误差 ↗</a></section></div></div>`;
}

function renderAudit() {
  const snap=state.catalog.snapshot,coverage=snap.coverage||{},manifest=state.manifest;
  const audit=state.audit;
  const entries=Array.isArray(audit)?audit: audit?.issues||audit?.audit||audit?.events||[];
  const sourceRows=[
    ...(manifest.client_summary?[{name:'本地客户端 GRP',url:null,state:'配置与资源已绑定',note:`版本 ${manifest.client_summary.version} · ${manifest.client_summary.ground_units} 个地面单位配置 · ${manifest.client_summary.status_counts?.complete??0} 个构型完成计算。补丁资源优先，使用独立车型材料绑定。`}]:[]),
    {name:'官方 Wiki',url:snap.source_url,state:'目录固定快照',note:`采集时间 ${snap.collected_at} · ${snap.id}。Wiki 不暴露游戏提交，因此不声称与独立防护数据完全同版。`},
    {name:'Datamine',url:manifest.sources?.datamine||'https://github.com/gszabi99/War-Thunder-Datamine',state:'提交已锁定',note:`版本 ${snap.datamine_game_version||'未知'} · commit ${snap.datamine_reference||'未知'}`},
    {name:'腾讯调整表',url:state.sourceScenario?.source_url,state:state.sourceScenario?.status||'未知',note:'计划保持为模拟方案。旧值冲突、重复行与未映射内容保留原始审计。'},
    {name:'客户端概率数据',url:null,state:'条件估计',note:`${manifest.probability_summary?.targets??'—'} 目标构型 · ${manifest.probability_summary?.profiles??'—'} 精确参考弹配置 · ${manifest.probability_summary?.views??'—'} 视角切片。来源与范围独立追溯。`},
    {name:'本车弹药对抗',url:null,state:'抽样估计',note:`${manifest.target_layer_summary?.targets??'—'} 个 GRP 构型、${fmt(manifest.target_layer_summary?.sampled_rays)} 条路径。按本车弹药的距离与倾角穿深表计算；Wiki 与客户端来源分别追踪，未模拟跳弹、毁伤和动态反应装甲。`},
    {name:'玩家统计',url:manifest.sources?.statistics,state:'未校准',note:'默认平均技能不依赖在线统计。接入数据必须声明模式、时间窗、样本、偏差；总体胜率不会直接当成纯性能。'}
  ];
  return `${heading('PROVENANCE / AUDIT', '知道数据来自哪里，也知道它缺少什么','目录覆盖、属性覆盖、精确物理覆盖分别衡量。所有经验系数与输入版本公开，缺失项不会被补为零。',button('model-config','查看模型配置','secondary')+button('download-audit','下载审计','primary','download'))}
  <div class="stat-grid"><article class="stat-card"><div class="stat-label">载具目录</div><div class="stat-value">${fmt(coverage.catalog_vehicles??state.catalog.vehicles.length)}</div><div class="stat-foot">地面、固定翼与直升机</div></article><article class="stat-card"><div class="stat-label">陆战历史 BR 已知</div><div class="stat-value positive">${fmt(coverage.ground_rb_known)}</div><div class="stat-foot">航空独立读取 Ground RB</div></article><article class="stat-card"><div class="stat-label">详情已解析</div><div class="stat-value">${fmt(coverage.details_parsed)}</div><div class="stat-foot">属性仍可能存在逐字段缺项</div></article><article class="stat-card"><div class="stat-label">具备具体弹药资料</div><div class="stat-value">${fmt(coverage.with_ammunition)}</div><div class="stat-foot">不以参考热力图推断弹药归属</div></article></div>
  <section class="panel panel-pad">${sourceRows.map(source=>`<div class="audit-source"><strong>${source.name}</strong><div>${source.url?`<a href="${esc(safeURL(source.url))}" target="_blank" rel="noopener">${esc(source.url)} ↗</a>`:'<span class="muted">用户提供的客户端与派生数据</span>'}<p>${esc(source.note)}</p></div>${tag(source.state,source.state==='未校准'?'warn':'neutral')}</div>`).join('')}</section>
  ${section('01','模型约定与运行方式')}
  <div class="three-column"><section class="panel panel-pad"><h3>固定模型，同版重算</h3><p class="lead">两版比较共享 ${esc(state.config.model_version)}。调整双方数据后再生成环境、固定队列与优化候选。经验系数均来自可编辑配置。</p></section><section class="panel panel-pad"><h3>无业务后端也可查询</h3><p class="lead">常规查询读取静态分片。自选队列在 Worker 内计算；GRP 获取、几何、射线和物理重算属于离线后端任务。</p></section><section class="panel panel-pad"><h3>完整保留未知</h3><p class="lead">NaN、背景、无内部保护目标和未知材料分别编码。模型中间消耗量不发布为 RHAe，单发穿透不等于击毁或胜率。</p></section></div>
  ${section('02','构建与资源测量')}
  <section class="panel panel-pad"><div class="compact-grid">${Object.entries(manifest.resource_summary||{}).map(([key,value])=>`<div class="compact-item"><dt>${esc(key)}</dt><dd>${esc(typeof value==='object'?JSON.stringify(value):value)}</dd></div>`).join('')||'<div class="muted">详见交付报告中的静态构建测量。</div>'}</div><div class="toolbar mt">${button('build-report','查看构建报告','small secondary')}${button('probability-report','查看概率导出报告','small secondary')}</div></section>
  ${section('03','采集、映射与缺项审计')}
  <section class="panel panel-pad">${entries.length?explain(entries):'<p class="muted">审计报告按来源、抓取失败、字段覆盖和方案冲突记录。打开完整报告可查看详细内容。</p>'}<details><summary>展开完整机器可读审计</summary><pre class="json-code">${esc(JSON.stringify(audit,null,2))}</pre></details></section>
  ${section('04','模型参数')}
  <section class="panel panel-pad"><p class="lead">${esc(state.config.description)}</p><div class="tags mt">${tag(state.config.model_version,'neutral')}${tag(state.config.calibration_status,'warn')}</div><details><summary>展开可版本化配置（所有经验权重、阈值与先验）</summary><pre class="json-code">${esc(JSON.stringify(state.config,null,2))}</pre></details></section>`;
}

function render() {
  if(!state.catalog)return;
  $('#current-view-label').textContent=views.find(v=>v[0]===state.view)?.[1]||'战备总览';
  $$('#navigation a').forEach(a=>{a.classList.toggle('active',a.dataset.view===state.view); if(a.dataset.view===state.view)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});
  $$('.version-control button').forEach(b=>b.classList.toggle('active',b.dataset.version===state.version));
  const renderers={overview:renderOverview,tree:renderTree,lineup:renderLineup,versions:renderVersions,matrix:renderMatrix,armor:renderArmor,audit:renderAudit};
  $('#workspace').innerHTML=(renderers[state.view]||renderOverview)();
  syncBRControl();
  if(state.view==='armor'&&state.armorGrid?.entry_id===state.armorEntry?.id&&state.armorGrid?.angle_deg===state.armorAngle) {drawArmor(); renderAim();}
  $('#footer-status').textContent='模型指数用于条件比较，不代表游戏胜率。';
}

function download(filename,data) {
  const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),link=document.createElement('a');
  link.href=url;link.download=filename;link.click();URL.revokeObjectURL(url);
}

function openDialog(html) {$('#dialog-content').innerHTML=html;$('#detail-dialog').showModal();}
async function openVehicle(id) {
  try {
    const original=byId(id);if(!original)return toast('目录中没有该载具');
    let v=original;
    if(!v._detail&&state.manifest.vehicle_details?.[id]) {v=await fetchJSON(state.manifest.vehicle_details[id]);state.baseVehicles.set(id,{...v,_detail:true});await applyScenarioToIndex();v=byId(id);}
    const field=(label,value,unit='')=>`<div class="compact-item"><dt>${label}</dt><dd>${typeof value==='boolean'?(value?'有':'无'):finite(value)?`${fmt(value)} ${unit}`:value?esc(value):'<span class="muted">未知 / 未核验</span>'}</dd></div>`;
    const p=v.protection||{},m=v.mobility||{},f=v.firepower||{},s=v.sensors||{},aa=v.air_defence||{};
    const ammo=v.ammo||[],loadouts=v.loadouts||[];
    openDialog(`<div class="detail-head">${vehicleArt(v)}<div><div class="eyebrow">VEHICLE DOSSIER</div><h2>${esc(v.name)}</h2><p>${esc(nationName(v.nation))} · ${esc(roleName(v.role))} · Rank ${fmt(v.rank)} · ${esc(v.id)}</p><div class="tags">${tag(`陆战历史 BR ${fmt(activeBR(v))}`)}${tag(v.special?'特种载具':'常规研发','neutral')}${tag(v.br?.current!==v.br?.planned?'方案有变化':'快照基线','blue')}</div></div></div>
    <div class="detail-section"><h3>机动 · ${esc(m.performance_state||state.config.defaults.performance_state)}</h3><dl class="compact-grid">${field(v.kind==='ground'?'前进极速':'最大速度（来源高度）',m.forward_kmh,'km/h')}${v.kind!=='ground'?field('速度对应高度',m.speed_altitude_m,'m'):''}${field('后退极速',m.reverse_kmh,'km/h')}${field('功重比',m.power_to_weight_hp_t,'hp/t')}${field('中心转向',m.neutral_steering)}</dl></div>
    <div class="detail-section"><h3>火力 · 区分满级王牌人工装填和自动装弹周期</h3><dl class="compact-grid">${field('人工满级王牌装填',f.reload_ace_s,'s')}${field('自动装弹周期',f.autoload_s,'s')}${field('装填机制',f.reload_type)}${field('俯角 / 仰角',finite(f.depression_deg)&&finite(f.elevation_deg)?`${fmt(f.depression_deg)}° / ${fmt(f.elevation_deg)}°`:null)}${field('方向机速度',f.traverse_deg_s,'°/s')}${field('高低机速度',f.vertical_deg_s,'°/s')}</dl></div>
    <div class="detail-section"><h3>防护 · 物理厚度不代表全车 RHAe</h3><dl class="compact-grid">${field('正面已知物理厚度',p.front_mm,'mm')}${field('侧面物理厚度',p.side_mm,'mm')}${field('ERA',p.era)}${field('防崩落内衬',p.spall_liner)}</dl><p class="lead">分区、入射角、复合结构与弹药响应需单独核验。厚度字段不会自动升级为精准等效防护。</p></div>
    <div class="detail-section"><h3>观瞄与辅助</h3><dl class="compact-grid">${field('炮手热像代数',s.thermal_generation??s.gunner_thermal_generation)}${field('车长热像代数',s.commander_thermal_generation)}${field('抛射烟',s.smoke_grenades??v.auxiliary?.smoke_grenades)}${field('引擎烟',s.engine_smoke??v.auxiliary?.engine_smoke)}${field('夜视',s.night_vision)}${field(`重机枪 ≥ ${fmt(state.ui.heavy_machine_gun_min_caliber_mm)} mm`,v.auxiliary?.heavy_machine_gun??(v.auxiliary?.machine_guns?.length?v.auxiliary.machine_guns.filter(g=>finite(g.caliber_mm)&&g.caliber_mm>=state.ui.heavy_machine_gun_min_caliber_mm).map(g=>`${g.name||g.weapon_id||'机枪'} ${fmt(g.caliber_mm)} mm`).join(' / '):null))}</dl></div>
    <div class="detail-section"><h3>精确弹药 / 距离 / 入射角</h3>${ammo.length?`<div class="table-wrap"><table class="data-table"><thead><tr><th>弹药</th><th>类别</th><th>标称口径</th><th>穿深曲线（距离 / 角度 / mm）</th></tr></thead><tbody>${ammo.map(a=>`<tr><td>${esc(a.name||a.id)}<br><small class="muted">${esc(a.id)}</small></td><td>${esc(a.type||a.mode||'未知')}</td><td>${fmt(a.caliber_mm)} mm</td><td class="wrap mono">${(a.penetration||[]).map(x=>`${x.range_invariant||a.range_invariant?'穿深不随距离变化':`${fmt(x.distance_m)} m`} / ${fmt(x.angle_deg)}° / ${fmt(x.mm)} mm`).join(' · ')||'未知'}</td></tr>`).join('')}</tbody></table></div>`:'<p class="lead">没有可核验的具体弹药列表。不会按国家、口径或参考弹药填入。</p>'}</div>
    ${v.multi_vehicle_system?`<div class="detail-section"><h3>多载具系统组成</h3><div class="tags">${v.multi_vehicle_system.members.map(member=>`<button class="tag neutral" data-vehicle="${esc(member.vehicle_id)}">${esc(nameOf(member.vehicle_id))} × ${fmt(member.count)} · ${esc(member.source_label)}</button>`).join('')}</div><p class="lead">成员关系来自官方页面。组件不获得主车 BR，不重复计为独立完整槽位；系统性能尚未自动聚合。</p></div>`:''}<div class="detail-section"><h3>防空 / 航空</h3><dl class="compact-grid">${field('导弹标称射程',aa.missile_range_m,'m')}${field('导弹过载',aa.missile_overload_g,'g')}${field('架上即用弹',aa.ready_missiles)}${field('雷达扫描周期',aa.scan_period_s??aa.radar_scan_period_s,'s')}</dl>${loadouts.length?`<details><summary>${loadouts.length} 个来源挂载预设 / 同时携带约束</summary><pre class="json-code">${esc(JSON.stringify(loadouts,null,2))}</pre></details>`:'<p class="lead">未核验合法混挂。互斥预设的弹药最大数量不会相加。</p>'}</div>
    <div class="detail-section"><h3>缺项与来源</h3>${explain(v.missing,'当前记录没有额外的字段缺项清单；仍以各字段的未知状态为准。')}<div class="source-line mt">${(v.provenance||[]).map(source=>`${source.url?`<a href="${esc(safeURL(source.url))}" target="_blank" rel="noopener">${esc(source.url)}</a>`:esc(source.source||'')}<br>${esc(source.method||'')} · ${esc(source.commit||source.collected_at||'')}`).join('<br>')}</div></div>
    <div class="detail-actions"><button class="button primary" data-add="${esc(v.id)}" ${v.nation!==state.nation?'disabled':''}>加入当前队列</button><button class="button secondary" data-armor-target="${esc(v.id)}">查看防护资料</button><a class="button ghost" href="${esc(safeURL(v.wiki_url))}" target="_blank" rel="noopener">官方 Wiki ↗</a></div>`);
  }catch(error){toast(error.message);}
}
function openPair(key) {
  const [attacker,target]=key.split('/'),p=state.result?.matrix?.find(p=>p.attacker_id===attacker&&p.target_id===target);
  if(!p)return toast('该方向没有配对数据');
  const reverse=state.result.reverse_matrix?.find(pair=>pair.attacker_id===target&&pair.target_id===attacker);
  openDialog(`<div class="eyebrow">PAIR EVIDENCE / DIRECTIONAL</div><h2>${esc(nameOf(attacker))} → ${esc(nameOf(target))}</h2><p class="lead">${esc(p.ammo_id||'弹药未知')} · ${esc(p.quality||'资料层级未知')}</p><div class="stat-grid mt"><div class="stat-card"><div class="stat-label">单向交战指数</div><div class="stat-value">${indexScore(p.score)}</div></div><div class="stat-card"><div class="stat-label">不确定性区间</div><div class="stat-value" style="font-size:22px">${indexScore(p.lower)} – ${indexScore(p.upper)}</div></div><div class="stat-card"><div class="stat-label">汇总权重</div><div class="stat-value">${fmt(p.aggregation_weight)}</div></div><div class="stat-card"><div class="stat-label">独立反向指数</div><div class="stat-value">${indexScore(reverse?.score)}</div></div></div>${notice('原始单向指数与整队汇总权重分别展示。缺反向数据时保持未知，不使用互补值。')}<h3 class="mb">缺项与适用条件</h3>${explain(p.missing)}<details open><summary>完整配对证据</summary><pre class="json-code">${esc(JSON.stringify(p,null,2))}</pre></details>${reverse?`<details><summary>独立计算的反向证据</summary><pre class="json-code">${esc(JSON.stringify(reverse,null,2))}</pre></details>`:''}`);
}

function drawArmor() {
  const canvas=$('#armor-canvas'),grid=state.armorGrid;if(!canvas||!grid)return;
  canvas.width=grid.width;canvas.height=grid.height;
  const ctx=canvas.getContext('2d'),data=ctx.createImageData(grid.width,grid.height),colors=state.ui.heatmap;
  for(let i=0;i<grid.probabilities.length;i++){
    const p=grid.probabilities[i],status=grid.status[i];let color;
    if(status===0)color=colors.background;
    else if(status===1)color=colors.no_target;
    else if(status===2||!Number.isFinite(p))color=colors.unknown;
    else {const low=p<colors.midpoint?colors.low:colors.mid,high=p<colors.midpoint?colors.mid:colors.high,ratio=p<colors.midpoint?p/colors.midpoint:(p-colors.midpoint)/(1-colors.midpoint);color=low.map((x,k)=>Math.round(x+(high[k]-x)*ratio));}
    if(grid.material_response_flags[i]&&((i%grid.width)+Math.floor(i/grid.width))%colors.hatch_period===0)color=color.map(x=>Math.round(x+(255-x)*colors.hatch_opacity));
    const offset=i*4;data.data[offset]=color[0];data.data[offset+1]=color[1];data.data[offset+2]=color[2];data.data[offset+3]=255;
  }
  ctx.putImageData(data,0,0);
  for(const point of state.armorAim?.aim_points||[]){ctx.beginPath();ctx.strokeStyle=point.type==='general_center'?'#ffffff':'#8cd9f0';ctx.lineWidth=state.ui.aim_marker_line_width_px;ctx.arc(point.x,point.y,state.ui.aim_marker_radius_px,0,Math.PI*2);ctx.stroke();}
  canvas.onpointermove=event=>{const rect=canvas.getBoundingClientRect(),x=Math.min(grid.width-1,Math.max(0,Math.floor((event.clientX-rect.left)*grid.width/rect.width))),y=Math.min(grid.height-1,Math.max(0,Math.floor((event.clientY-rect.top)*grid.height/rect.height))),i=y*grid.width+x,p=grid.probabilities[i],s=grid.status[i],angleIndex=state.armorEntry.angles_deg.indexOf(state.armorAngle),flags=grid.material_response_flags[i];
    const statusLabels=['背景','射线上无受保护内部目标','未知几何 / 材料','有条件模型估计'];
    const flagNames=Object.entries(state.probabilityIndex.flag_legend).filter(([bit])=>flags&Number(bit)).map(([,name])=>name);
    $('#pixel-readout').innerHTML=`像素 (${x}, ${y}) · angle_index ${angleIndex} · ray_id ${angleIndex*grid.width*grid.height+i}<br>status=${s} ${statusLabels[s]||'未知状态'} · probability=${Number.isFinite(p)?p.toFixed(state.ui.probability_digits):'NaN / 未计算'}<br>材料 flags=${flags}${flagNames.length?` · ${esc(flagNames.join(' / '))}`:''}`;
  };
}
function renderAim() {
  const el=$('#aim-result');if(!el)return;
  const aim=state.armorAim;
  if(!aim){el.innerHTML=busy('积分完整落点分布…');return;}
  const lower=aim.lower??aim.penetration_lower??aim.expected_lower,upper=aim.upper??aim.penetration_upper??aim.expected_upper;
  el.innerHTML=`<div class="stat-label">受保护目标条件穿透效用区间</div><div class="stat-value positive">${pct(lower)} – ${pct(upper)}</div><div class="prob-meta mt">目标外 / 背景落点 ${pct(aim.miss_mass??aim.outside_mass)}<br>未知区域落点 ${pct(aim.unknown_mass)}<br>无受保护内部目标落点 ${pct(aim.no_protected_target_mass??aim.no_target_mass)}<br>已知区域质量 ${pct(aim.known_mass)}</div><details><summary>积分参数与概率质量守恒</summary><pre class="json-code">${esc(JSON.stringify(aim,null,2))}</pre></details>`;
}

function workerCall(type,payload,context=state.context) {
  if(!state.worker){state.worker=new Worker(new URL('./worker.mjs',import.meta.url),{type:'module'});state.worker.onmessage=event=>{const {id,result,error}=event.data,job=state.pending.get(id);if(!job)return;state.pending.delete(id);if(error)job.reject(new Error(error));else job.resolve(result);};state.worker.onerror=error=>{for(const task of state.pending.values())task.reject(new Error(error.message));state.pending.clear();state.worker?.terminate();state.worker=null;};}
  const id=++state.jobCounter;return new Promise((resolve,reject)=>{state.pending.set(id,{resolve,reject});state.worker.postMessage({id,type,payload,manifest:state.manifest,context,scenario:state.scenario,config:state.config});});
}
async function loadNation(nation) {
  if(!state.nationData.has(nation)){
    const descriptor=state.manifest.nations[nation],data=await fetchJSON(descriptor);
    state.nationData.set(nation,data);
    for(const v of data.vehicles||data){state.baseVehicles.set(v.id,{...v,_detail:true});state.vehicles.set(v.id,{...v,_detail:true});}
  }
  syncIndex();
}
function syncIndex() {
  state.catalog.vehicles=state.catalog.vehicles.map(v=>state.vehicles.get(v.id)||v);
}
async function loadRecs(version=state.version) {
  const key=recommendationKey(version);
  if(state.recommendations.has(key))return;
  if(!defaultRecommendationConditions()||(state.scenarioCustom&&version==='planned')){const result=await workerCall('recommend',{nation:state.nation},{...state.context,version});state.recommendations.set(key,result);return;}
  const path=state.manifest.recommendations?.[`${version}/${state.nation}`];
  if(path){const result=await fetchJSON(path);state.recommendations.set(key,result.recommendation||result);}
}
async function loadVersionOverview() {
  const key=recommendationKey('planned');
  if(state.versionOverviews.has(key))return;
  if(defaultRecommendationConditions()&&!state.scenarioCustom){
    await loadRecs('planned');
    const overview=state.recommendations.get(key)?.version_overview;
    if(overview){state.versionOverviews.set(key,overview);return;}
  }
  const result=await workerCall('version-overview',{nation:state.nation});
  state.versionOverviews.set(key,result);
}
function updateBRPreview(){const slider=$('#br-slider');if(!slider)return;const br=state.brValues[Number(slider.value)];$('#br-value').textContent=fmt(br);slider.setAttribute('aria-valuetext',`BR ${fmt(br)}`);slider.style.setProperty('--fill',`${Number(slider.max)?100*Number(slider.value)/Number(slider.max):0}%`);}
function syncBRControl(){const slider=$('#br-slider');if(!slider)return;slider.max=state.brValues.length-1;slider.value=state.brValues.indexOf(state.br);$('#br-min').textContent=fmt(state.brValues[0]);$('#br-max').textContent=fmt(state.brValues.at(-1));updateBRPreview();}
function updateSelectors() {
  $('#nation-flags').innerHTML=state.catalog.nations.map(n=>`<button class="nation-flag" data-nation="${esc(n.id)}" aria-label="${esc(nationName(n.id))}" aria-pressed="${n.id===state.nation}"><img src="./assets/flags/${esc(n.id)}.svg" alt="" width="30" height="20"><span>${esc(nationName(n.id))}</span></button>`).join('');
  const brs=unique(state.catalog.vehicles.filter(v=>v.nation===state.nation).map(activeBR).filter(finite)).sort((a,b)=>a-b);
  if(!brs.includes(state.br)){const preferred=byId(state.ui.default_vehicle);state.br=preferred?.nation===state.nation&&brs.includes(activeBR(preferred))?activeBR(preferred):brs.at(-1);}
  state.brValues=brs;syncBRControl();
  state.context={...state.context,nation:state.nation,br:state.br,version:state.version};
}
async function switchContext({nation=state.nation,br=state.br,version=state.version}={}) {
  if(state.context.availableIds)state.context.availableIds=state.owned[nation]||[];
  state.nation=nation;state.br=br;state.version=version;state.result=null;state.comparison=null;state.treePage=0;state.matrixPage=0;state.changePage=0;state.analysisGeneration++;
  await loadNation(nation);await applyScenarioToIndex();updateSelectors();await loadRecs();save();render();if(state.view==='versions'){await loadVersionOverview();render();}
}
async function navigate() {
  if(!state.catalog)return;
  const hash=decodeURIComponent(location.hash.slice(1)),[view,id]=hash.split('/');
  if(view==='vehicle'&&id){openVehicle(id);return;}
  state.view=views.some(v=>v[0]===view)?view:state.ui.default_view;
  if(state.view!=='armor'){state.armorRequest?.abort();state.armorRequest=null;}
  $('#research-menu').open=false;$('#menu-toggle').setAttribute('aria-expanded','false');window.scrollTo({top:0,behavior:'instant'});render();
  if(state.view==='armor'){const available=await loadArmor(id);render();if(available&&state.view==='armor')await loadArmorSlice();}
  if(state.view==='audit'&&!state.audit){state.audit=await fetchJSON(state.manifest.audit);render();}
  if(state.view==='overview'){await loadRecs();render();}
  if(state.view==='versions'){await loadVersionOverview();if(!state.impact&&state.manifest.impact)state.impact=await fetchJSON(state.manifest.impact);render();}
}
async function applyScenarioToIndex() {
  // Reuse the same audited scenario implementation as both the worker and offline build.
  const engine=await import('../shared/engine.mjs');
  const needDetails=unique((state.scenario.entries||[]).filter(entry=>!entry.field?.startsWith('br')&&!state.baseVehicles.get(entry.vehicle_id)?._detail).map(entry=>entry.vehicle_id)).filter(id=>state.manifest.vehicle_details[id]);
  await Promise.all(needDetails.map(async id=>{const vehicle=await fetchJSON(state.manifest.vehicle_details[id]);state.baseVehicles.set(id,{...vehicle,_detail:true});}));
  const original=[...state.baseVehicles.values()];
  const transformed=engine.applyScenario({vehicles:original},state.scenario,'planned');
  const list=Array.isArray(transformed)?transformed:transformed.vehicles||transformed.catalog?.vehicles||original;
  state.scenarioApplied=transformed;
  for(const v of list){const baseline=state.baseVehicles.get(v.id);state.vehicles.set(v.id,state.version==='planned'?{...v,_detail:baseline?._detail}:{...baseline,br:{...baseline.br,planned:v.br?.planned}});}
  syncIndex();
}
async function evaluate() {
  let ids=chosenIds();if(!ids.length){ids=selectedCandidate()?.lineup_ids||[];state.lineups[state.nation]=ids;save();}
  if(!ids.length)return toast('请先在我的队列加入载具。');
  const generation=state.analysisGeneration;state.busyCount=(state.busyCount||0)+1;state.busy=true;render();
  try{const result=await workerCall('evaluate',{lineup_ids:ids});if(generation===state.analysisGeneration)state.result=result;}
  finally{state.busyCount--;state.busy=state.busyCount>0;render();}
}
async function scan() {
  const generation=state.analysisGeneration,key=recommendationKey();state.busyCount=(state.busyCount||0)+1;state.busy=true;render();
  try{const result=await workerCall('recommend',{nation:state.nation});if(generation===state.analysisGeneration){state.recommendations.set(key,result);toast('已按当前限制完成本国有效 BR 扫描');}}
  finally{state.busyCount--;state.busy=state.busyCount>0;render();}
}
async function compare() {
  let ids=chosenIds();if(!ids.length){ids=selectedCandidate()?.lineup_ids||[];state.lineups[state.nation]=ids;save();}
  if(!ids.length)return toast('请先建立一支旧版队列。');
  const generation=state.analysisGeneration;state.busyCount=(state.busyCount||0)+1;state.busy=true;render();
  try{const result=await workerCall('compare',{lineup_ids:ids});if(generation===state.analysisGeneration)state.comparison=result;}
  finally{state.busyCount--;state.busy=state.busyCount>0;render();}
}
async function loadArmor(targetId) {
  state.armorError=null;
  if(!state.manifest.probability){if(targetId)toast('当前数据版本暂未提供防护图。');return false;}
  if(!state.probabilityIndex){
    try{
      const index=await fetchJSON(state.manifest.probability);
      if(!Array.isArray(index.entries))throw new Error('防护数据索引格式无效');
      state.probabilityIndex=index;
    }catch(error){state.armorError=error.message;if(targetId)toast('防护资料暂时无法加载，请稍后重试。');return false;}
  }
  const list=state.probabilityIndex.entries;
  if(!list.length){if(targetId)toast('当前数据版本暂未提供防护图。');return false;}
  if(targetId){const candidate=list.find(e=>e.target_id===targetId);if(!candidate){toast('该构型暂未提供防护图。请选择已有研究数据的构型。');return false;}if(state.armorEntry?.id!==candidate.id){state.armorGrid=null;state.armorAim=null;}state.armorEntry=candidate;}
  if(!state.armorEntry)state.armorEntry=list[0];
  if(!state.armorEntry?.angles_deg.includes(state.armorAngle))state.armorAngle=state.armorEntry?.angles_deg[0];
  return true;
}
async function loadArmorSlice() {
  const entry=state.armorEntry;if(!entry)return;
  state.armorRequest?.abort();
  const request=new AbortController();state.armorRequest=request;
  const shard=entry.shards.find(s=>s.angle_deg===state.armorAngle),key=`${entry.id}/${state.armorAngle}`;state.armorKey=key;
  state.armorError=null;state.armorGrid=null;state.armorAim=null;const canvas=$('#armor-canvas');if(canvas){canvas.width=entry.width;canvas.height=entry.height;}renderAim();
  try{
    if(!shard)throw new Error('该采样视角没有可用的概率切片');
    const base=probabilityBaseURL(state.manifest.probability,state.probabilityIndex,import.meta.url);
    const buffer=await fetchBytes(shard,base,{signal:request.signal});
    if(state.armorRequest!==request)return;
    state.armorGrid={...decodeProbability(buffer,entry,state.probabilityIndex.encoding),entry_id:entry.id,angle_deg:shard.angle_deg,metadata:{target_id:entry.target_id,ammo_id:entry.ammo_id,physical_distance_m:entry.physical_distance_m,geometry_version:entry.geometry_version,configuration_version:entry.configuration_version,source_version:entry.source_version,model_version:entry.model_version}};drawArmor();await calculateAim();
  }catch(error){
    if(request.signal.aborted||state.armorRequest!==request)return;
    state.armorError=error.message;render();
  }finally{if(state.armorRequest===request)state.armorRequest=null;}
}
async function calculateAim() {
  if(!state.armorGrid)return;
  const key=state.armorKey,generation=(state.aimGeneration||0)+1;state.aimGeneration=generation;state.armorAim=null;renderAim();
  const result=await workerCall('aim',{grid:state.armorGrid,context:{...state.context,skill:state.armorSkill??state.config.defaults.skill,distance_m:state.armorDistance??state.config.defaults.distance_m,physical_distance_m:state.armorEntry.physical_distance_m}});
  if(state.armorKey===key&&state.aimGeneration===generation){state.armorAim=result;drawArmor();renderAim();}
}
function addVehicle(id) {
  const v=byId(id);if(!v)return;
  if(v.nation!==state.nation)return toast('只能加入当前国家的载具。');
  if(!finite(activeBR(v)))return toast('该载具没有经过核验的陆战历史 BR。');
  const ids=chosenIds();if(ids.includes(id))return toast('队列中已有该载具。');
  if(ids.length>=state.context.slots)return toast('队列槽位已满，请先移除载具或增加可用槽位。');
  state.lineups[state.nation]=[...ids,id];invalidateAnalysis();save();toast(`已加入 ${v.name}`);render();
}
function adoptCandidate(ids,cap) {
  state.lineups[state.nation]=ids;state.br=finite(cap)?cap:Math.max(...ids.map(id=>activeBR(byId(id))).filter(finite));invalidateAnalysis();updateSelectors();save();location.hash='#lineup';render();toast('已采用队列，可继续修改成员和交战条件。');
}
async function changeScenario(scenario,custom=true) {
  if(!scenario||!Array.isArray(scenario.entries))throw new Error('方案必须包含 entries 数组');
  state.scenario=scenario;state.scenarioCustom=custom;state.recommendations.clear();state.versionOverviews.clear();state.result=null;state.comparison=null;state.analysisGeneration++;
  await applyScenarioToIndex();updateSelectors();await loadRecs();render();
  if(state.view==='versions'){await loadVersionOverview();render();}
  if(chosenIds().length)await compare();
}
function editOwned() {
  const ids=state.owned[state.nation]||[];
  openDialog(`<div class="eyebrow">OWNERSHIP / LOCAL</div><h2>管理 ${esc(nationName(state.nation))} 的可用载具</h2><p class="lead">每行一个稳定载具 ID。可从档案复制 ID，或把当前队列加入列表。列表只保存在当前浏览器。</p><textarea id="owned-list" rows="12" style="width:100%;margin-top:20px" placeholder="载具稳定 ID，每行一个">${esc(ids.join('\n'))}</textarea><div class="detail-actions">${button('save-owned','保存拥有列表','primary')}${button('lineup-to-owned','填入当前队列','secondary')}</div>`);
}
async function action(name) {
  switch(name){
    case 'go-lineup':location.hash='#lineup';break;
    case 'go-versions':location.hash='#versions';break;
    case 'go-matrix':location.hash='#matrix';break;
    case 'auto-lineup':{const c=selectedCandidate();if(!c)return toast('当前没有可用推荐，请先执行本地扫描。');adoptCandidate(c.lineup_ids,c.actual_br??c.br);break;}
    case 'evaluate':await evaluate();break;
    case 'scan':await scan();break;
    case 'armor-retry':state.armorError=null;render();{const available=await loadArmor();render();if(available)await loadArmorSlice();}break;
    case 'compare':await compare();break;
    case 'clear-lineup':state.lineups[state.nation]=[];invalidateAnalysis();save();render();break;
    case 'import-lineup':$('#lineup-file').click();break;
    case 'export-lineup':download(`groundops-${state.nation}-lineup.json`,{schema_version:'1.0.0',nation:state.nation,lineup_ids:chosenIds(),context:state.context,snapshot_id:state.catalog.snapshot.id,model_version:state.config.model_version});break;
    case 'export-matrix':if(!state.result)return toast('请先计算队列。');download(`groundops-${state.nation}-matrix.json`,state.result);break;
    case 'import-scenario':$('#scenario-file').click();break;
    case 'export-scenario':download('groundops-scenario.json',state.scenario);break;
    case 'reset-scenario':await changeScenario(structuredClone(state.sourceScenario),false);toast('已恢复采集方案');break;
    case 'add-change':{const id=$('#scenario-vehicle').value,field=$('#scenario-field').value,input=$('#scenario-value').value;if(input.trim()==='')return toast('请输入方案新值。');const value=Number(input);if(!Number.isFinite(value))return toast('方案值必须是有限数值。');const v=state.baseVehicles.get(id),old=get(v,field);const entry={vehicle_id:id,field,old:old??null,new:value,source_row:'local-editor',description:`本地模拟 · ${new Date().toISOString()}`};const scenario={...state.scenario,status:'local_simulation',entries:[...(state.scenario.entries||[]).filter(e=>!(e.vehicle_id===id&&(e.field===field||(e.field?.startsWith('br')&&field.startsWith('br'))))),entry]};await changeScenario(scenario);toast('方案已更新，当前快照保持独立');break;}
    case 'edit-owned':editOwned();break;
    case 'lineup-to-owned':$('#owned-list').value=unique([...(state.owned[state.nation]||[]),...chosenIds()]).join('\n');break;
    case 'save-owned':{const ids=unique($('#owned-list').value.split(/\s+/).filter(Boolean));const invalid=ids.filter(id=>byId(id)?.nation!==state.nation);if(invalid.length)return toast(`ID 不存在或国家不符：${invalid.join(', ')}`);state.owned[state.nation]=ids;if(state.context.availableIds)state.context.availableIds=ids;invalidateAnalysis();save();$('#detail-dialog').close();render();break;}
    case 'model-config':openDialog(`<div class="eyebrow">VERSIONED MODEL CONFIG</div><h2>模型参数 · 全部待校准</h2><p class="lead">经验系数、BR 匹配规则、搜索限制、技能行为和未知先验均来自同一版本化配置。</p><pre class="json-code mt">${esc(JSON.stringify(state.config,null,2))}</pre>`);break;
    case 'download-audit':download('groundops-source-audit.json',state.audit||await fetchJSON(state.manifest.audit));break;
    case 'build-report':{const data=state.manifest.build_report?await fetchJSON(state.manifest.build_report):state.manifest;openDialog(`<h2>可复现构建与资源记录</h2><pre class="json-code mt">${esc(JSON.stringify(data,null,2))}</pre>`);break;}
    case 'probability-report':{const data=state.manifest.probability_report?await fetchJSON(state.manifest.probability_report):await fetchJSON(state.manifest.probability);openDialog(`<h2>概率导出与量化误差</h2><pre class="json-code mt">${esc(JSON.stringify(data,null,2))}</pre>`);break;}
    case 'armor-metadata':if(state.armorEntry)download(`groundops-${state.armorEntry.target_id}-${state.armorEntry.ammo_id}-metadata.json`,state.armorEntry);break;
  }
}

async function handleClick(event) {
  const target=event.target.closest('button,a,[data-vehicle],[data-br]');if(!target)return;
  try{
    if(target.dataset.remove){event.stopPropagation();state.lineups[state.nation]=chosenIds().filter(id=>id!==target.dataset.remove);invalidateAnalysis();save();render();return;}
    if(target.dataset.add){addVehicle(target.dataset.add);return;}
    if(target.dataset.action){await action(target.dataset.action);return;}
    if(target.dataset.vehicle){await openVehicle(target.dataset.vehicle);return;}
    if(target.dataset.pair){openPair(target.dataset.pair);return;}
    if(target.dataset.version){await switchContext({version:target.dataset.version});return;}
    if(target.dataset.nation){await switchContext({nation:target.dataset.nation,br:target.dataset.nationBr===undefined?state.br:Number(target.dataset.nationBr)});window.scrollTo({top:0,behavior:'smooth'});return;}
    if(target.dataset.impact){const [nation,cap]=target.dataset.impact.split('/'),br=Number(cap);const row=state.impact?.impact.filter(r=>r.nation===nation&&r.br===br).sort((a,b)=>(b.previous_score??-Infinity)-(a.previous_score??-Infinity))[0];if(!row)return;await switchContext({nation,br,version:'current'});state.lineups[nation]=row.previous_lineup_ids;save();await compare();window.scrollTo({top:0,behavior:'smooth'});return;}
    if(target.dataset.br){await switchContext({br:Number(target.dataset.br)});return;}
    if(target.dataset.candidate){if(target.dataset.candidateVersion&&target.dataset.candidateVersion!==state.version)await switchContext({version:target.dataset.candidateVersion});adoptCandidate(JSON.parse(target.dataset.candidate),Number(target.dataset.cap));return;}
    if(target.dataset.splitReview){await switchContext({version:'current',br:Number(target.dataset.cap)});state.lineups[state.nation]=JSON.parse(target.dataset.splitReview);save();await compare();$('#lineup-comparison')?.scrollIntoView({behavior:'smooth',block:'start'});return;}
    if(target.dataset.changeFilter){state.changeFilter=target.dataset.changeFilter;state.changePage=0;render();return;}
    if(target.dataset.changePage){state.changePage=Number(target.dataset.changePage);render();return;}
    if(target.dataset.kind){state.kind=target.dataset.kind;render();return;}
    if(target.dataset.page){state.treePage=Number(target.dataset.page);render();return;}
    if(target.dataset.matrixPage){state.matrixPage=Number(target.dataset.matrixPage);render();return;}
    if(target.dataset.angle){state.armorAngle=Number(target.dataset.angle);state.armorGrid=null;state.armorAim=null;render();await loadArmorSlice();return;}
    if(target.dataset.armorTarget){if(await loadArmor(target.dataset.armorTarget)){$('#detail-dialog').close();location.hash='#armor';if(state.view==='armor'){render();await loadArmorSlice();}}return;}
  }catch(error){state.busy=false;render();toast(error.message);console.error(error);}
}
async function handleChange(event) {
  const target=event.target;
  try{
    if(target.id==='br-slider')await switchContext({br:state.brValues[Number(target.value)]});
    else if(target.id==='change-scope'){state.changeScope=target.value;state.changePage=0;render();}
    else if(target.id==='tree-changed'){state.treeChanged=target.checked;render();}
    else if(target.id==='tree-matching'){state.treeMatching=target.checked;render();}
    else if(target.id==='matrix-fold'){state.matrixFold=target.checked;state.matrixPage=0;render();}
    else if(target.id==='matrix-target'){state.matrixTarget=target.value||null;state.matrixPage=0;render();}
    else if(target.id==='slots-input'){if(!target.checkValidity())return target.reportValidity();state.context.slots=Number(target.value);invalidateAnalysis();render();}
    else if(target.id==='distance-input'){if(!target.checkValidity())return target.reportValidity();state.context.distance_m=Number(target.value);state.context.physical_distance_m=Number(target.value);invalidateAnalysis();render();}
    else if(target.id==='skill-input'){state.context.skill=Number(target.value);invalidateAnalysis();render();}
    else if(target.id==='special-input'){state.context.includeSpecial=target.checked;invalidateAnalysis();render();}
    else if(target.dataset.choiceId){const key=target.dataset.choiceType==='ammo'?'ammo_by_vehicle':'loadout_by_vehicle';state.context[key]??={};if(target.value)state.context[key][target.dataset.choiceId]=target.value;else delete state.context[key][target.dataset.choiceId];invalidateAnalysis();render();}
    else if(target.id==='spawn-points'){const value=target.value.trim()===''?null:Number(target.value);if(value!==null&&(!finite(value)||value<0))return toast('重生点必须为非负有限数值或留空');state.context.available_spawn_points=value;invalidateAnalysis();render();}
    else if(target.id==='radar-emitting'){state.context.target_radar_emitting=target.value==='unknown'?null:target.value==='yes';invalidateAnalysis();render();}
    else if(target.id==='engagement-angle'){if(!target.checkValidity())return target.reportValidity();state.context.angle_deg=Number(target.value);invalidateAnalysis();render();}
    else if(target.id==='owned-input'){state.context.availableIds=target.checked?(state.owned[state.nation]||[]):null;invalidateAnalysis();render();}
    else if(target.id==='armor-target'){await loadArmor(target.value);render();await loadArmorSlice();}
    else if(target.id==='armor-ammo'){state.armorEntry=state.probabilityIndex.entries.find(e=>e.id===target.value);if(!state.armorEntry.angles_deg.includes(state.armorAngle))state.armorAngle=state.armorEntry.angles_deg[0];state.armorGrid=null;state.armorAim=null;render();await loadArmorSlice();}
    else if(target.id==='armor-skill'){state.armorSkill=Number(target.value);$('#armor-skill-value').textContent=fmt(state.armorSkill);await calculateAim();}
    else if(target.id==='armor-distance'){if(!target.checkValidity())return target.reportValidity();state.armorDistance=Number(target.value);await calculateAim();}
    else if(target.id==='armor-own-motion'||target.id==='armor-target-motion'){if(!target.checkValidity())return target.reportValidity();state.context[target.id==='armor-own-motion'?'own_motion_mps':'target_motion_mps']=Number(target.value);invalidateAnalysis();await calculateAim();}
    else if(target.id==='armor-gun-control'){state.armorGun=target.value;state.context.gun_control_deg_s=byId(target.value)?.firepower?.traverse_deg_s;invalidateAnalysis();await calculateAim();}
    else if(target.id==='scenario-file'&&target.files[0]){await changeScenario(JSON.parse(await target.files[0].text()));target.value='';toast('已导入模拟方案。');}
    else if(target.id==='lineup-file'&&target.files[0]){const data=JSON.parse(await target.files[0].text());if(!Array.isArray(data.lineup_ids)||!data.nation)throw new Error('队列文件需包含 nation 和 lineup_ids');if(!state.catalog.nations.some(n=>n.id===data.nation))throw new Error('未知国家');const invalid=data.lineup_ids.filter(id=>byId(id)?.nation!==data.nation);if(invalid.length)throw new Error(`载具不存在或国家不符：${invalid.join(', ')}`);await switchContext({nation:data.nation});state.lineups[data.nation]=unique(data.lineup_ids);save();target.value='';render();toast('已导入队列，请核对槽位与当前版本。');}
  }catch(error){toast(error.message);console.error(error);}
}

async function boot() {
  state.manifest=await fetchJSON({url:'./data/manifest.json',gzip_url:'./data/manifest.json.gz',cache:'no-cache'});
  const [ui,config,catalog,scenario]=await Promise.all([fetchJSON(state.manifest.ui),fetchJSON(state.manifest.model),fetchJSON(state.manifest.catalog_index),fetchJSON(state.manifest.scenario)]);
  Object.assign(state,{ui,config,catalog,scenario,sourceScenario:structuredClone(scenario)});
  state.context={...config.defaults};state.version=['current','planned'].includes(state.version)?state.version:config.defaults.version;
  state.nation=catalog.nations.some(n=>n.id===state.nation)?state.nation:catalog.nations.some(n=>n.id===ui.default_nation)?ui.default_nation:catalog.nations[0].id;
  const route=new URLSearchParams(location.search);
  if(catalog.nations.some(n=>n.id===route.get('nation')))state.nation=route.get('nation');
  if(['current','planned'].includes(route.get('version')))state.version=route.get('version');
  if(route.has('br')&&Number.isFinite(Number(route.get('br'))))state.br=Number(route.get('br'));
  for(const v of catalog.vehicles){state.vehicles.set(v.id,v);state.baseVehicles.set(v.id,v);}
  const navLink=([id,label])=>`<a href="#${id}" data-view="${id}">${icon(id)}<span>${label}</span></a>`;
  $('#navigation').innerHTML=`<div class="primary-nav">${views.slice(0,2).map(navLink).join('')}</div><details class="tools-menu" id="research-menu"><summary>研究工具 <span aria-hidden="true">⌄</span></summary><div class="tools-links">${views.slice(2).map(navLink).join('')}</div></details>`;
  $('#data-state').textContent=`${catalog.vehicles.length.toLocaleString('zh-CN')} 辆 · 官方目录快照`;
  $('#sidebar-build').textContent=`SNAPSHOT ${catalog.snapshot.id}\nMODEL ${config.model_version}`;
  await loadNation(state.nation);await applyScenarioToIndex();updateSelectors();await loadRecs();
  document.addEventListener('click',handleClick);document.addEventListener('change',handleChange);
  document.addEventListener('keydown',event=>{if(event.key==='/'&&!['INPUT','TEXTAREA','SELECT'].includes(event.target.tagName)){event.preventDefault();$('#global-search').focus();}if((event.key==='Enter'||event.key===' ')&&event.target.matches('article[data-vehicle]')){event.preventDefault();openVehicle(event.target.dataset.vehicle);}});
  document.addEventListener('input',event=>{if(event.target.id==='br-slider')updateBRPreview();if(['global-search','tree-search','lineup-search'].includes(event.target.id)){clearTimeout(boot.searchTimer);const value=event.target.value,id=event.target.id;boot.searchTimer=setTimeout(()=>{state.query=value;state.treePage=0;if(id==='global-search'){if(state.view!=='tree')location.hash='#tree';else render();}else{const cursor=event.target.selectionStart;render();const input=$(`#${id}`);if(input){input.focus();input.setSelectionRange(cursor,cursor);}}},ui.search_debounce_ms);}if(event.target.id==='armor-skill')$('#armor-skill-value').textContent=fmt(Number(event.target.value));});
  $('#menu-toggle').onclick=()=>{const menu=$('#research-menu');menu.open=!menu.open;$('#menu-toggle').setAttribute('aria-expanded',String(menu.open));};
  document.addEventListener('click',event=>{if(!event.target.closest('#research-menu,#menu-toggle')){$('#research-menu').open=false;$('#menu-toggle').setAttribute('aria-expanded','false');}});
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&$('#research-menu').open){$('#research-menu').open=false;$('#menu-toggle').setAttribute('aria-expanded','false');(window.matchMedia('(max-width: 650px)').matches?$('#menu-toggle'):$('#research-menu summary')).focus();}});
  $('#open-method').onclick=()=>{location.hash='#audit';};$('#settings-button').onclick=()=>action('model-config');
  $('.dialog-close').onclick=()=>$('#detail-dialog').close();
  $('#detail-dialog').addEventListener('click',event=>{if(event.target===$('#detail-dialog')){const r=event.target.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)event.target.close();}});
  document.addEventListener('error',event=>{if(event.target.tagName==='IMG'){event.target.replaceWith(Object.assign(document.createElement('span'),{innerHTML:silhouette(event.target.dataset.kind)}));}},true);
  window.addEventListener('hashchange',()=>navigate().catch(error=>toast(error.message)));
  await navigate();
  window.__GROUNDOPS__={snapshot:catalog.snapshot.id,model:config.model_version,ready:true,getState:()=>({view:state.view,nation:state.nation,br:state.br,version:state.version,lineup:chosenIds(),busy:state.busy,hasResult:!!state.result,hasComparison:!!state.comparison}),getDiagnostics:()=>({context:{...state.context},lineup:state.result?{actual_br:state.result.actual_br,valid:state.result.valid,score:state.result.score,pairs:state.result.matrix.length,reverse_pairs:state.result.reverse_matrix.length,invalid:state.result.invalid}:null,comparison:state.comparison?{old_br:state.comparison.before.actual_br,new_br:state.comparison.after_fixed.actual_br,announcements:state.comparison.announcements,rejected:state.comparison.rejected}:null,aim:state.armorAim,scenario:state.scenarioApplied?{applied:state.scenarioApplied.applied.length,rejected:state.scenarioApplied.rejected.length}:null})};
}
boot().catch(error=>{$('#workspace').innerHTML=`<div class="error-block"><h1>数据未能装载</h1><p>${esc(error.message)}</p><p>请刷新页面后重试，或稍后再访问。</p></div>`;console.error(error);});
