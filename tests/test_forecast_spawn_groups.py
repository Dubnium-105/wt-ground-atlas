import copy
import unittest
from test_forecast import settings
from wt_atlas.forecast.capabilities import spawn_cost, spawn_group
from wt_atlas.forecast.search import sortie_policy


def variant(group='tank', domain='ground'):
    return {'version':'fixture','domain':domain,'br_step':15,'vehicle':{'spawn_rule':{
        'status':'official_verified','source':'synthetic test of declared rules','source_version':'fixture',
        'base_points':100,'room_costs':{'3':80},'spawn_group':group,
        'respawn_model':'power_of_count','respawn_power':1}}}


class SpawnGroupTests(unittest.TestCase):
    def test_count_power_uses_third_sortie_factor_three_not_four(self):
        v=variant();s=settings()['spawn']
        self.assertEqual([spawn_cost(v,s,15,n) for n in range(4)],[100,200,300,400])
        self.assertEqual(spawn_cost(v,s,18,2),240)
        v['vehicle']['spawn_rule'].update(respawn_power=0)
        self.assertEqual(spawn_cost(v,s,15,3),100)

    def test_carried_payload_overrides_vehicle_spawn_category(self):
        v=variant('fighter','air');rule=copy.deepcopy(v['vehicle']['spawn_rule'])
        rule['spawn_group']='bomber';v['loadout']={'spawn_rule':rule}
        self.assertEqual(spawn_group(v),'bomber')
        v.pop('loadout');self.assertEqual(spawn_group(v),'fighter')
        v['vehicle'].pop('spawn_rule');self.assertEqual(spawn_group(v),'air')

    def test_policy_preserves_points_for_independent_category(self):
        s=settings()['spawn'];s.update(initial_points=200,phase_weights=[.5,.5],point_gains=[{'points':0,'probability':1.}])
        yes={'mean':1.,'lower':1.,'upper':1.,'coverage':1.}
        no={**yes,'usable':False}
        rows=[[yes,no],[no,yes]]
        vehicles=[variant('fighter','air'),variant('bomber','air')]
        cost=lambda i,n:spawn_cost(vehicles[i],s,15,n)
        actual=sortie_policy(rows,[100,100],s,spawn_groups=list(map(spawn_group,vehicles)),cost_function=cost)
        merged=sortie_policy(rows,[100,100],s,domains=['air','air'],cost_function=cost)
        self.assertEqual(actual['mean'],1.)
        self.assertEqual(actual['policy'][1]['actions'],[{'member_index':1,'probability':1.}])
        self.assertEqual(merged['mean'],.5)

    def test_malformed_or_ambiguous_source_rules_are_rejected(self):
        for patch in [{'respawn_power':float('nan')},{'respawn_power':-1},{'spawn_group':''},
                      {'respawn_model':'unknown'},{'respawn_multiplier':2}]:
            v=variant();v['vehicle']['spawn_rule'].update(patch)
            with self.subTest(patch=patch),self.assertRaises(ValueError):spawn_cost(v,settings()['spawn'],15,2)
        for count in [-1,True,1.5]:
            with self.subTest(count=count),self.assertRaises(ValueError):spawn_cost(variant(),settings()['spawn'],15,count)

    def test_existing_explicit_multiplier_rule_remains_compatible(self):
        v=variant();rule=v['vehicle']['spawn_rule']
        rule.pop('respawn_power');rule.pop('respawn_model');rule['respawn_multiplier']=2
        self.assertEqual(spawn_cost(v,settings()['spawn'],15,2),400)


if __name__=='__main__':unittest.main()
