/** Exercise an already-built research site without accessing raw physics. */
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
if(!process.argv[2])throw new Error('Pass the built site directory');
const root=path.resolve(process.argv[2]);let mode='normal',duplicateData;
const server=http.createServer(async(req,res)=>{
  try{
    const url=new URL(req.url,'http://localhost'),relative=decodeURIComponent(url.pathname).replace(/^\/sub\//,'');
    const filename=path.resolve(root,relative||'index.html');if(!filename.startsWith(root+path.sep)){res.writeHead(403);res.end();return;}
    let bytes=await fs.readFile(filename);
    if(mode==='duplicate'&&relative==='index.json'){
      const index=JSON.parse(bytes),descriptor=index.system_study.data;
      const study=JSON.parse(await fs.readFile(path.join(root,descriptor.url)));
      for(const system of study.systems){const row=system.rows[0];system.rows.push({...row,scenario_id:'synthetic-extra-scenario'});}
      duplicateData=Buffer.from(JSON.stringify(study));descriptor.bytes=duplicateData.length;descriptor.sha256=createHash('sha256').update(duplicateData).digest('hex');bytes=Buffer.from(JSON.stringify(index));
    }
    if(mode==='duplicate'&&relative.startsWith('data/system-study.'))bytes=duplicateData;
    if(mode==='corrupt'&&relative.startsWith('data/system-study.')){bytes=Buffer.from(bytes);bytes[bytes.length-1]^=1;}
    res.setHeader('Content-Type',({'.html':'text/html','.mjs':'text/javascript','.css':'text/css','.json':'application/json'})[path.extname(filename)]||'application/octet-stream');res.end(bytes);
  }catch{res.writeHead(404);res.end();}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{})});
try{
  const page=await browser.newPage({viewport:{width:1280,height:900}}),requests=[],errors=[];
  page.on('request',r=>requests.push(r.url()));page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/sub/index.html`);await page.waitForSelector('html[data-ready="true"]');
  await page.getByRole('link',{name:'多车系统'}).click();await page.waitForSelector('html[data-ready="true"]');
  assert.match(await page.locator('#study-status').innerText(),/游戏版本 .*研究数据独立于当前预测版本/);
  assert.match(await page.locator('#result').innerText(),/条件穿透范围/);
  assert.ok(await page.locator('#system option').count()>0);assert.ok(await page.locator('#result tbody tr').count()>0);
  const initial=await page.locator('#provenance').textContent();
  if(await page.locator('#yaw option').count()>1){await page.locator('#yaw').selectOption({index:1});assert.notEqual(await page.locator('#provenance').textContent(),initial);}
  await page.setViewportSize({width:390,height:844});assert.ok(await page.locator('#result table').isVisible());
  if(process.env.SYSTEM_STUDY_SCREENSHOT)await page.screenshot({path:process.env.SYSTEM_STUDY_SCREENSHOT,fullPage:true});
  assert.equal(requests.some(u=>u.endsWith('.npz')),false);assert.deepEqual(errors,[]);
  mode='duplicate';const multiPage=await browser.newPage();
  await multiPage.goto(`http://127.0.0.1:${server.address().port}/sub/system-study.html`);await multiPage.waitForSelector('html[data-ready="true"]');
  assert.equal(await multiPage.locator('#scenario-control').isVisible(),true);
  await multiPage.locator('#scenario').selectOption('synthetic-extra-scenario');assert.match(await multiPage.locator('#provenance').textContent(),/synthetic-extra-scenario/);
  mode='corrupt';const badPage=await browser.newPage();
  await badPage.goto(`http://127.0.0.1:${server.address().port}/sub/system-study.html`);
  await badPage.waitForFunction(()=>document.querySelector('#study-status').textContent.includes('校验失败'));
  console.log(JSON.stringify({status:'passed',navigation:true,scenario_switch:true,mobile:true,hash_rejection:true,raw_npz_requests:0}));
}finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
