"""Full task scope, physical anti-tank and automatic mixed-sortie search."""
import copy
import itertools
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_forecast import ROOT, fixture, settings
from wt_atlas.forecast.data import Atlas, canonical, read_json
from wt_atlas.forecast.engine import Forecast, ShotCache
from wt_atlas.forecast.profiles import resolve, cohorts
from wt_atlas.forecast.search import Optimizer, feasible
from wt_atlas.forecast.tasks import modeled_tasks, phase_tasks

FULL = ROOT / 'config/forecast.full-ground-rb.json'
TASKS = {'ground_control','anti_tank','recon','anti_helicopter','anti_air','cap','cas','sead'}


def add_air(catalog):
    for nation in ('a', 'b'):
        for kind in ('aircraft', 'helicopter'):
            vehicle = copy.deepcopy(next(v for v in catalog['vehicles'] if v['nation'] == nation))
            vehicle.update(id=f'synthetic_{nation}_{kind}', kind=kind, ammo=[], loadouts=[{
                'id':'clean', 'legal':True, 'source_status':'official_verified',
                'source_version':'synthetic-only', 'source':'synthetic fixture', 'weapons':[]}])
            catalog['vehicles'].append(vehicle)


class FullModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.cat, self.aroot, self.ammo = fixture(self.root)
        self.cache = ShotCache(self.root/'cache.sqlite')
        self.c = settings(); self.c['mission_policy'] = read_json(FULL)['mission_policy']

    def tearDown(self):
        self.cache.close(); self.tmp.cleanup()

    def engine(self):
        return Forecast(self.cat, Atlas(self.aroot,self.ammo,self.cat), self.c,self.cache)

    def test_ground_anti_tank_uses_exact_pair_trace_and_preserves_unknown_physics(self):
        e = self.engine(); own = e.variants[0]; context = (own['br_step'],self.c['scenarios'][0]['id'])
        ground = e.mission(own['key'],'ground',*context)
        anti = e.mission(own['key'],'anti_tank',*context)
        for field in ('mean','lower','upper','coverage','production_coverage'):
            self.assertEqual(anti[field],ground[field])
        self.assertEqual(anti['assumed_coverage'],0.)
        self.assertEqual(anti['evidence_trace'],ground['evidence_trace'])
        self.assertNotEqual(anti['mission_key'],ground['mission_key'])
        self.assertTrue(all(self.cache.evidence_get(k)['level']=='pair' for k in anti['evidence_trace']['pair_keys']))
        # Exact native range is mandatory even though defaults are enabled.
        self.c['scenarios'][0].update(physical_distance_m=100.,aiming_distance_m=100.)
        missing = self.engine().mission(own['key'],'anti_tank',*context)
        self.assertEqual(missing['coverage'],0.)
        self.assertEqual(missing['production_coverage'],0.)
        self.assertEqual(missing['assumed_coverage'],0.)
        self.assertEqual((missing['lower'],missing['upper']),(0.,1.))

    def test_task_domain_limits_and_air_anti_tank_defaults(self):
        add_air(self.cat); e=self.engine()
        ground=next(v for v in e.variants if v['domain']=='ground')
        air=next(v for v in e.variants if v['domain']=='air')
        for own,tasks in ((ground,('cap','cas')),(air,('ground','ground_control'))):
            for task in tasks:
                row=e.mission(own['key'],task,own['br_step'],self.c['scenarios'][0]['id'])
                self.assertFalse(row['usable']);self.assertEqual(row['upper'],0.)
                self.assertEqual(row['assumed_coverage'],0.)
        row=e.mission(air['key'],'anti_tank',air['br_step'],self.c['scenarios'][0]['id'])
        self.assertEqual((row['lower'],row['mean'],row['upper']),(0.,.5,1.))
        self.assertEqual(row['production_coverage'],0.)
        self.assertEqual(row['assumed_coverage'],1.)
        self.assertTrue(row['evidence_trace']['external_evidence'])

    def test_task_mixture_preserves_weighted_bounds_and_physical_trace(self):
        self.c['spawn'].update(phases=['opening'],phase_weights=[1.],
            phase_task_weights=[{'anti_tank':.4,'recon':.2,'cap':.4}],task_weight_status='assumed_not_observed')
        e=self.engine();own=e.variants[0];context=(own['br_step'],self.c['scenarios'][0]['id'])
        native=e.mission(own['key'],'anti_tank',*context);row=e.phase(own['key'],0,*context)
        self.assertAlmostEqual(row['mean'],.4*native['mean']+.2*.5)
        self.assertAlmostEqual(row['lower'],.4*native['lower'])
        self.assertAlmostEqual(row['upper'],.4*native['upper']+.2)
        self.assertAlmostEqual(row['assumed_coverage'],.2)
        trace=self.cache.evidence_get(row['mission_key'])['value']['evidence_trace']['task_mixture']
        self.assertEqual(trace['anti_tank']['mission_key'],native['mission_key'])
        self.assertEqual(trace['cap']['reason'],'task_domain_inapplicable')

    def test_full_task_multination_multibr_search_matches_independent_sortie_oracle(self):
        for v in self.cat['vehicles']:
            v['rating_configurations']={'ground_rb':{'status':'official_verified','source':{'url':'synthetic'},'options':[
                {'id':'low','br':{'current':9.3},'allowed_ammo_ids':[v['ammo'][0]['id']]},
                {'id':'high','br':{'current':9.7},'allowed_ammo_ids':[v['ammo'][0]['id']]}]}}
        add_air(self.cat)
        for v in self.cat['vehicles']:
            if v['kind']!='ground':v.pop('rating_configurations',None)
        self.c['spawn'].update({k:v for k,v in read_json(FULL)['task_profiles']['balanced'].items() if k!='publication_scope'})
        self.c['spawn'].update(air_cost=150,point_gains=[{'points':0,'probability':1.}])
        self.c['search'].update(slots=[2,3],beam_width=128,max_evaluations=5000)
        e=self.engine();o=Optimizer(e);world=o.world()
        self.assertEqual({t['nation'] for t in world['tables']},{'a','b'})
        self.assertEqual({t['cap_step'] for t in world['tables']},{28,29})
        self.assertTrue(any(r['composition']=='combined_arms' for t in world['tables'] for r in t['candidates']))
        for table in world['tables']:
            pool=[v for v in e.variants if v['nation']==table['nation'] and v['br_step']<=table['cap_step']]
            brute=[o.evaluate(tuple(sorted(v['key'] for v in lineup))) for n in range(1,table['slots']+1)
                for lineup in itertools.combinations(pool,n) if feasible(lineup,table['slots'])
                and max(v['br_step'] for v in lineup)==table['cap_step']]
            eligible=[r for r in brute if r['rankable']]
            self.assertTrue(eligible);self.assertFalse(table['budget_exhausted'])
            best=table['candidates'][0]
            self.assertAlmostEqual(best['estimate'],max(r['estimate'] for r in eligible))
            self.assertEqual(set(best['mission_coverage']),TASKS)
            # Enumerate sortie sequences independently of the dynamic program.
            for case in best['cases']:
                values=[]
                for sequence in itertools.product([None,*best['keys']],repeat=3):
                    used=[k for k in sequence if k is not None]
                    if len(set(used))!=len(used):continue
                    values.append(sum(weight * sum(tw * e.mission(key,task,case['room_top_step'],case['scenario'])['mean']
                        for task,tw in mix.items()) for key,weight,mix in
                        zip(sequence,self.c['spawn']['phase_weights'],e.phase_tasks) if key is not None))
                self.assertAlmostEqual(case['mean'],max(values))
            o.explain(best)
            self.assertEqual(len(best['reserve_contributions']),best['lineup_size'])
        self.assertEqual(world['boards'],Optimizer(e).world()['boards'])


