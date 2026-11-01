import unittest
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
from unittest.mock import patch
from wt_atlas.common import content_hash, file_hash
from wt_atlas.forecast.inventory import stamp
from wt_atlas.component_native_planning import component_requests, plan


class ComponentNativePlanningTests(unittest.TestCase):
    def fixture(self):
        members=[{'vehicle_id':'member','role':'component','count':2}]
        parent={'id':'parent','nation':'a','kind':'ground','system_role':'basic','system_id':'parent','br':{'current':10.},'wiki_url':'wiki-parent',
            'multi_vehicle_system':{'basic_vehicle_id':'parent','members':members,'provenance':{'method':'official-Wiki-multiunit-membership','url':'wiki-parent'}}}
        component={'id':'member','nation':'a','kind':'ground','system_role':'component','system_id':'parent',
            'wiki_url':'wiki-member','multi_vehicle_system':{'basic_vehicle_id':'parent','members':members,
                'provenance':{'method':'official-Wiki-multiunit-membership','url':'wiki-member'}}}
        enemy={'id':'enemy','nation':'b','kind':'ground','br':{'current':10.}}
        ally={'id':'ally','nation':'a','kind':'ground','br':{'current':10.}}
        profile={'name':'shot','mode':'KE','distance_m':100.,'provenance':{}}
        return {'vehicles':[parent,component,enemy,ally]}, {'enemy':[profile],'ally':[profile]}

    def test_uses_parent_br_and_exact_owned_profiles_for_missing_yaws(self):
        catalog,profiles=self.fixture();scenarios=[{'physical_distance_m':100.,'yaw_deg':0},{'physical_distance_m':100.,'yaw_deg':15}]
        result=component_requests(catalog,profiles,scenarios,3,{('member','shot',100.):{0}})
        self.assertEqual(result['candidate_request_count'],1)
        self.assertEqual(result['candidate_rebuild_requests'][0],{'target_vehicle_id':'member','profile_id':'shot','attacker_ids':['enemy'],'required_yaws_deg':[15]})
        self.assertFalse(result['publishable'])

    def test_rejects_unbound_members_and_does_not_plan_unknown_delivery(self):
        catalog,profiles=self.fixture();catalog['vehicles'][1]['multi_vehicle_system']['members'][0]['count']=0
        with self.assertRaisesRegex(ValueError,'membership'):component_requests(catalog,profiles,[{'physical_distance_m':100.,'yaw_deg':0}],3)
        catalog,profiles=self.fixture();catalog['vehicles'][1]['multi_vehicle_system']['provenance']['url']='different'
        with self.assertRaisesRegex(ValueError,'membership'):component_requests(catalog,profiles,[{'physical_distance_m':100.,'yaw_deg':0}],3)
        catalog,profiles=self.fixture();profiles['enemy'][0]['provenance']['chemical_energy']={'source_min_distance_m':200}
        result=component_requests(catalog,profiles,[{'physical_distance_m':100.,'yaw_deg':0}],3)
        self.assertEqual(result['candidate_request_count'],0)
        self.assertEqual(result['counts']['attacker_profiles_outside_declared_delivery_bounds'],1)

    def test_existing_index_only_skips_fresh_exact_source_bound_maps(self):
        catalog,profiles=self.fixture();shot=profiles['enemy'][0]
        shot['provenance']={'client_version':'fixture','datamine_commit':'fixture'}
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);arrays=root/'atlas'/'arrays';arrays.mkdir(parents=True)
            def put(name,obj):
                path=root/name;path.write_text(json.dumps(obj),encoding='utf8');return path
            c=put('catalog.json',catalog);a=put('ammo.json',[shot]);cfg=put('config.json',{
                'matchmaking':{'spread_steps':3},'scenarios':[{'physical_distance_m':100.,'yaw_deg':0}]})
            bindings=put('bindings.json',{'repository':str(root),'jobs':str(root),'bindings':{'member':{'target_sha256':'geometry'}}})
            meta_file=arrays/'m.json';meta_file.write_text('{"source":"fixture"}',encoding='utf8')
            array_file=arrays/'m.npz';array_file.write_bytes(b'previously-verified-fixture')
            metadata={'target_id':'member','target_sha256':'geometry','target_binding':'independent_geometry_verified',
                'ammo_id':'shot','ammo_profile_sha256':content_hash(shot),'source_version':'fixture','datamine_commit':'fixture',
                'array_file':'arrays/m.npz','array_sha256':file_hash(array_file)}
            index=root/'index.sqlite'
            with closing(sqlite3.connect(index)) as db:
                db.execute('create table settings(key text,value text)');db.execute('insert into settings values (?,?)',('root',str(root/'atlas')))
                db.execute('create table maps(target text,ammo text,distance real,angles text,metadata text,array_stamp text,meta_hash text,path text,selected integer,status text)')
                db.execute('insert into maps values (?,?,?,?,?,?,?,?,?,?)',('member','shot',100.,'[0]',json.dumps(metadata),stamp(array_file),file_hash(meta_file),'m.json',1,'verified'))
                db.commit()
            class FakeAtlas:
                def __init__(self,*args,**kwargs):self.profiles={'shot':shot}
                def bindings(self,vehicle):return [('owned',shot)] if vehicle['id']=='enemy' else []
            with patch('wt_atlas.component_native_planning.Atlas',FakeAtlas),patch('wt_atlas.component_native_planning.bind_geometry',return_value=({'member':{'target_sha256':'geometry'}},[])):
                self.assertEqual(plan(c,a,cfg,bindings,[index])['candidate_request_count'],0)
                array_file.write_bytes(b'changed-after-preflight')
                with self.assertRaisesRegex(ValueError,'source changed'):plan(c,a,cfg,bindings,[index])


if __name__=='__main__':unittest.main()
