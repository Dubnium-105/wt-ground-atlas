import copy
import unittest
from unittest.mock import patch

from wt_atlas.system_batch_planning import candidate_requests,validate_plan


class SystemBatchPlanningTests(unittest.TestCase):
    def fixture(self):
        members=[{'vehicle_id':'base','role':'basic','count':1},{'vehicle_id':'part','role':'component','count':2}]
        system={'basic_vehicle_id':'base','members':members,'provenance':{'method':'official-Wiki-multiunit-membership','url':'wiki'}}
        base={'id':'base','kind':'ground','nation':'a','wiki_url':'wiki','system_id':'base','system_role':'basic','multi_vehicle_system':system,'br':{'current':10.}}
        part={'id':'part','kind':'ground','nation':'a','system_id':'base','system_role':'component','multi_vehicle_system':{'members':members}}
        enemy={'id':'enemy','kind':'ground','nation':'b','br':{'current':10.}}
        ally={'id':'ally','kind':'ground','nation':'a','br':{'current':10.}}
        far={'id':'far','kind':'ground','nation':'b','br':{'current':7.}}
        catalog={'vehicles':[base,part,enemy,ally,far]}
        config={'matchmaking':{'spread_steps':3},'scenarios':[{'physical_distance_m':10.,'yaw_deg':0.},
            {'physical_distance_m':10.,'yaw_deg':15.},{'physical_distance_m':100.,'yaw_deg':0.}]}
        profiles=[{'name':'p10','mode':'KE','distance_m':10.},{'name':'p100','mode':'KE','distance_m':100.}]
        class Atlas:
            def bindings(self,vehicle):return vehicle['id']
        def payloads(identifier):return [({'id':identifier+'-round'},profiles,None)]
        available={(target,p['name'],p['distance_m']):({0.,15.} if p['distance_m']==10. else {0.})
                   for target in ('base','part') for p in profiles}
        return catalog,config,Atlas(),payloads,available

    def test_only_complete_enemy_contexts_are_planned(self):
        catalog,config,atlas,payloads,available=self.fixture()
        with patch('wt_atlas.system_batch_planning.ammunition_payloads',side_effect=payloads):
            result=candidate_requests(catalog,atlas,config,available)
            self.assertEqual(len(validate_plan(result,catalog,atlas,config)),4)
            altered=copy.deepcopy(result);altered['requests'][0]['ammo_id']='not-owned'
            with self.assertRaisesRegex(ValueError,'does not replay'):validate_plan(altered,catalog,atlas,config)
        self.assertEqual([(r['system_id'],r['attacker_id'],r['ammo_id']) for r in result['requests']],[('base','enemy','enemy-round')])
        self.assertEqual(len(result['keys']),4)
        available[('part','p10',10.)]={0.}
        with patch('wt_atlas.system_batch_planning.ammunition_payloads',side_effect=payloads):
            self.assertEqual(candidate_requests(catalog,atlas,config,available)['requests'],[])

    def test_unknown_delivery_and_ordered_belt_are_not_promoted(self):
        catalog,config,atlas,payloads,available=self.fixture()
        def invalid(identifier):
            return [({'id':identifier+'-round','ordered_belt':{'declared':True}},
                     [{'name':'p10','mode':'KE','distance_m':10.},{'name':'p100','mode':'KE','distance_m':100.}],None)]
        with patch('wt_atlas.system_batch_planning.ammunition_payloads',side_effect=invalid):
            self.assertEqual(candidate_requests(catalog,atlas,config,available)['requests'],[])
        def limited(identifier):
            return [({'id':identifier+'-round'},[{'name':'p10','mode':'CE','distance_m':10.,
                     'provenance':{'chemical_energy':{'source_min_distance_m':100.}}},
                     {'name':'p100','mode':'KE','distance_m':100.}],None)]
        with patch('wt_atlas.system_batch_planning.ammunition_payloads',side_effect=limited):
            self.assertEqual(candidate_requests(catalog,atlas,config,available)['requests'],[])
        bad=copy.deepcopy(catalog);bad['vehicles'][1]['system_id']='different'
        with self.assertRaisesRegex(ValueError,'Inconsistent'):
            candidate_requests(bad,atlas,config,available)


if __name__=='__main__':unittest.main()
