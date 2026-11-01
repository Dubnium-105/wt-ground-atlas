/** Exercise lazy synthetic research shards, including corrupted payload rejection. */
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {gzipSync} from 'node:zlib';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
const sha=b=>createHash('sha256').update(b).digest('hex');
const assets=new Map();let corrupt=false,httpDecoded=false,shardRequests=0;
const choices=[];
for(const [attacker,ammo] of [['A','one'],['A','two'],['B','three']]){
  const study={source_version:'fixture',attacker_id:attacker,attacker_name:attacker,ammo_id:ammo,ammo_name:ammo,skill_profile:'normal',
    systems:[{system_id:'system',name:'System',report_sha256:'fixture',rows:[{scenario_id:'scene',distance_m:10,yaw_deg:0,lower:.2,upper:.8,profile_id:'fixture',
      members:[{name:'Member',count:1,lower:.2,upper:.8,status:'source_bound_map'}]}]}]};
  const raw=Buffer.from(JSON.stringify(study)),compressed=gzipSync(raw),url=`data/system-study/${attacker}-${ammo}.json.gz`;
  assets.set(url,compressed);choices.push({attacker_id:attacker,attacker_name:attacker,ammo_id:ammo,ammo_name:ammo,
    data:{url,sha256:sha(compressed),bytes:compressed.length,decoded_sha256:sha(raw),decoded_bytes:raw.length,encoding:'gzip'}});
}
const manifest=Buffer.from(JSON.stringify({kind:'batch',choices}));assets.set('data/batch.json',manifest);
assets.set('index.json',Buffer.from(JSON.stringify({system_study:{data:{url:'data/batch.json',sha256:sha(manifest),bytes:manifest.length}}})));
const server=http.createServer(async(req,res)=>{
  try{
    const name=decodeURIComponent(new URL(req.url,'http://localhost').pathname).replace(/^\/sub\//,'');
    let bytes=assets.get(name);
    if(name.startsWith('data/system-study/')){shardRequests++;if(corrupt){bytes=Buffer.from(bytes);bytes[bytes.length-1]^=1;}if(httpDecoded)res.setHeader('Content-Encoding','gzip');}
    if(!bytes&&['system-study.html','system-study.mjs','styles.css'].includes(name))bytes=await fs.readFile(path.resolve('web/forecast',name));
    if(!bytes){res.writeHead(404);res.end();return;}
    res.setHeader('Content-Type',name.endsWith('.mjs')?'text/javascript':name.endsWith('.html')?'text/html':name.endsWith('.css')?'text/css':'application/octet-stream');res.end(bytes);
  }catch{res.writeHead(500);res.end();}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{})});
try{
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/sub/system-study.html`);await page.waitForSelector('html[data-ready="true"]');
  assert.equal(shardRequests,1);assert.equal(await page.locator('#attacker').inputValue(),'A');
  await page.locator('#ammo').selectOption('two');await page.waitForFunction(()=>document.querySelector('#study-status').textContent.includes('two'));
  assert.equal(shardRequests,2);await page.locator('#attacker').selectOption('B');
  await page.waitForFunction(()=>document.querySelector('#study-status').textContent.includes('three'));assert.equal(shardRequests,3);
  await page.setViewportSize({width:390,height:844});assert.ok(await page.locator('#result table').isVisible());assert.deepEqual(errors,[]);
  httpDecoded=true;const decoded=await browser.newPage();await decoded.goto(`http://127.0.0.1:${server.address().port}/sub/system-study.html`);
  await decoded.waitForSelector('html[data-ready="true"]');httpDecoded=false;
  corrupt=true;const bad=await browser.newPage();await bad.goto(`http://127.0.0.1:${server.address().port}/sub/system-study.html`);
  await bad.waitForFunction(()=>document.querySelector('#study-status').textContent.includes('校验失败'));
  console.log(JSON.stringify({status:'passed',lazy_shards:true,selection:true,mobile:true,http_decoded:true,corruption_rejected:true}));
}finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