class FullProfileTests(unittest.TestCase):
    def test_default_cohort_full_grid_and_ground_research_scope(self):
        source=read_json(FULL);c,chosen=resolve(source)
        self.assertEqual(chosen,('normal','balanced'))
        self.assertEqual(c['search']['slots'],[5,8,10])
        self.assertEqual(modeled_tasks(c['spawn']),TASKS)
        self.assertEqual(len(c['scenarios']),96)
        self.assertEqual({(s['physical_distance_m'],s['yaw_deg']) for s in c['scenarios']},
            {(d,yaw) for d in (10,100,500,1000) for yaw in range(0,360,15)})
        self.assertTrue(all(s['physical_distance_m']==s['aiming_distance_m'] for s in c['scenarios']))
        self.assertEqual(c['spawn']['status'],'assumed_not_official_rules')
        self.assertIsNone(c['spawn']['air_cost'])
        self.assertEqual(cohorts(source)[0],chosen);self.assertEqual(len(set(cohorts(source))),9)
        ground,_=resolve(source,preference='ground')
        self.assertEqual(ground['publication']['scope'],'ground_engagement')
        self.assertNotIn('phase_task_weights',ground['spawn'])
        legacy=settings();unchanged,_=resolve(legacy)
        self.assertEqual(unchanged,legacy)

    def test_invalid_mixtures_and_cohorts_rejected(self):
        for mutation in ({'phase_task_weights':[{'recon':.2}]},
                         {'phase_task_weights':[{'made_up':1.}]*3},
                         {'phase_task_weights':[{'recon':True}]*3},
                         {'task_weight_status':'validated'}):
            spawn=read_json(FULL)['spawn'];spawn.update(mutation)
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):phase_tasks(spawn)
        c=read_json(FULL);c['default_cohort']['preference']='missing'
        with self.assertRaises(ValueError):resolve(c)

    def test_actual_default_build_and_all_profile_root_are_balanced(self):
        with tempfile.TemporaryDirectory() as temporary:
            r=Path(temporary);catalog,atlas,ammo=fixture(r);add_air(catalog)
            (r/'catalog.json').write_text(canonical(catalog),encoding='utf-8')
            c=read_json(FULL);c['scenarios']=settings()['scenarios'];c['search'].update(slots=[2],max_evaluations=1000)
            c['skill_profiles']={k:{**v,'sd':0,'nodes':1} for k,v in c['skill_profiles'].items()}
            (r/'config.json').write_text(canonical(c),encoding='utf-8')
            command=[sys.executable,'scripts/build_forecast.py','--catalog',str(r/'catalog.json'),'--ammunition',str(ammo),
                '--atlas',str(atlas),'--config',str(r/'config.json'),'--index',str(r/'index.sqlite'),
                '--cache',str(r/'cache.sqlite'),'--out',str(r/'forecast')]
            for extra in ([],['--all-profiles']):
                run=subprocess.run([*command,*extra],cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
                self.assertEqual(run.returncode,0,run.stderr)
                index=read_json(r/'forecast/index.json');report=read_json(r/'forecast'/index['report']['url'])
                self.assertEqual(report['task_preference'],'balanced')
                self.assertEqual(report['skill_profile'],'normal')
                self.assertEqual(report['ranking_scope'],'full_ground_rb')
                self.assertFalse(report['published_rankings'])
                self.assertNotIn('required_tasks_not_modeled',report['release_readiness']['current']['why_not_publishable'])
                self.assertIn('spawn_rules_assumed',report['release_readiness']['current']['why_not_publishable'])
                self.assertIn('synthetic_fixture_not_production',report['release_readiness']['current']['why_not_publishable'])
                if extra:
                    self.assertEqual(index['cohorts'][0]['preference'],'balanced')
                    child=r/'forecast/profiles/normal-ground';ci=read_json(child/'index.json')
                    self.assertEqual(read_json(child/ci['report']['url'])['ranking_scope'],'ground_engagement')


if __name__=='__main__':unittest.main()
