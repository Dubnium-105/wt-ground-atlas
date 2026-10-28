/** End-to-end acceptance on ordinary HTTP with all external services blocked.
 * Playwright is a development-only test dependency, never required by the site. */
import {createRequire} from 'node:module';
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
const require=createRequire(import.meta.url);
const {chromium}=require('playwright');
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const qa=JSON.parse(await fs.readFile(path.join(root,'config/qa.json'),'utf8'));
const config=JSON.parse(await fs.readFile(path.join(root,'config/model.json'),'utf8'));
const catalog=JSON.parse(await fs.readFile(path.join(root,'data/snapshots/catalog.json'),'utf8'));
const base=process.env.GROUNDOPS_TEST_URL||qa.base_url;
const screenshots=path.join(root,qa.screenshot_directory);await fs.mkdir(screenshots,{recursive:true});
const results=[],requests=[],failures=[],pageErrors=[];
const browser=await chromium.launch({headless:true,executablePath:process.env.GROUNDOPS_CHROMIUM||undefined,args:['--no-sandbox']});
const browserSession=await browser.newBrowserCDPSession();
let peakAggregateRSSKiB=null,memorySamples=0,memorySampling=false;
async function sampleMemory(){
  if(memorySampling)return;memorySampling=true;
  try{const info=await browserSession.send('SystemInfo.getProcessInfo');const readings=await Promise.all(info.processInfo.map(async process=>{try{const content=await fs.readFile(`/proc/${process.id}/status`,'utf8');return Number(content.match(/^VmRSS:\s+(\d+)\s+kB/m)?.[1]||0);}catch{return 0;}}));const rss=readings.reduce((sum,value)=>sum+value,0);if(rss){peakAggregateRSSKiB=Math.max(peakAggregateRSSKiB??0,rss);memorySamples++;}}
  catch{}finally{memorySampling=false;}
}
const memoryTimer=setInterval(sampleMemory,qa.memory_sample_interval_ms);
const context=await browser.newContext({viewport:qa.desktop,deviceScaleFactor:1});
await context.route('https://**/*',route=>route.abort());
const page=await context.newPage();page.setDefaultTimeout(qa.timeout_ms);
page.on('pageerror',error=>pageErrors.push(error.message));
page.on('request',request=>requests.push({url:request.url(),method:request.method(),kind:request.resourceType()}));
page.on('response',response=>{if(response.status()>=400&&response.url().startsWith(base))failures.push({url:response.url(),status:response.status()});});
const run=async(name,task)=>{const start=performance.now();try{await task();results.push({name,status:'passed',elapsed_ms:performance.now()-start});console.log(`PASS ${name}`);}catch(error){results.push({name,status:'failed',message:error.message,elapsed_ms:performance.now()-start});throw error;}};
let firstLoadResources,firstLoadNavigation,firstLoadMemory,initial,cachedFilterPaintMs;
const lineupKey=ids=>[...ids].sort().join('|');
async function expectedTop(nation,version){const rec=JSON.parse(await fs.readFile(path.join(root,`web/data/recommendations/${nation}.${version}.json`),'utf8'));const rows=rec.candidates.flatMap(c=>[c,...c.alternatives_at_br]).sort((a,b)=>b.score-a.score||b.coverage-a.coverage||a.actual_br-b.actual_br||lineupKey(a.lineup_ids).localeCompare(lineupKey(b.lineup_ids)));return [...new Map(rows.filter(c=>c.valid&&Number.isFinite(c.score)).map(c=>[lineupKey(c.lineup_ids),c])).values()].sort((a,b)=>b.score-a.score||b.coverage-a.coverage||a.actual_br-b.actual_br||lineupKey(a.lineup_ids).localeCompare(lineupKey(b.lineup_ids))).slice(0,3);}
async function navigateTo(view){
  const link=page.locator(`#navigation [data-view="${view}"]`);
  if(!await link.isVisible()){
    if(await page.locator('#menu-toggle').isVisible())await page.locator('#menu-toggle').click();
    else await page.locator('#research-menu > summary').click();
  }
  await link.click();
  await page.waitForFunction(value=>window.__GROUNDOPS__.getState().view===value,view);
}

