import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from wt_atlas.common import file_hash
from wt_atlas.system_study import build_study
from site_fixture import load_script


class SystemStudyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        members=[{'vehicle_id':'base','role':'basic','count':1},{'vehicle_id':'part','role':'component','count':2}]
        shared={'basic_vehicle_id':'base','members':members,'provenance':{'method':'official-Wiki-multiunit-membership','url':'wiki'}}
        cat={'snapshot':{'client_source':{'version':'fixture'},'datamine_reference':'fixture-commit'},'vehicles':[
            {'id':'base','kind':'ground','nation':'a','wiki_url':'wiki','system_id':'base','system_role':'basic','multi_vehicle_system':shared},
            {'id':'part','kind':'ground','nation':'a','system_id':'base','system_role':'component','multi_vehicle_system':{'members':members}},
            {'id':'enemy','kind':'ground','nation':'b','ammo':[{'id':'ammo'}]}]}
        self.catalog=self.root/'catalog.json';self.catalog.write_text(json.dumps(cat),encoding='utf8')
        self.ammunition=self.root/'ammunition.json';self.ammunition.write_text(json.dumps([{'name':'shot','mode':'KE','distance_m':10.,
            'provenance':{'client_version':'fixture','datamine_commit':'fixture-commit','penetration_source_ammo_ids':['ammo']}}]),encoding='utf8')
        self.bindings=self.root/'bindings.json';self.bindings.write_text(json.dumps({'bindings':{'base':{'target_sha256':'c'*64},'part':{'target_sha256':'c'*64}}}),encoding='utf8')
        self.config=self.root/'config.json';self.config_text=json.dumps({'scenarios':[{'id':'scene','physical_distance_m':10.,'yaw_deg':0.}],
            'skill_profiles':{'normal':{'distribution':'normal_assumption','mean':0.,'sd':0.,'nodes':1}}})
        self.config.write_text(self.config_text,encoding='utf8');(self.root/'code.py').write_text('pass\n',encoding='utf8')
        rows=[{'vehicle_id':m['vehicle_id'],'role':m['role'],'count':m['count'],'status':'source_bound_map',
               'lower':.2,'upper':.4,'map_id':'a'*64,'array_sha256':'b'*64,'target_sha256':'c'*64,
               'by_skill':[{'skill':0.,'weight':1.,'lower':.2,'upper':.4}]} for m in members]
        self.report={'schema_version':'1.0.0','publishable':False,'formal_rankings':0,'target_selection_weights':'unknown',
            'attacker_id':'enemy','ammo_id':'ammo','skill_profile':'normal','system_id':'base',
            'input_sha256':{'catalog.json':file_hash(self.catalog),'ammunition.json':file_hash(self.ammunition),'bindings.json':file_hash(self.bindings),'config.json':file_hash(self.root/'config.json')},
            'source_code_sha256_lf':{'code.py':hashlib.sha256(b'pass\n').hexdigest()},
            'rows':[{'scenario_id':'scene','physical_distance_m':10.,'yaw_deg':0.,'profile_id':'shot','members':rows,
                     'exposure_bounds':{'lower':.2,'upper':.4,'mean':None,'target_selection_weights':'unknown'}}]}
        self.path=self.root/'report.json';self.write()

    def tearDown(self):self.tmp.cleanup()
    def write(self):self.path.write_text(json.dumps(self.report),encoding='utf8')
    def build(self):return build_study([self.path],self.catalog,self.ammunition,self.bindings,self.config,self.root)

    def test_packaging_preserves_version_bounds_and_source_hashes(self):
        result=self.build();self.assertEqual(result['source_version'],'fixture')
        self.assertFalse(result['publishable']);self.assertEqual(result['systems'][0]['rows'][0]['upper'],.4)
        self.assertEqual(result['systems'][0]['report_sha256'],file_hash(self.path))

    def test_rejects_mutated_inputs_and_unsupported_members(self):
        changed=json.loads(self.config_text);changed['skill_profiles']['normal']['mean']=.5
        self.config.write_text(json.dumps(changed),encoding='utf8')
        with self.assertRaisesRegex(ValueError,'configuration mismatch'):self.build()
        self.config.write_text(self.config_text,encoding='utf8')
        self.report['rows'][0]['members'][1]['count']=3;self.write()
        with self.assertRaisesRegex(ValueError,'membership'):self.build()
        self.report['rows'][0]['members'][1]['count']=2
        self.report['rows'][0]['exposure_bounds']['mean']=.3;self.write()
        with self.assertRaisesRegex(ValueError,'scenario'):self.build()

    def test_rejects_inconsistent_skill_and_selection_bounds(self):
        self.report['rows'][0]['members'][0]['lower']=.1;self.write()
        with self.assertRaisesRegex(ValueError,'skill integration'):self.build()
        self.report['rows'][0]['members'][0]['lower']=.2
        self.report['rows'][0]['exposure_bounds']['upper']=.5;self.write()
        with self.assertRaisesRegex(ValueError,'selection bounds'):self.build()

    def test_rejects_unmatched_geometry_and_projectile(self):
        self.report['rows'][0]['members'][0]['target_sha256']='d'*64;self.write()
        with self.assertRaisesRegex(ValueError,'geometry binding'):self.build()
        self.report['rows'][0]['members'][0]['target_sha256']='c'*64
        self.report['rows'][0]['profile_id']='different';self.write()
        with self.assertRaisesRegex(ValueError,'projectile source'):self.build()

    def test_distinct_tactical_scenarios_may_share_distance_and_yaw(self):
        config=json.loads(self.config_text)
        config['scenarios'].append({'id':'other-scene','physical_distance_m':10.,'yaw_deg':0.})
        self.config.write_text(json.dumps(config),encoding='utf8')
        self.report['input_sha256']['config.json']=file_hash(self.config)
        other=copy.deepcopy(self.report['rows'][0]);other['scenario_id']='other-scene'
        self.report['rows'].append(other);self.write()
        self.assertEqual({r['scenario_id'] for r in self.build()['systems'][0]['rows']},{'scene','other-scene'})

    def test_composition_rejects_overlapping_research_inputs(self):
        output=self.root/'site';report=output/'report.json'
        with self.assertRaisesRegex(ValueError,'overlaps product output'):
            load_script('build_product').compose(self.root/'forecast',output,system_reports=[report],
                system_catalog=self.catalog,system_ammunition=self.ammunition,system_bindings=self.bindings,system_config=self.config)


if __name__=='__main__':unittest.main()
