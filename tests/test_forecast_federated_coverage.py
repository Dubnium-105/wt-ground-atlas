import json
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from test_forecast import fixture,settings
from wt_atlas.common import write_json
from wt_atlas.forecast.federated_coverage import audit


class FederatedCoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.catalog,self.atlas,self.ammunition=fixture(self.root)
        self.config=self.root/'config.json';write_json(self.config,settings())
        self.indexes=[self.root/'one.sqlite',self.root/'two.sqlite']
        connections=[sqlite3.connect(p) for p in self.indexes]
        for db in connections:
            db.execute('CREATE TABLE maps (id TEXT,target TEXT,ammo TEXT,distance REAL,angles TEXT,metadata TEXT,meta_hash TEXT,array_stamp TEXT,selected INTEGER,status TEXT)')
            db.execute('CREATE TABLE settings (key TEXT,value TEXT)')
            db.execute('INSERT INTO settings VALUES (?,?)',('root',str(self.atlas)))
        for i,path in enumerate(sorted((self.atlas/'arrays').glob('*.json'))):
            meta=json.loads(path.read_text(encoding='utf8'));meta['target_binding']='independent_geometry_verified';meta['metadata_sha256']='a'*64
            connections[i%2].execute('INSERT INTO maps VALUES (?,?,?,?,?,?,?,?,?,?)',
                (meta['map_id'],meta['target_id'],meta['ammo_id'],10.,'[0.0]',json.dumps(meta),'a'*64,'[1,1,1]',1,'verified'))
        for db in connections:db.commit();db.close()

    def tearDown(self):self.temp.cleanup()

    def run_audit(self):return audit(self.root/'catalog.json',self.ammunition,self.config,self.indexes)

    def test_union_measures_complete_bidirectional_coverage_without_publishing(self):
        result=self.run_audit()
        self.assertEqual(result['inventory']['verified_arrays'],16)
        self.assertEqual(result['counts']['required_directed_pairs'],8)
        self.assertEqual(result['counts']['available_bidirectional_pairs'],8)
        self.assertFalse(result['source_arrays_rechecked']);self.assertFalse(result['publishable'])
        self.assertEqual(result['formal_rankings'],0)

    def test_missing_direction_is_reported_not_filled_from_other_pairs(self):
        with closing(sqlite3.connect(self.indexes[0])) as db:
            db.execute("DELETE FROM maps WHERE id=(SELECT id FROM maps WHERE target LIKE 'synthetic_a_%' AND ammo LIKE 'synthetic_b_%' LIMIT 1)")
            db.commit()
        result=self.run_audit()
        self.assertLess(result['counts']['available_bidirectional_pairs'],result['counts']['required_directed_pairs'])
        self.assertIn('incomplete_bidirectional_native_coverage',result['blockers'])

    def test_unverified_or_duplicate_identities_are_rejected(self):
        with closing(sqlite3.connect(self.indexes[0])) as db:
            db.execute("UPDATE maps SET status='metadata_valid' WHERE rowid=1");db.commit()
        with self.assertRaisesRegex(ValueError,'unverified'):self.run_audit()
        with closing(sqlite3.connect(self.indexes[0])) as db:
            db.execute("UPDATE maps SET status='verified' WHERE rowid=1");db.commit()
        with closing(sqlite3.connect(self.indexes[0])) as src,closing(sqlite3.connect(self.indexes[1])) as dst:
            row=src.execute('SELECT * FROM maps LIMIT 1').fetchone();dst.execute('INSERT INTO maps VALUES (?,?,?,?,?,?,?,?,?,?)',row);dst.commit()
        with self.assertRaisesRegex(ValueError,'Duplicate physical identity'):self.run_audit()

    def test_wrong_profile_hash_or_active_wal_cannot_count_as_verified(self):
        with closing(sqlite3.connect(self.indexes[0])) as db:
            raw=db.execute('SELECT metadata FROM maps WHERE rowid=1').fetchone()[0]
            meta=json.loads(raw);meta['ammo_profile_sha256']='0'*64
            db.execute('UPDATE maps SET metadata=? WHERE rowid=1',(json.dumps(meta),));db.commit()
        with self.assertRaisesRegex(ValueError,'conflicts'):self.run_audit()
        wal=Path(str(self.indexes[1])+'-wal');wal.write_bytes(b'active')
        with self.assertRaisesRegex(ValueError,'live WAL'):self.run_audit()

    def test_corrupt_index_fingerprints_cannot_count_as_verified(self):
        with closing(sqlite3.connect(self.indexes[0])) as db:
            db.execute("UPDATE maps SET array_stamp='garbage' WHERE rowid=1");db.commit()
        with self.assertRaisesRegex(ValueError,'array stamp'):self.run_audit()
        with closing(sqlite3.connect(self.indexes[0])) as db:
            db.execute("UPDATE maps SET array_stamp='[1,1,1]',meta_hash=? WHERE rowid=1",('b'*64,));db.commit()
        with self.assertRaisesRegex(ValueError,'fingerprints'):self.run_audit()

    def test_cli_will_not_write_inside_a_source_atlas(self):
        from test_forecast import ROOT
        out=self.atlas/'new-report.json'
        result=subprocess.run([sys.executable,str(ROOT/'scripts/audit_federated_coverage.py'),
            '--catalog',str(self.root/'catalog.json'),'--ammunition',str(self.ammunition),
            '--config',str(self.config),'--index',str(self.indexes[0]),'--out',str(out)],capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0);self.assertFalse(out.exists())

    def test_missing_or_wrong_catalog_source_version_is_not_accepted(self):
        catalog_path=self.root/'catalog.json';catalog=json.loads(catalog_path.read_text(encoding='utf8'))
        catalog['snapshot']['datamine_reference']='a'*40;write_json(catalog_path,catalog)
        with self.assertRaisesRegex(ValueError,'conflicts'):self.run_audit()
        catalog['snapshot']['datamine_reference']='synthetic-commit';write_json(catalog_path,catalog)
        with closing(sqlite3.connect(self.indexes[0])) as db:
            raw=db.execute('SELECT metadata FROM maps WHERE rowid=1').fetchone()[0]
            meta=json.loads(raw);meta['datamine_commit']=''
            db.execute('UPDATE maps SET metadata=? WHERE rowid=1',(json.dumps(meta),));db.commit()
        with self.assertRaisesRegex(ValueError,'conflicts'):self.run_audit()


if __name__=='__main__':unittest.main()
