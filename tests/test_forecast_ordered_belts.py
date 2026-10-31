"""Ordered projectiles, exact immutable source reuse, and end-to-end coverage."""
import copy
import itertools
import json
import sqlite3
from pathlib import Path
import tempfile
import unittest
import numpy as np
from test_forecast import fixture, settings
from wt_atlas.forecast.combat import duel, weapon_timing
from wt_atlas.forecast.data import Atlas, canonical, digest, file_hash
from wt_atlas.forecast.engine import Forecast, ShotCache, enumerate_variants, variant_projectiles
from wt_atlas.forecast.belts import ordered_bindings
from wt_atlas.forecast.inventory import coverage
from wt_atlas.forecast.search import Optimizer
from wt_atlas.ordered_belt_recovery import recover_belt


def outcome_oracle(a, b, qa, qb, horizon):
    """Enumerate independent Bernoulli outcomes, stopping at the first hit.

    Tiny integer/half-integer schedules avoid tolerance/grouping implementation
    reuse. This is deliberately not the production survival-vector algorithm.
    """
    schedule = {}
    for side, (timing, belt) in enumerate(((a, qa), (b, qb))):
        for i in range(timing['ready_capacity']):
            t = timing['first_s'] + i * timing['cycle_s']
            if t <= min(horizon, a['exposure_end_s'], b['exposure_end_s']):
                schedule.setdefault(t, {})[side] = belt[i % len(belt)]
    groups = [schedule[t] for t in sorted(schedule)]
    answer = 0.
    for bits in itertools.product((False, True), repeat=sum(len(g) for g in groups)):
        probability = 1.; i = 0; score = None
        for group in groups:
            hits = {}
            for side, p in group.items():
                hit = bits[i]; i += 1
                probability *= p if hit else 1-p
                hits[side] = hit
            if score is None and any(hits.values()):
                score = .5 if all(hits.get(s, False) for s in (0, 1)) else float(hits.get(0, False))
        answer += probability * (.5 if score is None else score)
    return answer


class OrderedCombatTests(unittest.TestCase):
    def setUp(self):
        self.c = settings()['timing']
        self.t = {'valid':True, 'first_s':0., 'cycle_s':1., 'exposure_end_s':10., 'ready_capacity':3}

    def test_sequence_matches_independent_outcome_enumeration_and_swap(self):
        qa, qb = [.1, .8], [.6, .2, .4]
        for offset in (0., .5):
            b = {**self.t, 'first_s':offset}
            r = duel(self.t, b, [[0,0,p] for p in qa], [[0,0,p] for p in qb], 3., self.c)
            self.assertAlmostEqual(r['score'], outcome_oracle(self.t, b, qa, qb, 3.))
            swapped = duel(b, self.t, [[0,0,p] for p in qb], [[0,0,p] for p in qa], 3., self.c)
            self.assertAlmostEqual(r['score'] + swapped['score'], 1.)
            self.assertAlmostEqual(sum(map(sum, r['state_mass'])), 1.)
            self.assertIsNone(r['kill_probability'])

    def test_order_is_not_belt_average_or_maximum(self):
        one = {**self.t, 'ready_capacity':1}
        first = duel(one, one, [[0,0,1], [0,0,0]], [0,0,.5], 3., self.c)
        last = duel(one, one, [[0,0,0], [0,0,1]], [0,0,.5], 3., self.c)
        self.assertEqual(first['score'], .75); self.assertEqual(last['score'], .25)

    def test_repeats_without_exceeding_ready_capacity(self):
        r = duel(self.t, self.t, [[0,0,0], [0,0,.5]], [0,0,0], 3., self.c)
        self.assertEqual(r['a_only'], .5)
        self.assertEqual(r['expected_shots'], [2.5,2.5])
        self.assertEqual(r['projectile_sequence_lengths'], [2,1])

    def test_damage_disabling_stops_the_other_sequence(self):
        b = {**self.t, 'first_s':.5}
        r = duel(self.t, b, [[1,0,0], [0,0,1]], [[0,0,1], [0,0,0]], 3., self.c, endpoint='damage')
        self.assertEqual(r['expected_shots'], [2.,0.]); self.assertEqual(r['kill_probability'], 1.)

    def test_unknown_component_bounds_are_monotone(self):
        results = [duel(self.t, self.t, [[0,0,.2], [0,0,q]], [0,0,.3], 3., self.c)['score'] for q in (0.,.5,1.)]
        self.assertLess(results[0],results[1]); self.assertLess(results[1],results[2])

    def test_invalid_sequences_and_collapsed_cadence_rejected(self):
        for q in ([], [[]], [[0,0,float('nan')]], [[.8,.8,0]], [0,[0,0,1]], [[0,0,-.1]]):
            with self.assertRaises(ValueError): duel(self.t, self.t, q, [0,0,0], 3., self.c)
        with self.assertRaisesRegex(ValueError,'tolerance'):
            duel({**self.t,'cycle_s':self.c['simultaneous_tolerance_s']}, self.t, [0,0,0], [0,0,0], 3., self.c)

    def test_manual_magazine_reload_does_not_replace_automatic_cadence(self):
        ammo = {'weapon_profile':{'reload_type':'manual','reload_ace_s':3.,'fire_rate_rpm':480.,'belt_capacity':20.}}
        timing = weapon_timing({},ammo,0.,settings()['scenarios'][0],self.c)
        self.assertEqual(timing['cycle_s'],.125); self.assertEqual(timing['ready_capacity'],20)
        self.assertEqual(timing['cadence_source'],'within_magazine_rpm')
        ammo['ordered_belt']={'binding':{'mounts':[{},{}]}}
        self.assertFalse(weapon_timing({},ammo,0.,settings()['scenarios'][0],self.c)['valid'])


