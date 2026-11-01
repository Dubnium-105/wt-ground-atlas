import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_forecast import fixture,settings
from wt_atlas.common import file_hash,write_json
from wt_atlas.forecast.data import Atlas
from wt_atlas.system_batch_planning import candidate_requests
from wt_atlas.system_batch_runner import SourceGuard,report_key,run


class SystemBatchRunnerTests(unittest.TestCase):
    def test_source_guard_rechecks_changes_and_rejects_conflicting_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'source';path.write_text('first',encoding='utf8');guard=SourceGuard()
            original=guard.add(path);guard.check();path.write_text('changed',encoding='utf8')
            with self.assertRaisesRegex(ValueError,'Pinned source changed'):guard.check()
            with self.assertRaisesRegex(ValueError,'Pinned source changed'):SourceGuard().add(path,original)

    def test_job_key_binds_every_request_and_batch_identity(self):
        identity={'plan_sha256':'one','runtime':{'numpy':'a'}};request={'system_id':'one','attacker_id':'a','ammo_id':'p'}
        self.assertEqual(report_key(identity,request),report_key(dict(identity),dict(request)))
        self.assertNotEqual(report_key(identity,request),report_key(identity,{**request,'ammo_id':'q'}))
        self.assertNotEqual(report_key(identity,request),report_key({**identity,'plan_sha256':'two'},request))

    def test_synthetic_batch_resumes_and_rejects_tampered_results(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);catalog,atlas_path,ammunition=fixture(root);config=settings()
            base,part=catalog['vehicles'][:2];members=[{'vehicle_id':base['id'],'role':'basic','count':1},
                                                        {'vehicle_id':part['id'],'role':'component','count':2}]
            shared={'basic_vehicle_id':base['id'],'members':members,
                    'provenance':{'method':'official-Wiki-multiunit-membership','url':'fixture-wiki'}}
            base.update(system_role='basic',system_id=base['id'],wiki_url='fixture-wiki',multi_vehicle_system=shared)
            part.update(system_role='component',system_id=base['id'],multi_vehicle_system={'members':members})
            catalog_path=root/'catalog.json';config_path=root/'config.json';bindings_path=root/'bindings.json'
            write_json(catalog_path,catalog);write_json(config_path,config)
            jobs=root/'jobs';jobs.mkdir();bound={}
            for vehicle in (base,part):
                identifier=vehicle['id'];job={'vehicle_id':identifier,'status':'complete',
                    'profile_path':identifier+'.profile','layer_metadata':identifier+'.json','raw_path':identifier+'.grp'}
                for name in (job['profile_path'],job['layer_metadata'],job['raw_path'],identifier+'.npz'):
                    (root/name).write_bytes(b'synthetic geometry')
                path=jobs/(identifier+'.json');write_json(path,job)
                bound[identifier]={'target_sha256':vehicle['analysis_target_sha256'],'source_job_sha256':file_hash(path)}
            write_json(bindings_path,{'repository':str(root),'jobs':str(jobs),'bindings':bound})
            atlas=Atlas(atlas_path,ammunition,catalog);yaws={s['yaw_deg'] for s in config['scenarios']}
            available={(target,ammo,distance):yaws for target,ammo,distance in atlas.lookup if target in bound}
            planned=candidate_requests(catalog,atlas,config,available);self.assertEqual(len(planned['requests']),2)
            sources=[catalog_path,ammunition,config_path,bindings_path]
            source_hashes={str(p.resolve()):file_hash(p) for p in sources}
            repo=Path(__file__).resolve().parents[1];code=repo/'scripts/plan_system_research_batch.py'
            planned.update(schema_version='1.0.0',input_sha256=source_hashes,publishable=False,formal_rankings=0,
                code_sha256_lf={'scripts/plan_system_research_batch.py':hashlib.sha256(code.read_bytes().replace(b'\r\n',b'\n')).hexdigest()})
            plan=root/'plan.json';write_json(plan,planned)
            records=[]
            for key in planned['keys']:
                map_id=atlas.map_for(key['target_id'],key['profile_id'],key['distance_m']);meta=atlas.meta[map_id]
                records.append({**key,'map_id':map_id,'metadata_sha256':meta['metadata_sha256'],'array_sha256':meta['array_sha256']})
            marker=atlas_path/'.wt-system-research-atlas.json';write_json(marker,{'records':records,'publishable':False})
            assembly=root/'assembly.json';write_json(assembly,{'plan_sha256':file_hash(plan),'atlas_manifest_sha256':file_hash(marker),
                'maps':len(records),'candidates':len(planned['requests']),'publishable':False})
            def geometry(_repository,_jobs,selected):return {v['id']:bound[v['id']] for v in selected['vehicles']},[]
            output=root/'results'
            with patch('wt_atlas.system_batch_runner.bind_geometry',side_effect=geometry):
                first=run(plan,assembly,atlas_path,catalog_path,ammunition,config_path,bindings_path,output,limit=1)
                self.assertEqual((first['computed'],first['reused']),(1,0))
                second=run(plan,assembly,atlas_path,catalog_path,ammunition,config_path,bindings_path,output,limit=1,resume=True)
                self.assertEqual((second['computed'],second['reused']),(0,1))
                result=next(p for p in output.glob('*.json') if len(p.stem)==64);result.write_text('{}',encoding='utf8')
                with self.assertRaisesRegex(ValueError,'checksum mismatch'):
                    run(plan,assembly,atlas_path,catalog_path,ammunition,config_path,bindings_path,output,limit=1,resume=True)


if __name__=='__main__':unittest.main()
