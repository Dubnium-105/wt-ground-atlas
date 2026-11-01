/** Acceptance of a real, already built unified product; no game data is modified. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {createReadStream} from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import {chromium} from 'playwright';
const site=path.resolve(process.argv[2]||'dist/site'),reportPath=path.resolve(process.argv[3]||'reports/product-runtime.json');
const index=JSON.parse(await fs.readFile(path.join(site,'index.json'),'utf8'));
const source=JSON.parse(await fs.readFile(path.join(site,index.report.url),'utf8'));
const mime={'.html':'text/html','.mjs':'text/javascript','.css':'text/css','.json':'application/json','.svg':'image/svg+xml','.gz':'application/gzip'};
const server=http.createServer(async(req,res)=>{
  try{
    const name=decodeURIComponent(new URL(req.url,'http://localhost').pathname);
    const file=path.resolve(site,name.replace(/^\/atlas\//,'')||'index.html');
    if(!file.startsWith(site+path.sep))throw new Error('outside site');
    const stat=await fs.stat(file);if(!stat.isFile())throw new Error('not a file');
    res.writeHead(200,{'Content-Type':mime[path.extname(file)]||'application/octet-stream','Content-Length':stat.size});createReadStream(file).pipe(res);
  }catch{res.writeHead(404);res.end();}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const browser=await chromium.launch({headless:true});const result={checks:[],build_key:index.build_key,source_vehicles:source.catalog_vehicles,source_maps:source.metadata_count};
try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[],requests=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>requests.push(r.url()));
  const started=performance.now();await page.goto(`http://127.0.0.1:${server.address().port}/atlas/index.html`);await page.waitForSelector('html[data-ready="true"]');result.ready_ms=performance.now()-started;
  result.first_load_transfer_bytes=await page.evaluate(()=>performance.getEntriesByType('resource').reduce((sum,r)=>sum+r.transferSize,0)+performance.getEntriesByType('navigation')[0].transferSize);
  assert.equal(await page.locator('#version option').count(),Object.keys(index.worlds).length);
  assert.equal(requests.some(url=>/\.(npz|grp)(?:$|\?)/.test(url)),false);result.checks.push('same-build identity and no initial raw physics requests');
  if(!source.published_rankings)assert.match(await page.locator('#status').innerText(),/正式榜未开放|合成世界/);
  assert.ok((await page.locator('#status').innerText()).includes(String(source.catalog_vehicles)));result.checks.push('truthful source coverage and publication gate');
  const dir=path.join(path.dirname(reportPath),'screenshots');await fs.mkdir(dir,{recursive:true});await page.screenshot({path:path.join(dir,'forecast-real-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.screenshot({path:path.join(dir,'forecast-real-mobile.png'),fullPage:true});result.checks.push('mobile layout');
  if(index.research?.status==='available'){
    await page.locator('#research-nav a').first().click();await page.waitForFunction(()=>window.__GROUNDOPS__?.ready);assert.equal(await page.evaluate(()=>window.__GROUNDOPS__.getState().view),'tree');result.checks.push('real research drilldown');
    assert.match(await page.locator('[aria-label="研究模型说明"]').innerText(),/V3 正式推荐/);
  }
  assert.deepEqual(errors,[]);result.checks.push('no page exceptions');result.page_errors=errors;result.status='passed';
}catch(error){result.status='failed';result.error=error.message;throw error;}
finally{await fs.mkdir(path.dirname(reportPath),{recursive:true});await fs.writeFile(reportPath,JSON.stringify(result,null,2));console.log(JSON.stringify(result));await browser.close();await new Promise(resolve=>server.close(resolve));}
