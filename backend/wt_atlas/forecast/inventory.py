"""Disk-backed, resumable source inventory. Metadata and array verification are separate.

Only explicitly selected, unambiguous physical identities enter Atlas. Invalid and
historical objects remain in the audit database, never quietly in the ranking pool.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import zipfile

from .data import Atlas, canonical, digest, file_hash, number, read_json, safe_file, taxonomy


def stamp(path):
    s = path.stat()
    return canonical([s.st_size, s.st_mtime_ns, s.st_ctime_ns])


class Rows(Mapping):
    def __init__(self, index, lookup=False):
        self.index, self.is_lookup = index, lookup

    def __len__(self):
        return self.index.db.execute('SELECT count(*) FROM maps WHERE selected=1').fetchone()[0]

    def __iter__(self):
        sql = 'SELECT target,ammo,distance FROM maps WHERE selected=1' if self.is_lookup else 'SELECT id FROM maps WHERE selected=1 ORDER BY id'
        for row in self.index.db.execute(sql):
            yield tuple(row) if self.is_lookup else row[0]

    def __getitem__(self, key):
        if self.is_lookup:
            row = self.index.db.execute('SELECT id FROM maps WHERE selected=1 AND target=? AND ammo=? AND distance=?', key).fetchone()
            if row: return row[0]
        else:
            row = self.index.db.execute('SELECT metadata FROM maps WHERE selected=1 AND id=?', (key,)).fetchone()
            if row: return json.loads(row[0])
        raise KeyError(key)

    def values(self):
        if self.is_lookup:
            yield from super().values()
        else:
            for row in self.index.db.execute('SELECT metadata FROM maps WHERE selected=1 ORDER BY id'):
                yield json.loads(row[0])

    def items(self):
        if self.is_lookup:
            yield from super().items()
        else:
            for row in self.index.db.execute('SELECT id,metadata FROM maps WHERE selected=1 ORDER BY id'):
                yield row[0], json.loads(row[1])


def verify_array(task):
    root, meta, budget = task
    started = time.perf_counter()
    # Reuse the exact production array validator, without constructing an index.
    atlas = Atlas.__new__(Atlas)
    atlas.root, atlas.meta = Path(root), {meta['map_id']: meta}
    atlas.memory_budget = budget
    atlas._loaded_id = atlas._loaded = None
    atlas._verified, atlas.loads = {}, 0
    try:
        data = atlas.load(meta['map_id'])
        return {'id': meta['map_id'], 'status': 'verified', 'angles': data['angles_deg'].tolist(),
                'shape': list(data['status'].shape), 'error': None,
                'seconds': time.perf_counter() - started}
    except (ValueError, OSError, KeyError, ArithmeticError, EOFError,zipfile.BadZipFile) as exc:
        return {'id': meta['map_id'], 'status': 'invalid_array', 'angles': None, 'shape': None,
                'error': str(exc), 'seconds': time.perf_counter() - started}


class Inventory:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA cache_size=-16384')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS maps (
            path TEXT PRIMARY KEY, id TEXT, target TEXT, ammo TEXT, distance REAL,
            metadata TEXT, meta_hash TEXT, array_stamp TEXT, bytes INTEGER,
            status TEXT, error TEXT, selected INTEGER DEFAULT 0, generation INTEGER,
            input_key TEXT, angles TEXT, shape TEXT);
          CREATE INDEX IF NOT EXISTS physical_identity ON maps(target,ammo,distance);
          CREATE INDEX IF NOT EXISTS map_id ON maps(id);
          CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY,value TEXT);
        ''')
        self.metadata, self.lookup = Rows(self), Rows(self, True)
        self.metrics = {}

    def close(self):
        self.db.commit()
        self.db.close()

    def sync(self, root, ammunition, catalog, *, bindings=None, force=False):
        started, cpu = time.perf_counter(), time.process_time()
        root = Path(root).resolve()
        profiles = read_json(Path(ammunition))
        by_profile = {p['name']: p for p in profiles}
        if len(profiles) != len(by_profile): raise ValueError('Duplicate ammunition identity')
        by_vehicle = {v['id']: v for v in catalog['vehicles']}
        if len(by_vehicle) != len(catalog['vehicles']): raise ValueError('Duplicate vehicle identity')
        hashes = {k: digest(p) for k, p in by_profile.items()}
        bindings = bindings or {}
        base_key=digest([str(root),file_hash(Path(__file__)),file_hash(Path(__file__).with_name('data.py')),file_hash(Path(__file__).parents[1]/'npz_io.py')])
        target_keys={k:digest([v.get('client_collision'),v.get('source_attributes',{}).get('client'),v.get('analysis_target_sha256'),bindings.get(k)]) for k,v in by_vehicle.items()}
        generation = time.time_ns()
        counts = Counter()
        self.db.execute('UPDATE maps SET selected=0')
        for entry in os.scandir(root / 'arrays'):
            if not entry.name.endswith('.json'): continue
            path = Path(entry.path)
            key = entry.name
            input_key=base_key
            try:
                if path.is_symlink(): raise ValueError('Symlink metadata')
                raw = path.read_bytes()
                h = hashlib.sha256(raw).hexdigest()
                old = self.db.execute('SELECT meta_hash,array_stamp,status,input_key,metadata FROM maps WHERE path=?', (key,)).fetchone()
                m = json.loads(raw,parse_constant=lambda x:(_ for _ in ()).throw(ValueError('Non-finite JSON: '+x)))
                if not isinstance(m,dict):raise ValueError('Metadata must be an object')
                input_key=digest([base_key,hashes.get(m.get('ammo_id')),target_keys.get(m.get('target_id'))])
                source = safe_file(root, m['array_file'])
                current_stamp = stamp(source)
                if not force and old and (h, current_stamp, input_key) == (old[0], old[1], old[3]) and old[2] in ('metadata_valid', 'verified'):
                    self.db.execute('UPDATE maps SET generation=? WHERE path=?', (generation,key))
                    counts['incremental_hits'] += 1
                    continue
                for field in ('map_id','target_id','target_sha256','ammo_id','ammo_profile_sha256','source_version','datamine_commit','array_sha256'):
                    if not isinstance(m.get(field), str) or not m[field]: raise ValueError('Missing map identity: '+field)
                if m['map_id'] != path.stem: raise ValueError('Map filename identity mismatch')
                p = by_profile.get(m['ammo_id'])
                if p is None: raise ValueError('profile_missing')
                if hashes[m['ammo_id']] != m['ammo_profile_sha256']: raise ValueError('profile_hash_mismatch')
                if p.get('mode') not in ('KE','CE') or p.get('reference_only') or m.get('reference_only'): raise ValueError('unsupported_or_reference_ammunition')
                prov = p.get('provenance', {})
                if (prov.get('client_version'),prov.get('datamine_commit')) != (m['source_version'],m['datamine_commit']): raise ValueError('profile_version_mismatch')
                d = p.get('distance_m')
                if not number(d) or d < 0: raise ValueError('distance_missing')
                v = by_vehicle.get(m['target_id'])
                if v is None: raise ValueError('catalog_target_missing')
                client = v.get('client_collision') or v.get('source_attributes',{}).get('client',{})
                if client.get('source_version') != m['source_version']: raise ValueError('target_version_mismatch')
                bound = bindings.get(m['target_id'])
                expected = bound.get('target_sha256') if bound else v.get('analysis_target_sha256')
                if expected and expected != m['target_sha256']: raise ValueError('target_geometry_mismatch')
                m['target_binding'] = 'independent_geometry_verified' if bound else 'source_declared_id_version_only'
                m['metadata_sha256'] = h
                m.pop('images', None)
                self.db.execute('INSERT OR REPLACE INTO maps VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (key,m['map_id'],m['target_id'],m['ammo_id'],d,canonical(m),h,current_stamp,source.stat().st_size,
                     'metadata_valid',None,0,generation,input_key,None,None))
                counts['metadata_rebuilt'] += 1
            except (ValueError, KeyError, OSError, TypeError) as exc:
                self.db.execute('INSERT OR REPLACE INTO maps(path,status,error,generation,input_key) VALUES (?,?,?,?,?)',
                                (key,'rejected',str(exc),generation,input_key))
                counts['rejected'] += 1
            if sum(counts.values()) % 500 == 0: self.db.commit()
        self.db.execute('DELETE FROM maps WHERE generation != ?', (generation,))
        self.db.execute("UPDATE maps SET selected=1 WHERE status IN ('metadata_valid','verified')")
        # Distinct map IDs with the same target/ammo/range are not interchangeable.
        self.db.execute('''UPDATE maps SET selected=0 WHERE (target,ammo,distance) IN
          (SELECT target,ammo,distance FROM maps WHERE selected=1 GROUP BY target,ammo,distance HAVING count(*)>1)''')
        self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('root',str(root)))
        self.db.commit()
        self.metrics = {**counts,'metadata_seconds':time.perf_counter()-started,'metadata_cpu_seconds':time.process_time()-cpu}
        return self.summary()

    def verify(self, root, *, max_workers=1, memory_budget=512*1024**2, resume=True, limit=None):
        if max_workers < 1 or memory_budget < 32*1024**2: raise ValueError('Invalid worker/memory budget')
        workers = min(max_workers, max(1,memory_budget // (64*1024**2)))
        per_worker = memory_budget // workers
        sql = "SELECT metadata FROM maps WHERE selected=1" + (" AND status!='verified'" if resume else '') + ' ORDER BY id'
        cursor = self.db.execute(sql)
        if limit is not None and limit < 0: raise ValueError('Negative verification limit')
        started = time.perf_counter(); verified = 0
        with ProcessPoolExecutor(max_workers=workers) as pool:
            # Bounded batches work on Python 3.11+ (no unbounded executor.map queue).
            while limit is None or verified < limit:
                batch = cursor.fetchmany(min(workers*4,limit-verified) if limit is not None else workers*4)
                if not batch: break
                tasks = [(str(root),json.loads(r[0]),per_worker) for r in batch]
                for result in pool.map(verify_array,tasks):
                    self.db.execute('UPDATE maps SET status=?,error=?,angles=?,shape=?,selected=? WHERE id=?',
                        (result['status'],result['error'],canonical(result['angles']),canonical(result['shape']),
                         int(result['status']=='verified'),result['id']))
                    verified += 1
                self.db.commit()
        self.metrics.update(array_seconds=time.perf_counter()-started,arrays_checked=verified,workers=workers,memory_budget_bytes=memory_budget)
        return self.summary()

    def apply_bindings(self, bindings):
        """Apply freshly reconstructed geometry evidence without re-reading NPZ files."""
        for identifier,row in bindings.items():
            for path,raw in self.db.execute("SELECT path,metadata FROM maps WHERE target=? AND status IN ('metadata_valid','verified')",(identifier,)).fetchall():
                m=json.loads(raw)
                if m['target_sha256']!=row['target_sha256']:
                    self.db.execute("UPDATE maps SET selected=0,status='rejected',error='target_geometry_mismatch' WHERE path=?",(path,))
                else:
                    m['target_binding']='independent_geometry_verified'
                    m['binding_evidence_sha256']=digest(row)
                    self.db.execute('UPDATE maps SET metadata=? WHERE path=?',(canonical(m),path))
        self.db.commit()
        return self.summary()

    def summary(self):
        status = dict(self.db.execute('SELECT status,count(*) FROM maps GROUP BY status'))
        errors = dict(self.db.execute('SELECT error,count(*) FROM maps WHERE error IS NOT NULL GROUP BY error'))
        selected, byte_count = self.db.execute('SELECT count(*),coalesce(sum(bytes),0) FROM maps WHERE selected=1').fetchone()
        verified = self.db.execute("SELECT count(*) FROM maps WHERE selected=1 AND status='verified'").fetchone()[0]
        h = hashlib.sha256()
        for row in self.db.execute('SELECT id,meta_hash,status,selected FROM maps ORDER BY path'):
            h.update(canonical(list(row)).encode())
        return {'metadata_objects':sum(status.values()),'selected_maps':selected,'verified_arrays':verified,
                'selected_array_bytes':byte_count,'status_counts':status,'rejections':errors,
                'ambiguous_maps':self.db.execute("SELECT count(*) FROM maps WHERE status IN ('metadata_valid','verified') AND selected=0").fetchone()[0],
                'content_sha256':h.hexdigest(),'metrics':self.metrics}


def bind_geometry(repository: Path, jobs: Path, catalog: dict) -> tuple[dict, list]:
    """Reconstruct the V2 generator target identity from independently hashed files.

    A declared analysis_target_sha256 alone is never independent verification.
    Stored legacy Windows job separators are normalized only in this local adapter.
    """
    bound, errors = {}, []
    vehicles = {v['id']:v for v in catalog['vehicles']}
    for path in sorted(jobs.glob('*.json')):
        try:
            job = read_json(path); identifier = job['vehicle_id']
            if identifier not in vehicles or job.get('status') != 'complete': continue
            def local(name): return safe_file(repository, name.replace('\\','/'))
            profile_path, meta_path, raw_path = (local(job[k]) for k in ('profile_path','layer_metadata','raw_path'))
            profile, meta = read_json(profile_path), read_json(meta_path)
            layers_path = meta_path.with_suffix('.npz')
            if layers_path.is_symlink(): raise ValueError('Symlink geometry layers')
            hashes = {'profile':file_hash(profile_path),'grp':file_hash(raw_path),'layers':file_hash(layers_path)}
            if hashes['profile'] != job['profile_sha256'] or hashes['grp'] != job['grp_sha256'] or hashes['grp'] != meta['grp_sha256'] or hashes['layers'] != meta['arrays_sha256']:
                raise ValueError('Geometry input hash mismatch')
            client = vehicles[identifier].get('source_attributes',{}).get('client',{})
            prov = profile['provenance']
            if profile.get('vehicle') != identifier or client.get('source_version') != prov['client_version'] or client.get('geometry_sha256') != hashes['grp'] or client.get('configuration_sha256') != hashes['profile']:
                raise ValueError('Catalog configuration/geometry/version mismatch')
            identity = {'vehicle_id':identifier,'geometry_sha256':hashes['grp'],
                        'configuration_sha256':hashes['profile'],'layers_sha256':hashes['layers'],
                        'client_version':prov['client_version'],'datamine_commit':prov['datamine_commit'],'plan':meta['plan']}
            bound[identifier] = {'target_sha256':digest(identity),'identity':identity,'source_job_sha256':file_hash(path)}
        except (ValueError, KeyError, OSError, TypeError) as exc:
            errors.append({'job':path.name,'error':str(exc)})
    return bound, errors


def discover(roots):
    """Bounded conventional-path discovery; never scan every disk or infer versions."""
    found = {'catalogs':[], 'ammunition':[], 'atlases':[], 'samples':[], 'sample_archives':[], 'client_jobs':[], 'clients':[]}
    for raw in roots:
        root = Path(raw).expanduser().resolve()
        for p in root.glob('data/snapshots/catalog.json'): found['catalogs'].append(str(p))
        for p in root.glob('data/derived/grp_probability_v2/*/ammunition/all_resolved_profiles_final_with_apcr_gun_caliber.json'): found['ammunition'].append(str(p))
        for p in root.glob('data/derived/grp_probability_v2/*/*/arrays'): found['atlases'].append(str(p.parent))
        for p in root.glob('data/work/*/jobs'): found['client_jobs'].append(str(p))
        for pattern in ('analysis-sample*/manifest.json','data/analysis-sample*/manifest.json'):
            for p in root.glob(pattern): found['samples'].append(str(p.parent))
        # Archives are discovered separately; their existence is not a claim
        # of manifest validation and they are never silently treated as folders.
        for pattern in ('analysis-sample*.zip','data/analysis-sample*.zip','data/exports/analysis-sample*.zip'):
            for p in root.glob(pattern):
                if p.is_file():found['sample_archives'].append(str(p))
        cfg = root/'config/pipeline.json'
        if cfg.is_file():
            source = read_json(cfg).get('client',{}).get('source')
            if source and Path(source).is_dir(): found['clients'].append(str(Path(source).resolve()))
    return {k:sorted(set(v)) for k,v in found.items()}


def coverage(catalog, atlas, config, index=None):
    """Exact directed required-map matrix for declared native scenarios, not queue frequency."""
    vehicles = catalog['vehicles']; rows = []; totals = Counter(); groups = {}
    from .data import br_step
    grounds = [v for v in vehicles if v.get('kind')=='ground' and number(v.get('br',{}).get('current')) and v.get('system_role')!='component']
    bound = {v['id']:atlas.bindings(v) for v in grounds}
    # One index query per map, then bounded set membership in the pair loop.
    available = set(atlas.lookup)
    angle_sets={}
    if index:
        for target,ammo,distance,angles in index.db.execute('SELECT target,ammo,distance,angles FROM maps WHERE selected=1'):
            angle_sets[(target,ammo,distance)]=set(json.loads(angles)) if angles and angles!='null' else None
    def has_slice(key,yaw):
        if key not in available:return False
        angles=angle_sets.get(key)
        return angles is not None and yaw in angles
    if index is None:
        for key,map_id in atlas.lookup.items():
            declared=atlas.meta[map_id].get('angles_deg')
            angle_sets[key]=set(declared) if declared is not None else None
    for v in vehicles:
        key=(v.get('nation'),v.get('br',{}).get('current'),taxonomy(v)['domain'],taxonomy(v)['class'])
        groups[key] = groups.get(key,0)+1
    for a in grounds:
        item=Counter(); profiles={p['name']:p for _,p in bound[a['id']]}
        for b in grounds:
            if a['nation']==b['nation'] or abs(br_step(a['br']['current'])-br_step(b['br']['current']))>config['matchmaking']['spread_steps']: continue
            for scenario in config['scenarios']:
                item['required_directed_pairs']+=1
                d=scenario['physical_distance_m']
                forward=any(has_slice((b['id'],p,d),scenario['yaw_deg']) for p in profiles)
                reverse=any(has_slice((a['id'],p['name'],d),scenario['yaw_deg']) for _,p in bound[b['id']])
                if not profiles: item['missing_owned_ammunition']+=1
                if not forward: item['missing_direction_or_distance']+=1
                if forward: item['available_forward_pairs']+=1
                if forward and reverse: item['available_bidirectional_pairs']+=1
        totals.update(item);rows.append({'vehicle_id':a['id'],'nation':a['nation'],'br':a['br']['current'],**item})
    report={'schema_version':'1.0.0','catalog_vehicles':len(vehicles),'ammunition_profiles':len(atlas.profiles),
        'physical_distances_m':sorted({p['distance_m'] for p in atlas.profiles.values() if number(p.get('distance_m'))}),
        'groups':[{'nation':k[0],'br':k[1],'domain':k[2],'class':k[3],'count':v} for k,v in sorted(groups.items(),key=lambda x:str(x[0]))],
        'counts':dict(totals),'matrix':rows,'pair_count_semantics':'vehicle directions per configured scenario; any exact owned supported round; only exact verified or explicitly declared yaw slices count; any missing angle metadata is unavailable',
        'sample_subset':catalog.get('sample_subset',False),'inventory':index.summary() if index else None}
    report['blockers']=[]
    if catalog.get('sample_subset'): report['blockers'].append('sample_subset')
    if totals['available_bidirectional_pairs']<totals['required_directed_pairs']: report['blockers'].append('incomplete_bidirectional_native_coverage')
    if index:
        summary=report['inventory']
        if summary['verified_arrays']<summary['selected_maps']:report['blockers'].append('arrays_not_fully_verified')
        if summary['ambiguous_maps']:report['blockers'].append('ambiguous_physical_identities')
    return report
