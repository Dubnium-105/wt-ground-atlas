/** End-to-end static publication from synthetic V2 data; never uploads game data. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const work=await fs.mkdtemp(path.join(os.tmpdir(),'wt-forecast-browser-'));
const python=process.env.PYTHON || 'python';
const setup=spawnSync(python,['-c',`
import sys,json
from pathlib import Path
sys.path.insert(0, str(Path('tests').resolve()))
from test_forecast import fixture, settings
from site_fixture import create_fixture,build_recommendations,load_script
p=Path(sys.argv[1]);catalog,_,_=fixture(p)
catalog.update(schema_version='1.0.0',nations=[{'id':n,'label':'Fixture '+n} for n in ('a','b')])
(p/'catalog.json').write_text(json.dumps(catalog))
c=settings();c['mission_policy']=json.loads(Path('config/forecast.json').read_text(encoding='utf-8'))['mission_policy'];c['search']['global_limit']=1;c['skill_profiles']={k:{**v,'nodes':1,'sd':0} for k,v in c['skill_profiles'].items()}
(p/'config.json').write_text(json.dumps(c))
(p/'patch.json').write_text(json.dumps({'id':'synthetic-patch','status':'proposed','entries':[{'vehicle_id':'synthetic_a_0','field':'br','old':9.3,'new':9.7}]}))
research=create_fixture(p/'research-project')
(research/'data/snapshots/catalog.json').write_bytes((p/'catalog.json').read_bytes())
(research/'data/snapshots/changes.json').write_bytes((p/'patch.json').read_bytes())
build_recommendations(research);load_script('build_web').build(research)
`,work],{cwd:root,encoding:'utf8'});
assert.equal(setup.status,0,setup.stderr);
const build=spawnSync(python,['build.py','--forecast','--all-profiles','--research-site',path.join(work,'research-project/dist/site'),'--catalog',path.join(work,'catalog.json'),'--ammunition',path.join(work,'ammo.json'),'--atlas',path.join(work,'v2'),'--config',path.join(work,'config.json'),'--scenario',path.join(work,'patch.json'),'--cache',path.join(work,'cache.sqlite'),'--index',path.join(work,'index.sqlite'),'--forecast-output',path.join(work,'forecast'),'--output',path.join(work,'site')],{cwd:root,encoding:'utf8'});
assert.equal(build.status,0,build.stderr);
// Give the full-mode fixture a real-shaped but synthetic missile bound. The
// corresponding NPZ remains a conditional impact map, not delivery proof.
const missileFixture=spawnSync(python,['-c',`
import json,sys
from pathlib import Path
sys.path.insert(0, str(Path('backend').resolve()))
from wt_atlas.forecast.data import digest
p=Path(sys.argv[1]); catalog_path=p/'catalog.json'; catalog=json.loads(catalog_path.read_text())
catalog['snapshot']['client_source']['version']='2.59.0.92'
for vehicle in catalog['vehicles']: vehicle['source_attributes']['client']['source_version']='2.59.0.92'
catalog_path.write_text(json.dumps(catalog))
path=p/'ammo.json'; profiles=json.loads(path.read_text())
for profile in profiles: profile['provenance']['client_version']='2.59.0.92'
profile=profiles[0]; profile['provenance']['chemical_energy']={'source_min_distance_m':30.,'source_max_distance_m':4000.,'flight_and_activation_modeled':False}
path.write_text(json.dumps(profiles))
by_name={profile['name']:profile for profile in profiles}
for metadata in (p/'v2/arrays').glob('*.json'):
    row=json.loads(metadata.read_text())
    row['source_version']='2.59.0.92'; row['ammo_profile_sha256']=digest(by_name[row['ammo_id']]); metadata.write_text(json.dumps(row))
`,work],{cwd:root,encoding:'utf8'});
assert.equal(missileFixture.status,0,missileFixture.stderr);
// Exercise the product's actual default configuration without substituting the
// legacy fixture config. Too few source vehicles must show diagnostics, not ranks.
const fullBuild=spawnSync(python,['build.py','--forecast','--catalog',path.join(work,'catalog.json'),'--ammunition',path.join(work,'ammo.json'),'--atlas',path.join(work,'v2'),'--cache',path.join(work,'full-cache.sqlite'),'--index',path.join(work,'full-index.sqlite'),'--forecast-output',path.join(work,'full-forecast'),'--output',path.join(work,'full-site')],{cwd:root,encoding:'utf8'});
assert.equal(fullBuild.status,0,fullBuild.stderr);
const site=path.join(work,'site');
const server=http.createServer(async(req,res)=>{
  try{
    const pathname=decodeURIComponent(new URL(req.url,'http://localhost').pathname);
    const base=pathname.startsWith('/full/')?path.join(work,'full-site'):site;
    const relative=pathname.startsWith('/sub/')?pathname.slice(5):pathname.startsWith('/full/')?pathname.slice(6):pathname.slice(1);
    const filename=path.resolve(base,relative||'index.html');
    if(!filename.startsWith(base+path.sep)){res.writeHead(403);res.end();return;}
    const data=await fs.readFile(filename);
    res.setHeader('Content-Type',({'.html':'text/html','.mjs':'text/javascript','.css':'text/css','.json':'application/json'})[path.extname(filename)]||'application/octet-stream');res.end(data);
  }catch{res.writeHead(404);res.end();}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{})});
try{
  const page=await browser.newPage({viewport:{width:1280,height:900}}),errors=[],requests=[];page.on('request',r=>requests.push(r.url()));
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/sub/index.html`);
  await page.waitForSelector('html[data-ready="true"]');
  assert.equal(await page.locator('#version').inputValue(),'planned');
  assert.match(await page.locator('#status').innerText(),/任务记录可留空：使用默认假设/);
  assert.match(await page.locator('#status').innerText(),/模型保持未校准/);
  assert.ok(await page.locator('.card').count()>0,'automatic recommendations require no manual lineup');
  assert.equal(await page.locator('.card:not(.movement)').count(),1,'global limit is applied only to representatives');
  assert.equal(requests.some(url=>url.includes('.view.')),false,'independent views load on demand');
  await page.locator('#view').selectOption('br');
  await page.waitForFunction(()=>document.querySelectorAll('#rankings .card').length===2);
  assert.deepEqual(new Set(await page.locator('#rankings .eyebrow').allTextContents().then(x=>x.map(s=>s.split('BR ')[1]))),new Set(['9.3','9.7']));
  await page.locator('#view').selectOption('nation');
  await page.waitForFunction(()=>document.querySelectorAll('#rankings .card').length===2&&[...document.querySelectorAll('#rankings .eyebrow')].some(x=>x.textContent.includes(' · b · ')));
  await page.locator('#view').selectOption('global');
  await page.waitForFunction(()=>document.querySelectorAll('#rankings .card').length===1);
  await page.locator('#nation').selectOption('a');
  await page.waitForFunction(()=>[...document.querySelectorAll('.card .eyebrow')].every(x=>x.textContent.includes(' · a · ')));
  await page.locator('#version').selectOption('current');
  await page.waitForFunction(()=>document.querySelector('#rankings h2').textContent.startsWith('当前'));
  await page.setViewportSize({width:390,height:844});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),'mobile horizontal overflow');
  assert.equal(requests.some(url=>/\.(npz|grp)(?:$|\?)/.test(url)),false);
  await Promise.all([page.waitForURL('**/profiles/expert-ground/index.html**'),page.locator('#skill').selectOption('expert')]);await page.waitForSelector('html[data-ready="true"]');assert.equal(await page.locator('#skill').inputValue(),'expert');
  await Promise.all([page.waitForURL('**/profiles/expert-balanced/index.html**'),page.locator('#preference').selectOption('balanced')]);await page.waitForSelector('html[data-ready="true"]');assert.equal(await page.locator('#preference').inputValue(),'balanced');
  await page.locator('#research-nav a').first().click();await page.waitForFunction(()=>window.__GROUNDOPS__?.ready);assert.equal(await page.evaluate(()=>window.__GROUNDOPS__.getState().view),'tree');
  await page.locator('.predictor-return').click();await page.waitForSelector('html[data-ready="true"]');
  const fullPage=await browser.newPage();
  await fullPage.goto(`http://127.0.0.1:${server.address().port}/full/index.html`);
  await fullPage.waitForSelector('html[data-ready="true"]');
  assert.equal(await fullPage.locator('#preference').inputValue(),'balanced');
  assert.deepEqual(new Set(await fullPage.locator('#board option').evaluateAll(options=>options.map(o=>o.value.split('/')[0]))),new Set(['5','8','10']));
  assert.equal(await fullPage.locator('.card:not(.movement)').count(),0);
  const fullIndex=JSON.parse(await fs.readFile(path.join(work,'full-site/index.json'),'utf8'));
  const fullReport=JSON.parse(await fs.readFile(path.join(work,'full-site',fullIndex.report.url),'utf8'));
  assert.equal(fullReport.ranking_scope,'full_ground_rb');
  assert.equal(fullReport.published_rankings,false);
  assert.equal(fullReport.release_readiness.current.why_not_publishable.includes('required_tasks_not_modeled'),false);
  assert.ok(fullReport.release_readiness.current.why_not_publishable.includes('spawn_rules_assumed'));
  assert.ok(fullReport.release_readiness.current.why_not_publishable.includes('delivery_unverified_outside_source_distance_bounds'));
  assert.match(await fullPage.locator('#coverage').innerText(),/部分导弹的场景距离超出来源声明范围，投射能力尚未核验/);
  assert.match(await fullPage.locator('#coverage').innerText(),/图表覆盖不代表导弹飞行或投射能力已核验/);
  assert.ok(fullReport.release_readiness.current.why_not_publishable.includes('random_ground_reinforcements_unmodeled_or_unverified'));
  assert.match(await fullPage.locator('#coverage').innerText(),/高分房随机补充载具的当前分配与出击规则尚未完整建模/);
  await fullPage.locator('#coverage details summary').click();
  await fullPage.locator('#coverage details li').first().waitFor();
  assert.match(await fullPage.locator('#coverage details').innerText(),/synthetic_a_0.*10 米/);
  await fullPage.close();
  const broken=await browser.newPage();await broken.route('**/data/report.*.json',r=>r.fulfill({contentType:'application/json',body:'{}'}));await broken.goto(`http://127.0.0.1:${server.address().port}/sub/index.html`);await broken.getByRole('button',{name:'重试加载'}).waitFor();await broken.unroute('**/data/report.*.json');await broken.getByRole('button',{name:'重试加载'}).click();await broken.waitForSelector('html[data-ready="true"]');await broken.close();
  assert.deepEqual(errors,[]);
  if(process.env.FORECAST_SCREENSHOT)await page.screenshot({path:process.env.FORECAST_SCREENSHOT,fullPage:true});
  console.log(JSON.stringify({status:'passed',automatic_ranking:true,full_mode_product_default:true,independent_br_and_nation_views:true,lazy_view_shards:true,version_switch:true,nation_filter:true,subpath:true,mobile:true,cohort_switch:true,unified_research:true,hash_error_retry:true,raw_physics_initial_requests:0,page_errors:errors}));
}finally{
  await browser.close();await new Promise(resolve=>server.close(resolve));await fs.rm(work,{recursive:true,force:true});
}