try{
  await run('AT12: static boot with external services unavailable',async()=>{
    await page.goto(base,{waitUntil:'networkidle'});await page.waitForFunction(()=>window.__GROUNDOPS__?.ready);
    initial=await page.evaluate(()=>window.__GROUNDOPS__.getState());
    assert.equal(initial.version,config.defaults.version);
    assert.equal(config.defaults.slots,10);
    assert.equal(await page.locator('#br-slider').count(),0);
    const expected=await expectedTop(initial.nation,initial.version);
    const shown=await page.locator('.lineup-option [data-candidate]').evaluateAll(els=>els.map(el=>JSON.parse(el.dataset.candidate)));
    assert.deepEqual(shown.map(lineupKey),expected.map(c=>lineupKey(c.lineup_ids)));
    assert.ok(shown.every(ids=>ids.length===10));
    assert.ok((await page.locator('#workspace h1').innerText()).includes('队列推荐'));
    assert.equal(requests.filter(r=>/\.(grp|npz)(?:$|\?)/.test(r.url)).length,0);
    assert.equal(requests.filter(r=>r.url.includes('/api/')).length,0);
    assert.equal(requests.filter(r=>r.url.includes('/probability/shards/')).length,0);
    assert.equal(requests.filter(r=>r.url.includes('/target-layers-')).length,0);
    firstLoadResources=await page.evaluate(()=>performance.getEntriesByType('resource').map(r=>({name:r.name,transferSize:r.transferSize,encodedBodySize:r.encodedBodySize,duration:r.duration})));
    firstLoadNavigation=await page.evaluate(()=>performance.getEntriesByType('navigation').map(r=>({name:r.name,transferSize:r.transferSize,encodedBodySize:r.encodedBodySize,duration:r.duration})));
    firstLoadMemory=await page.evaluate(()=>performance.memory?{usedJSHeapSize:performance.memory.usedJSHeapSize,totalJSHeapSize:performance.memory.totalJSHeapSize}:null);
    await page.screenshot({path:path.join(screenshots,'overview-desktop.png'),fullPage:false});
  });
  await run('Primary navigation, scoped version filters and pagination',async()=>{
    assert.equal(await page.locator('.primary-nav a').count(),2);
    assert.equal(await page.locator('.lineup-option').count(),3);
    assert.equal(await page.locator('.lineup-option [data-candidate]').count(),3);
    const choices=await page.locator('.lineup-option [data-candidate]').evaluateAll(els=>els.map(el=>JSON.parse(el.dataset.candidate).sort().join('|')));
    assert.equal(new Set(choices).size,choices.length);
    assert.equal(await page.locator('#nation-flags button').count(),10);
    assert.equal(await page.locator('#nation-flags [aria-pressed="true"]').count(),1);
    assert.equal(await page.locator('.disclosure[open]').count(),0);
    await navigateTo('versions');
    await page.waitForSelector('#planned-top-lineups .lineup-option');
    assert.equal(await page.locator('#planned-top-lineups .lineup-option').count(),3);
    assert.equal(await page.locator('#split-top-lineups .lineup-option').count(),3);
    assert.equal(await page.locator('#br-slider').count(),0);
    const ranked=await expectedTop(initial.nation,'planned');
    const actual=await page.locator('#planned-top-lineups [data-candidate]').evaluateAll(els=>els.map(el=>JSON.parse(el.dataset.candidate)));
    assert.deepEqual(actual.map(lineupKey),ranked.map(c=>lineupKey(c.lineup_ids)));
    const splitScores=await page.locator('#split-top-lineups .lineup-option').evaluateAll(els=>els.map(el=>Number(el.dataset.score)));
    assert.deepEqual(splitScores,[...splitScores].sort((a,b)=>b-a));
    await page.locator('#split-top-lineups').screenshot({path:path.join(screenshots,'split-rankings-desktop.png')});
    assert.equal(await page.locator('#change-scope').inputValue(),'nation');
    assert.ok(await page.locator('.change-row').count()<=20);
    await page.screenshot({path:path.join(screenshots,'versions-overview-desktop.png'),fullPage:false});
    await page.locator('[data-change-filter="up"]').click();
    const rows=await page.locator('.change-row .br-transition').evaluateAll(elements=>elements.map(el=>[Number(el.children[0].textContent),Number(el.children[2].textContent)]));
    assert.ok(rows.length>0);assert.ok(rows.every(([old,next])=>next>old));
    await page.locator('[data-change-filter="down"]').click();
    const downs=await page.locator('.change-row .br-transition').evaluateAll(elements=>elements.map(el=>[Number(el.children[0].textContent),Number(el.children[2].textContent)]));
    assert.ok(downs.every(([old,next])=>next<old));
    await page.locator('[data-change-filter="all"]').click();
    await page.locator('#change-scope').selectOption('all');
    const first=await page.locator('.changed-vehicle').first().getAttribute('data-vehicle');
    await page.locator('[data-change-page="1"]').click();
    assert.notEqual(await page.locator('.changed-vehicle').first().getAttribute('data-vehicle'),first);
    await page.locator('.changed-vehicle').first().click();
    await page.waitForSelector('#detail-dialog[open]');await page.locator('.dialog-close').click();
    await page.locator('#change-scope').selectOption('nation');
    await page.setViewportSize(qa.mobile);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
    await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));
    await page.screenshot({path:path.join(screenshots,'versions-mobile.png'),fullPage:false});
    await page.locator('#menu-toggle').click();
    assert.equal(await page.locator('#research-menu').evaluate(el=>el.open),true);
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#menu-toggle').evaluate(el=>el===document.activeElement),true);
    assert.equal(await page.locator('#research-menu').evaluate(el=>el.open),false);
    await navigateTo('tree');
    assert.equal(await page.locator('#research-menu').evaluate(el=>el.open),false);
    await page.setViewportSize(qa.desktop);await navigateTo('overview');
    await page.locator('[data-version="planned"]').click();
    await page.waitForFunction(()=>window.__GROUNDOPS__.getState().version==='planned');
    await page.waitForFunction(()=>document.querySelector('.selection-context')?.textContent.includes('模拟'));
    await page.locator('[data-version="current"]').click();
    await page.waitForFunction(()=>document.querySelector('.selection-context')?.textContent.includes('当前'));
  });
  await run('Flag controls, custom-only BR cap and nation-wide top three',async()=>{
    await page.locator('#nation-flags [data-nation="china"]').click();
    await page.waitForFunction(()=>window.__GROUNDOPS__.getState().nation==='china'&&document.querySelector('.selection-context')?.textContent.includes('中国'));
    assert.equal(await page.locator('#nation-flags [data-nation="china"]').getAttribute('aria-pressed'),'true');
    const original=await page.locator('.lineup-option [data-candidate]').evaluateAll(els=>els.map(el=>el.dataset.candidate));
    await navigateTo('lineup');
    assert.equal(await page.locator('#slots-input').inputValue(),'10');
    assert.equal(await page.locator('#contextbar #br-slider').count(),0);
    const old=await page.locator('#br-slider').inputValue();
    const next=Math.max(0,Number(old)-1);
    await page.locator('#br-slider').fill(String(next));await page.locator('#br-slider').dispatchEvent('change');
    await page.waitForFunction(()=>window.__GROUNDOPS__.getState().br===Number(document.querySelector('#br-value').textContent));
    assert.equal(await page.locator('#br-slider').getAttribute('aria-valuetext'),`BR ${await page.locator('#br-value').innerText()}`);
    await navigateTo('overview');
    assert.equal(await page.locator('#br-slider').count(),0);
    assert.deepEqual(await page.locator('.lineup-option [data-candidate]').evaluateAll(els=>els.map(el=>el.dataset.candidate)),original,'custom BR must not filter homepage rankings');
    const chosen=await page.locator('.lineup-option [data-candidate]').nth(1).getAttribute('data-candidate');
    await page.locator('.lineup-option [data-candidate]').nth(1).click();
    await page.waitForFunction(()=>window.__GROUNDOPS__.getState().view==='lineup');
    assert.deepEqual((await page.evaluate(()=>window.__GROUNDOPS__.getState())).lineup,JSON.parse(chosen));
    await navigateTo('overview');
    await page.locator('#nation-flags [data-nation="ussr"]').click();
    await page.waitForFunction(()=>window.__GROUNDOPS__.getState().nation==='ussr'&&document.querySelector('.selection-context')?.textContent.includes('苏联'));
  });
  await run('New-version adoption and split review keep their own version and members',async()=>{
    await navigateTo('versions');await page.waitForSelector('#planned-top-lineups [data-candidate]');
    const planned=JSON.parse(await page.locator('#planned-top-lineups [data-candidate]').first().getAttribute('data-candidate'));
    await page.locator('#planned-top-lineups [data-candidate]').first().click();
    await page.waitForFunction(()=>window.__GROUNDOPS__.getState().view==='lineup'&&window.__GROUNDOPS__.getState().version==='planned');
    assert.deepEqual((await page.evaluate(()=>window.__GROUNDOPS__.getState())).lineup,planned);
    await navigateTo('versions');await page.waitForSelector('#split-top-lineups [data-split-review]');
    const split=JSON.parse(await page.locator('#split-top-lineups [data-split-review]').first().getAttribute('data-split-review'));
    await page.locator('#split-top-lineups [data-split-review]').first().click();
    await page.waitForFunction(()=>window.__GROUNDOPS__.getState().hasComparison&&!window.__GROUNDOPS__.getState().busy);
    const current=await page.evaluate(()=>window.__GROUNDOPS__.getState());
    assert.equal(current.version,'current');assert.deepEqual(current.lineup,split);
    assert.ok((await page.evaluate(()=>window.__GROUNDOPS__.getDiagnostics())).comparison.announcements.some(x=>x.type==='lineup_split'));
    await navigateTo('overview');
  });
  await run('Manual lineup uses actual member BR and independent bidirectional pairs',async()=>{
    await page.locator('.lineup-option [data-candidate]').first().click();
    await page.waitForFunction(()=>window.__GROUNDOPS__.getState().view==='lineup');
    await page.locator('button[data-action="evaluate"]').click();
    await page.waitForFunction(()=>window.__GROUNDOPS__.getState().hasResult&&!window.__GROUNDOPS__.getState().busy);
    const data=await page.evaluate(()=>({state:window.__GROUNDOPS__.getState(),diag:window.__GROUNDOPS__.getDiagnostics()}));
    assert.equal(data.diag.lineup.valid,true);assert.ok(data.diag.lineup.pairs>0);assert.equal(data.diag.lineup.pairs,data.diag.lineup.reverse_pairs);
    const actual=Math.max(...data.state.lineup.map(id=>catalog.vehicles.find(v=>v.id===id).br.current));assert.equal(data.diag.lineup.actual_br,actual);
    const offline=JSON.parse(await fs.readFile(path.join(root,`web/data/recommendations/${data.state.nation}.current.json`),'utf8'));
    const match=offline.candidates.flatMap(c=>[c,...c.alternatives_at_br]).find(c=>[...c.lineup_ids].sort().join('|')===[...data.state.lineup].sort().join('|'));
    assert.ok(match);assert.ok(Math.abs(data.diag.lineup.score-match.score)<1e-10,'browser worker must use the same own-ammo layers as offline scoring');
    assert.ok(requests.some(r=>r.url.includes('/target-layers-0.')));
    assert.ok(!requests.some(r=>/target-layers-(?:15|30|90)\./.test(r.url)));
    await page.screenshot({path:path.join(screenshots,'lineup-desktop.png'),fullPage:false});
  });
  await run('Matrix drilldown retains ammunition, uncertainty and reverse direction',async()=>{
    await navigateTo('matrix');
    await page.locator('button[data-pair]').first().click();
    await page.waitForSelector('#detail-dialog[open]');
    const text=await page.locator('#dialog-content').innerText();
    const pair=JSON.parse(await page.locator('#dialog-content .json-code').first().innerText());
    if(pair.ammo_id)assert.ok(catalog.vehicles.find(v=>v.id===pair.attacker_id).ammo.some(a=>a.id===pair.ammo_id));
    assert.ok(text.includes('ammo_id'));assert.ok(text.includes('aggregation_weight'));assert.ok(text.includes('独立计算的反向证据'));
    await page.locator('.dialog-close').click();
    await page.screenshot({path:path.join(screenshots,'matrix-desktop.png'),fullPage:false});
  });
  await run('AT01/AT11: imported scenario changes fixed member cost and retains baseline',async()=>{
    const current=await page.evaluate(()=>window.__GROUNDOPS__.getState());
    const v=current.lineup.map(id=>catalog.vehicles.find(v=>v.id===id)).sort((a,b)=>b.br.current-a.br.current)[0];
    const scenario={status:'acceptance_fixture_simulation',entries:[{vehicle_id:v.id,field:'br.current',old:v.br.current,new:v.br.current+config.matching.max_br_spread,source_row:'browser acceptance fixture'}],audit:[]};
    await navigateTo('versions');
    await page.locator('#scenario-file').setInputFiles({name:'acceptance-scenario.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(scenario))});
    await page.waitForFunction(()=>window.__GROUNDOPS__.getState().hasComparison&&!window.__GROUNDOPS__.getState().busy);
    const d=await page.evaluate(()=>window.__GROUNDOPS__.getDiagnostics());assert.equal(d.comparison.old_br,v.br.current);assert.equal(d.comparison.new_br,v.br.current+config.matching.max_br_spread);assert.ok(d.comparison.announcements.some(a=>a.type==='lineup_split'));
    await page.screenshot({path:path.join(screenshots,'versions-desktop.png'),fullPage:false});
  });
  await run('Changed engagement conditions invalidate both lineup and version results',async()=>{
    await navigateTo('lineup');
    await page.waitForFunction(()=>window.__GROUNDOPS__.getState().view==='lineup');
    await page.locator('summary').filter({hasText:'精确弹药、合法挂载与出击条件'}).click();
    await page.locator('#radar-emitting').selectOption('no');
    const state=await page.evaluate(()=>window.__GROUNDOPS__.getState());
    assert.equal(state.hasComparison,false);assert.equal(state.hasResult,false);
    await page.locator('summary').filter({hasText:'精确弹药、合法挂载与出击条件'}).click();
    await page.locator('#radar-emitting').selectOption('unknown');
  });
  await run('AT08/AT09/AT10: lazy heatmap, full probability mass, unchanged physical shot data',async()=>{
    await navigateTo('armor');
    await page.waitForFunction(()=>window.__GROUNDOPS__.getDiagnostics().aim!==null);
    const before=await page.evaluate(()=>window.__GROUNDOPS__.getDiagnostics().aim);
    const mass=before.known_mass+before.unknown_mass+before.no_protected_target_mass+before.miss_mass;
    assert.ok(Math.abs(mass-1)<qa.probability_mass_tolerance);
    const physical=before.physical_distance_m;
    const binRequests=requests.filter(r=>r.url.includes('/probability/shards/')&&r.url.endsWith('.bin.gz')).length;
    assert.ok(binRequests>0);
    const next=Math.min(config.ui.distance_m.max,before.aiming_distance_m+config.ui.distance_m.step);
    await page.locator('#armor-distance').fill(String(next));await page.locator('#armor-distance').dispatchEvent('change');
    await page.waitForFunction(value=>window.__GROUNDOPS__.getDiagnostics().aim?.aiming_distance_m===value,next);
    const after=await page.evaluate(()=>window.__GROUNDOPS__.getDiagnostics().aim);assert.equal(after.physical_distance_m,physical);assert.equal(after.physical_probability_unchanged,true);
    assert.equal(requests.filter(r=>r.url.includes('/probability/shards/')&&r.url.endsWith('.bin.gz')).length,binRequests);
    assert.equal(await page.locator('#armor-canvas').evaluate(c=>c.width*c.height),before.width*before.height);
    await page.screenshot({path:path.join(screenshots,'armor-desktop.png'),fullPage:false});
  });
  await run('Tree source fields and responsive mobile navigation',async()=>{
    await navigateTo('tree');
    await page.waitForFunction(()=>window.__GROUNDOPS__.getState().view==='tree');
    await page.waitForSelector('article[data-vehicle]');
    assert.ok(await page.locator('[data-vehicle]').count()>0);
    cachedFilterPaintMs=await page.evaluate(async()=>{const target=document.querySelector('[data-kind="ground"]');const start=performance.now();target.click();await new Promise(requestAnimationFrame);return performance.now()-start;});
    await page.locator('article[data-vehicle]').first().click();
    assert.ok((await page.locator('#dialog-content').innerText()).includes('物理厚度不代表全车 RHAe'));
    await page.locator('.dialog-close').click();
    await page.setViewportSize(qa.mobile);await navigateTo('overview');
    await page.waitForFunction(()=>window.__GROUNDOPS__.getState().view==='overview');
    assert.equal(await page.locator('#research-menu').evaluate(el=>el.open),false);
    assert.equal(await page.locator('.primary-nav a:visible').count(),2);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
    assert.equal(await page.locator('.lineup-option').count(),3);
    assert.equal(await page.locator('#nation-flags [aria-pressed="true"]').count(),1);
    await page.reload({waitUntil:'networkidle'});await page.waitForFunction(()=>window.__GROUNDOPS__?.ready);
    await page.screenshot({path:path.join(screenshots,'overview-mobile.png'),fullPage:false});
  });
  await run('No browser runtime exceptions or missing local assets',async()=>{assert.deepEqual(pageErrors,[]);assert.deepEqual(failures,[]);});
}catch(error){await page.screenshot({path:path.join(screenshots,'failure.png'),fullPage:false});console.error(error.stack);process.exitCode=1;}
finally{
  clearInterval(memoryTimer);await sampleMemory();
  const report={browser:await browser.version(),viewport:qa.desktop,mobile_viewport:qa.mobile,external_services_blocked:true,tests:results,page_errors:pageErrors,local_http_failures:failures,first_load_resources:firstLoadResources,first_load_navigation:firstLoadNavigation,first_load_memory:firstLoadMemory,memory:{peak_sampled_aggregate_RSS_KiB:peakAggregateRSSKiB,samples:memorySamples,interval_ms:qa.memory_sample_interval_ms,method:'CDP process list + /proc VmRSS; sum includes shared pages and is a sampled upper estimate, not exact unique physical memory.'},cached_filter_to_next_frame_ms:cachedFilterPaintMs,total_local_requests:requests.filter(r=>r.url.startsWith(base)).length,raw_geometry_requests:requests.filter(r=>/\.(grp|npz)(?:$|\?)/.test(r.url)),application_api_requests:requests.filter(r=>r.url.includes('/api/')),limitations:['Chromium headless on this build host; not a universal latency or device-memory guarantee.','External thumbnails deliberately unavailable; core views remain usable.']};
  await fs.writeFile(path.join(root,'reports/browser-e2e.json'),JSON.stringify(report,null,2));await browser.close();
}
