/** Serve an existing research build plus local probability exports for read-only QA.
 * The temporary HTTP manifest is never saved or used as a production CDN claim. */
import fs from 'node:fs/promises';
import {createReadStream} from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import {createHash} from 'node:crypto';
import {gzipSync} from 'node:zlib';
import {spawn} from 'node:child_process';
const dataRoot=path.resolve(process.argv[2]||'.'),site=path.resolve(process.argv[3]||path.join(dataRoot,'dist/site'));
const physical=path.join(dataRoot,'data/exports/probability');
const manifest=JSON.parse(await fs.readFile(path.join(site,'data/manifest.json'),'utf8'));
const bytes=await fs.readFile(path.join(physical,'index.json'));
manifest.probability={url:'/probability/index.json',gzip_url:'/probability/index.json.gz',bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')};
const raw=Buffer.from(JSON.stringify(manifest)),gz=gzipSync(raw);
const types={'.json':'application/json','.mjs':'text/javascript','.css':'text/css','.html':'text/html','.svg':'image/svg+xml','.gz':'application/gzip'};
const server=http.createServer(async(req,res)=>{
  try{
    const name=decodeURIComponent(new URL(req.url,'http://localhost').pathname);
    if(name==='/data/manifest.json'||name==='/data/manifest.json.gz'){res.writeHead(200,{'Content-Type':types[path.extname(name)]});res.end(name.endsWith('.gz')?gz:raw);return;}
    const isPhysical=name.startsWith('/probability/'),base=isPhysical?physical:site;
    const file=path.resolve(base,(isPhysical?name.slice('/probability/'.length):name.slice(1))||'index.html');
    if(!file.startsWith(base+path.sep))throw new Error('outside test surface');
    const stat=await fs.stat(file);if(!stat.isFile())throw new Error('not a file');
    res.writeHead(200,{'Content-Type':types[path.extname(file)]||'application/octet-stream','Content-Length':stat.size});createReadStream(file).pipe(res);
  }catch{res.writeHead(404);res.end();}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const child=spawn(process.execPath,['tests/browser.e2e.mjs'],{stdio:'inherit',env:{...process.env,GROUNDOPS_DATA_ROOT:dataRoot,GROUNDOPS_TEST_URL:`http://127.0.0.1:${server.address().port}/`}});
const code=await new Promise(resolve=>child.on('exit',resolve));await new Promise(resolve=>server.close(resolve));process.exitCode=code||0;
