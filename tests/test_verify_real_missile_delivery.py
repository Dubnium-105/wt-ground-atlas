import copy
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from test_forecast import fixture, settings
from wt_atlas.forecast.data import digest

ROOT = Path(__file__).resolve().parents[1]


class RealDeliveryVerifierTests(unittest.TestCase):
    def test_real_path_protocol_rejects_changed_geometry_without_overwriting_result(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); catalog, atlas, ammo_file = fixture(root); config = settings()
            attacker = next(v for v in catalog['vehicles'] if v['nation'] == 'a')
            target = next(v for v in catalog['vehicles'] if v['nation'] == 'b')
            ammo_id = attacker['ammo'][0]['id']; profiles = json.loads(ammo_file.read_text(encoding='utf8'))
            p10 = next(p for p in profiles if ammo_id in p['provenance']['penetration_source_ammo_ids'])
            p10['provenance']['chemical_energy'] = {'source_min_distance_m': 30., 'source_max_distance_m': 4000.}
            p500 = copy.deepcopy(p10); p500.update(name=p10['name'] + '_500', distance_m=500.)
            profiles.append(p500); ammo_file.write_text(json.dumps(profiles), encoding='utf8')
            s500 = copy.deepcopy(config['scenarios'][0]); s500.update(id='range500', physical_distance_m=500.)
            config['scenarios'].append(s500); config_file = root / 'config.json'
            config_file.write_text(json.dumps(config), encoding='utf8')
            source_meta = next(p for p in (atlas / 'arrays').glob('*.json')
                               if (lambda m: m['target_id'] == target['id'] and m['ammo_id'] == p10['name'])(json.loads(p.read_text())))
            m10 = json.loads(source_meta.read_text()); m10.update(ammo_profile_sha256=digest(p10), target_binding='independent_geometry_verified')
            source_meta.write_text(json.dumps(m10), encoding='utf8')
            m500 = copy.deepcopy(m10); m500.update(map_id=digest([target['id'], p500['name']]), ammo_id=p500['name'],
                                                  ammo_profile_sha256=digest(p500))
            m500['array_file'] = 'arrays/' + m500['map_id'] + '.npz'
            shutil.copyfile(atlas / m10['array_file'], atlas / m500['array_file'])
            (atlas / 'arrays' / (m500['map_id'] + '.json')).write_text(json.dumps(m500), encoding='utf8')
            index = root / 'inventory.sqlite'; db = sqlite3.connect(index)
            db.execute('create table settings(key text,value text)'); db.execute('insert into settings values (?,?)', ('root', str(atlas)))
            db.execute('create table maps(target text,ammo text,distance real,id text,metadata text,selected integer,status text)')
            for m, distance in ((m10, 10.), (m500, 500.)):
                db.execute('insert into maps values (?,?,?,?,?,?,?)', (target['id'], m['ammo_id'], distance, m['map_id'], json.dumps(m), 1, 'verified'))
            db.commit(); db.close()
            binding = root / 'bindings.json'; binding.write_text(json.dumps({'status':'completed','errors':[],
                'bindings':{target['id']:{'target_sha256':target['analysis_target_sha256'],
                  'identity':{'vehicle_id':target['id'],'client_version':'synthetic-only','datamine_commit':'synthetic-commit'}}}}), encoding='utf8')
            out = root / 'result.json'; command = [sys.executable, str(ROOT / 'scripts/verify_real_missile_delivery.py'),
                '--catalog',str(root/'catalog.json'),'--ammunition',str(ammo_file),'--config',str(config_file),
                '--bindings',str(binding),'--index',str(index),'--attacker',attacker['id'],'--target',target['id'],
                '--ammo-id',ammo_id,'--outside-distance','10','--inside-distance','500','--out',str(out)]
            ok = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            result = json.loads(out.read_text()); self.assertEqual(result['outside']['shot_result'], 'unknown')
            self.assertEqual(result['inside']['shot_result'], 'conditional_impact_only')
            self.assertTrue(result['inside']['warm_cache_reused']); before = out.read_bytes()
            binding.write_text(json.dumps({'status':'completed','errors':[],
                'bindings':{target['id']:{'target_sha256':'wrong'}}}), encoding='utf8')
            bad = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            self.assertNotEqual(bad.returncode, 0); self.assertEqual(out.read_bytes(), before)


if __name__ == '__main__': unittest.main()
