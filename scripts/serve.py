#!/usr/bin/env python3
"""Serve only the public static directory, with zero application API or database."""
import argparse
import functools
import http.server
import json
import re
import threading
import webbrowser
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
settings=json.loads((ROOT/'config/hosting.json').read_text(encoding='utf-8'))
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--bind',default=settings['bind'])
parser.add_argument('--port',type=int,default=settings['port'])
parser.add_argument('--no-browser',action='store_true')
parser.add_argument('--directory',type=Path,help='Serve a generated site directory instead of the configured default.')
args=parser.parse_args()

class StaticHandler(http.server.SimpleHTTPRequestHandler):
    extensions_map={**http.server.SimpleHTTPRequestHandler.extensions_map,'.mjs':'text/javascript','.json':'application/json','.gz':'application/gzip','.bin':'application/octet-stream'}
    def end_headers(self):
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Cache-Control',f'public, max-age={settings["cache_seconds"]}, immutable' if re.search(r'(?:/|\.)[a-f0-9]{16,64}\.',self.path) else 'no-cache')
        super().end_headers()

directory=(args.directory or ROOT/settings['public_directory']).resolve()
if not (directory/'data/manifest.json').exists():
    raise SystemExit('Missing built static data. Run python build.py before serving.')
handler=functools.partial(StaticHandler,directory=str(directory))
with http.server.ThreadingHTTPServer((args.bind,args.port),handler) as server:
    host=args.bind if args.bind not in ('0.0.0.0','::') else 'localhost'
    url=f'http://{host}:{server.server_port}/'
    print(f'GROUND OPS: {url}',flush=True)
    print(f'Serving {directory}. Press Ctrl+C to stop.',flush=True)
    if settings['open_browser'] and not args.no_browser:
        threading.Thread(target=webbrowser.open,args=(url,),daemon=True).start()
    try:server.serve_forever()
    except KeyboardInterrupt:pass
