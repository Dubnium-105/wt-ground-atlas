/** Verify a built batch site and its lazy requests without reading raw maps. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
if(!process.argv[2])throw new Error('Pass the built batch site directory');
const root=path.resolve(process.argv[2]);
const server=http.createServer(async(req,res)=>{
  try{
    const relative=decodeURIComponent(new URL(req.url,'http://localhost').pathname).replace(/^\/sub\//,'');
    const filename=path.resolve(root,relative||'index.html');if(!filename.startsWith(root+path.sep)){res.writeHead(403);res.end();return;}
    const bytes=await fs.readFile(filename);res.setHeader('Content-Type',({'.html':'text/html','.mjs':'text/javascript','.css':'text/css','.json':'application/json'})[path.extname(filename)]||'application/octet-stream');res.end(bytes);
  }catch{res.writeHead(404);res.end();}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{})});
try{
  const page=await browser.newPage(),requests=[],errors=[];page.on('request',r=>requests.push(r.url()));page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/sub/index.html`);await page.waitForSelector('html[data-ready="true"]');
  await page.getByRole('link',{name:'多车系统'}).click();await page.waitForSelector('html[data-ready="true"]');
  const attackers=await page.locator('#attacker option').count(),first=await page.locator('#attacker').inputValue();
  assert.ok(attackers>1);assert.equal(requests.filter(u=>u.includes('/data/system-study/')).length,1);
  await page.locator('#attacker').selectOption({index:1});await page.waitForFunction(first=>document.querySelector('#attacker').value!==first&&document.documentElement.dataset.ready==='true',first);
  assert.equal(requests.filter(u=>u.includes('/data/system-study/')).length,2);
  assert.match(await page.locator('#result').innerText(),/条件穿透范围/);assert.ok(await page.locator('#result tbody tr').count()>0);
  assert.equal(requests.some(u=>u.endsWith('.npz')),false);assert.deepEqual(errors,[]);
  console.log(JSON.stringify({status:'passed',attacker_options:attackers,shards_requested:2,raw_npz_requests:0}));
}finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