def belt_fixture(root):
    catalog, atlas_root, ammo_path = fixture(root)
    profiles = json.loads(ammo_path.read_text(encoding='utf-8'))
    bullets = []
    for i, profile in enumerate(profiles):
        bullet = {'bulletName':'synthetic_round_' + str(i), 'bulletCount':2, 'mass':1. + i}
        bullets.append(bullet)
        profile['provenance']['recovery_bindings'] = {str(i):{'projectile_sha256':digest(bullet)}}
    for path in (atlas_root/'arrays').glob('*.json'):
        meta = json.loads(path.read_text())
        profile = next(p for p in profiles if p['name'] == meta['ammo_id'])
        meta.update(ammo_profile_sha256=digest(profile), angles_deg=[0.])
        source = atlas_root/meta['array_file']
        with np.load(source, allow_pickle=False) as data: arrays = {k:data[k] for k in data.files}
        arrays['penetration_probability'][:] = .9 if profiles.index(profile) == 0 else .1
        np.savez_compressed(source, **arrays)
        meta['array_sha256'] = file_hash(source)
        path.write_text(canonical(meta))
    ammo_path.write_text(canonical(profiles))
    vehicle = catalog['vehicles'][0]
    ammo = copy.deepcopy(vehicle['ammo'][0]); ammo['id'] = vehicle['id'] + '::mixed'
    binding = {'unit_path':'gamedata/units/tankmodels/' + vehicle['id'] + '.blk', 'unit_sha256':'a'*64,
        'weapon_path':'gamedata/weapons/synthetic.blk', 'weapon_sha256':'b'*64, 'selector':'synthetic_mixed',
        'selection_sha256':digest(bullets[:2]), 'mounts':[{'trigger':'gunner0'}], 'bullets':bullets[:2]}
    recover_belt(vehicle, ammo, binding, {digest(b):[p] for b,p in zip(bullets,profiles)},
        'synthetic-only', 'synthetic-commit', {'url':'synthetic://fixture','sha256':'c'*64})
    vehicle['ammo'] = [ammo]
    return catalog, atlas_root, ammo_path, profiles


class OrderedBeltIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.catalog,self.atlas_root,self.ammo_path,self.profiles = belt_fixture(self.root)
        self.cache = ShotCache(self.root/'shots.sqlite')

    def tearDown(self): self.cache.close(); self.temp.cleanup()

    def engine(self): return Forecast(self.catalog, Atlas(self.atlas_root,self.ammo_path,self.catalog), settings(), self.cache)

    def test_one_selection_uses_ordered_component_maps_and_pair_trace(self):
        engine = self.engine(); a = next(v for v in engine.variants if v['ammo_id'].endswith('::mixed'))
        b = next(v for v in engine.variants if v['nation']=='b')
        self.assertEqual(len(engine.variants),4); self.assertEqual(len(variant_projectiles(a)),2)
        scenario = settings()['scenarios'][0]
        pa = [engine.shot(a,b,scenario,0.,projectile_index=i) for i in range(2)]
        self.assertGreater(pa[0]['mean'],pa[1]['mean'])
        self.assertNotEqual(pa[0]['aim_cache_key'],pa[1]['aim_cache_key'])
        pair = engine.pair(a['key'],b['key'],scenario['id'])
        self.assertGreater(pair['coverage'],0.)
        evidence = self.cache.evidence_get(pair['pair_key'])['value']
        self.assertEqual(evidence['timing'][0]['a_projectile_shot_keys'], [p['aim_cache_key'] for p in pa])
        self.assertIn('assumed_belt_start_and_repeating_source_order',pair['missing'])
        self.assertIsNone(engine.shot(a,b,{**scenario,'physical_distance_m':500},0.,projectile_index=1))
        lineup=Optimizer(engine).evaluate(tuple(sorted(v['key'] for v in engine.variants if v['nation']==a['nation'])))
        member=next(m for m in lineup['members'] if m['ammo_id']==a['ammo_id'])
        self.assertIsNone(member['ammo_profile_sha256'])
        self.assertEqual([p['projectile_sha256'] for p in member['ordered_belt']['projectiles']],
                         [p['projectile_sha256'] for p in a['ammo']['ordered_belt']['projectiles']])

    def test_missing_second_component_is_unknown_and_not_covered(self):
        engine = self.engine(); a = next(v for v in engine.variants if v['ammo_id'].endswith('::mixed'))
        b = next(v for v in engine.variants if v['nation']=='b')
        before = coverage(self.catalog,engine.atlas,settings())
        del engine.atlas.lookup[(b['vehicle_id'], self.profiles[1]['name'], 10.)]
        pair = engine.pair(a['key'],b['key'],settings()['scenarios'][0]['id'])
        self.assertEqual((pair['lower'],pair['upper'],pair['coverage']), (0.,1.,0.))
        after = coverage(self.catalog,engine.atlas,settings())
        old = next(r for r in before['matrix'] if r['vehicle_id']==a['vehicle_id'])
        new = next(r for r in after['matrix'] if r['vehicle_id']==a['vehicle_id'])
        self.assertEqual(old['available_forward_pairs'] - new['available_forward_pairs'], 1)

    def test_hash_ownership_order_and_version_mutations_are_rejected(self):
        vehicle = self.catalog['vehicles'][0]; originals = copy.deepcopy(vehicle)
        mutations = [lambda a:a['ordered_belt']['projectiles'].reverse(),
            lambda a:a['ordered_belt'].update(source_version='different'),
            lambda a:a['ordered_belt'].update(vehicle_id='other'),
            lambda a:a['ordered_belt']['projectiles'][0].update(profile_sha256='0'*64)]
        for mutate in mutations:
            vehicle.clear(); vehicle.update(copy.deepcopy(originals)); mutate(vehicle['ammo'][0])
            with self.assertRaises(ValueError): ordered_bindings(vehicle,{p['name']:p for p in self.profiles})

    def test_range_derivative_requires_exact_original_profile_identity(self):
        vehicle = self.catalog['vehicles'][0]
        p = copy.deepcopy(self.profiles[0]); p.update(name='derived',distance_m=500.)
        p['provenance']['range_rebuild'] = {'source_profile_sha256':digest(self.profiles[0])}
        bindings = ordered_bindings(vehicle,{r['name']:r for r in self.profiles+[p]})
        self.assertIn('derived',{r['name'] for _,r in bindings})
        p['provenance']['client_version']='other'
        with self.assertRaisesRegex(ValueError,'source identity'): ordered_bindings(vehicle,{r['name']:r for r in self.profiles+[p]})

    def test_planner_requests_missing_second_component_even_when_first_exists(self):
        from wt_atlas.ordered_belt_planning import plan, include_components
        engine=self.engine(); target=self.catalog['vehicles'][2]['id']; profile=self.profiles[1]['name']
        path=self.root/'index.sqlite';db=sqlite3.connect(path)
        db.execute('CREATE TABLE maps(target,ammo,distance,angles,selected,status)')
        for (t,p,d) in engine.atlas.lookup:
            if (t,p)==(target,profile):continue
            db.execute('INSERT INTO maps VALUES(?,?,?,?,1,?)',(t,p,d,'[0]','verified'))
        db.commit();db.close()
        catalog_path=self.root/'catalog.json';catalog_path.write_text(canonical(self.catalog))
        config_path=self.root/'config.json';config_path.write_text(canonical(settings()))
        report=plan(catalog_path,self.ammo_path,path,config_path)
        self.assertEqual(report['candidate_request_count'],1)
        self.assertEqual(report['candidate_rebuild_requests'][0],{'target_vehicle_id':target,'profile_id':profile,
            'attacker_ids':[self.catalog['vehicles'][0]['id']],'required_yaws_deg':[0.]})
        merged=include_components({**report,'candidate_rebuild_requests':[]},report)
        self.assertEqual(merged['candidate_rebuild_requests'],report['candidate_rebuild_requests'])
        db=sqlite3.connect(path);db.execute("UPDATE maps SET status='metadata_valid'");db.commit();db.close()
        with self.assertRaisesRegex(ValueError,'Finish inventory'):plan(catalog_path,self.ammo_path,path,config_path)


if __name__ == '__main__': unittest.main()
