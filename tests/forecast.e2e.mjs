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
p=Path(sys.argv[1]);fixture(p)
(p/'config.json').write_text(json.dumps(settings()))
(p/'patch.json').write_text(json.dumps({'id':'synthetic-patch','status':'proposed','entries':[{'vehicle_id':'synthetic_a_0','field':'br','old':9.3,'new':9.7}]}))
`,work],{cwd:root,encoding:'utf8'});
assert.equal(setup.status,0,setup.stderr);
const build=spawnSync(python,['scripts/build_forecast.py','--catalog',path.join(work,'catalog.json'),'--ammunition',path.join(work,'ammo.json'),'--atlas',path.join(work,'v2'),'--config',path.join(work,'config.json'),'--scenario',path.join(work,'patch.json'),'--cache',path.join(work,'cache.sqlite'),'--out',path.join(work,'site')],{cwd:root,encoding:'utf8'});
assert.equal(build.status,0,build.stderr);
const site=path.join(work,'site');
const server=http.createServer(async(req,res)=>{
  try{
    const pathname=decodeURIComponent(new URL(req.url,'http://localhost').pathname);
    const relative=pathname.startsWith('/sub/')?pathname.slice(5):pathname.slice(1);
    const filename=path.resolve(site,relative||'index.html');
    if(!filename.startsWith(site+path.sep)){res.writeHead(403);res.end();return;}
    const data=await fs.readFile(filename);
    res.setHeader('Content-Type',({'.html':'text/html','.mjs':'text/javascript','.css':'text/css','.json':'application/json'})[path.extname(filename)]||'application/octet-stream');res.end(data);
  }catch{res.writeHead(404);res.end();}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{})});
try{
  const page=await browser.newPage({viewport:{width:1280,height:900}}),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/sub/index.html`);
  await page.waitForSelector('html[data-ready="true"]');
  assert.equal(await page.locator('#version').inputValue(),'planned');
  assert.ok(await page.locator('.card').count()>0,'automatic recommendations require no manual lineup');
  await page.locator('#nation').selectOption('a');
  await page.waitForFunction(()=>[...document.querySelectorAll('.card .eyebrow')].every(x=>x.textContent.includes(' · a · ')));
  await page.locator('#version').selectOption('current');
  await page.waitForFunction(()=>document.querySelector('#rankings h2').textContent.startsWith('当前'));
  await page.setViewportSize({width:390,height:844});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),'mobile horizontal overflow');
  assert.deepEqual(errors,[]);
  if(process.env.FORECAST_SCREENSHOT)await page.screenshot({path:process.env.FORECAST_SCREENSHOT,fullPage:true});
  console.log(JSON.stringify({status:'passed',automatic_ranking:true,version_switch:true,nation_filter:true,subpath:true,mobile:true,page_errors:errors}));
}finally{
  await browser.close();await new Promise(resolve=>server.close(resolve));await fs.rm(work,{recursive:true,force:true});
}
