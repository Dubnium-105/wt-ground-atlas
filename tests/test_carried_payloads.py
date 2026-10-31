"""Synthetic carried-device fixtures; no gameplay effectiveness observations."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from wt_atlas.client_payloads import CapturedPayloads
from wt_atlas.common import read_json
from wt_atlas.forecast.capabilities import loadout_errors
from test_forecast_client_payloads import blk,SOURCE


class CarriedPayloadTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.triggers=read_json(ROOT/'config/sources.json')['datamine']['external_weapon_triggers']
        self.vehicle={'id':'plane','kind':'aircraft','missing':[],'loadouts':[]}
        self.unit={'weapons':{'p':{}}}
        self.put('gamedata/flightmodels/plane.blk',{'weapon_presets':{'preset':{
            'name':'p','blk':'preset.blk'}}})
        self.put('gun.blk',{'cannon':True,'bullets':180,'mass':102.,
            'bullet':[{'bulletName':'a'},{'bulletType':'he'}]})
        self.put('tank.blk',{'fuelTankGun':True,'payload':{
            'bulletName':'fuel_tank','mass':42.,'type':'fmModule','itemType1':'fuelTank'}})

    def tearDown(self):self.temp.cleanup()
    def put(self,path,data):
        p=self.root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(blk(data),encoding='utf-8')
    def verify(self,weapons):
        self.put('preset.blk',{'Weapon':weapons})
        return CapturedPayloads(self.root,'fixture','a'*40,self.triggers).verify(self.vehicle,self.unit,SOURCE)
    def payload(self,weapons):
        v,r=self.verify(weapons);self.assertEqual(r['verified'],1,r)
        self.verified_vehicle=v
        return v['loadouts'][0]

    def test_gun_quantity_is_not_ammunition_and_mount_mass_overrides_resource(self):
        l=self.payload({'blk':'gun.blk','trigger':'cannon','bullets':150,'mass':210.})
        w=l['weapons'][0]
        self.assertEqual((w['type'],w['count'],w['ammunition_per_unit'],w['ammunition_count']),('gun',1,150,150))
        self.assertEqual(w['mass_kg'],210.)
        self.assertEqual(w['mass_source'],'mount.mass')
        self.assertEqual(w['ammunition_selection_status'],'not_selected')
        self.assertFalse(w['mass_complete']);self.assertEqual(len(w['provenance']['source_sha256']),64)
        self.assertNotIn('penetration_mm',w);self.assertNotIn('validated_hit_probability',w)

    def test_identical_guns_merge_devices_and_rounds_but_keep_each_mount(self):
        w={'blk':'gun.blk','trigger':'additional gun','bullets':150}
        l=self.payload([w,copy.deepcopy(w)])
        self.assertEqual(len(l['weapons']),1)
        gun=l['weapons'][0]
        self.assertEqual((gun['count'],gun['ammunition_count'],gun['ammunition_per_unit']),(2,300,150))
        self.assertEqual([a['selection_index'] for a in gun['mount_assignments']],[0,1])
        self.assertEqual([a['source_weapon_index'] for a in gun['mount_assignments']],[0,0])

    def test_different_gun_mass_or_round_count_never_collapses(self):
        w={'blk':'gun.blk','trigger':'machine gun','bullets':150,'mass':210.}
        l=self.payload([w,{**w,'bullets':75},{**w,'mass':102.}])
        self.assertEqual(len(l['weapons']),3)
        self.assertEqual(sum(g['ammunition_count'] for g in l['weapons']),375)
        self.assertEqual(sum(g['mass_kg']*g['count'] for g in l['weapons']),522)

    def test_conflicting_repeated_ammunition_counts_remain_unknown_not_summed(self):
        source={'blk':'gun.blk','trigger':'gunner0','bullets':[460,250]}
        l=self.payload([source,copy.deepcopy(source)])
        w=l['weapons'][0]
        self.assertEqual(w['count'],2);self.assertIsNone(w['ammunition_count'])
        self.assertIsNone(w['ammunition_per_unit'])
        self.assertEqual(w['declared_ammunition_quantities'],[460,250])
        self.assertEqual(w['ammunition_quantity_status'],'ambiguous_repeated_parameter')

    def test_identical_repeated_counts_do_not_duplicate_ammunition(self):
        w=self.payload({'blk':'gun.blk','trigger':'gunner0','bullets':[250,250]})['weapons'][0]
        self.assertEqual(w['count'],1);self.assertEqual(w['ammunition_count'],250)
        self.assertEqual(w['declared_ammunition_quantities'],[250,250])

    def test_fuel_tank_is_retained_without_fuel_or_attack_estimate(self):
        w=self.payload({'blk':'tank.blk','trigger':'fuel tanks','bullets':2})['weapons'][0]
        self.assertEqual((w['type'],w['count'],w['mass_kg']),('fuel_tank',2,42.))
        self.assertFalse(w['combat_delivery']);self.assertFalse(w['mass_complete'])
        self.assertEqual(w['consumable_state'],'fuel_load_not_selected')
        self.assertNotIn('range_m',w);self.assertNotIn('endurance_s',w)

    def test_targeting_pod_and_countermeasure_counts_have_distinct_semantics(self):
        self.put('pod.blk',{'targetingPodGun':True,'payload':{'mass':230.}})
        self.put('cm.blk',{'cannon':False,'mass':0.,'bullets':90,
                           'bullet':{'bulletName':'flare'}})
        l=self.payload([{'blk':'pod.blk','trigger':'targetingPod','bullets':1},
                        {'blk':'cm.blk','trigger':'countermeasures','bullets':240}])
        pod,cm=l['weapons']
        self.assertEqual((pod['type'],pod['count']),('targeting_pod',1))
        self.assertEqual((cm['type'],cm['count'],cm['ammunition_count']),('countermeasure_dispenser',1,240))
        self.assertTrue(all(not w['combat_delivery'] for w in l['weapons']))
        self.assertEqual(cm['ammunition_selection_status'],'not_selected')

    def test_booster_keeps_unresolved_propellant_and_missing_mass(self):
        self.put('booster.blk',{'boosterGun':True,'payload':{'type':'fmModule','itemType1':'engine'}})
        w=self.payload({'blk':'booster.blk','trigger':'boosters','bullets':1})['weapons'][0]
        self.assertEqual(w['type'],'booster');self.assertFalse(w['combat_delivery'])
        self.assertIsNone(w['mass_kg']);self.assertFalse(w['mass_complete'])
        self.assertEqual(w['consumable_state'],'propellant_state_not_selected')

    def test_source_machine_gun_can_omit_optional_cannon_flag(self):
        self.put('machine.blk',{'bullets':500,'mass':23.,'bullet':[{'bulletType':'ap'},{'bulletType':'i'}]})
        w=self.payload({'blk':'machine.blk','trigger':'machine gun','bullets':100})['weapons'][0]
        self.assertEqual((w['type'],w['count'],w['ammunition_count']),('gun',1,100))

    def test_torpedo_has_its_own_type_and_no_ground_hit_rate(self):
        self.put('torpedo.blk',{'torpedoGun':True,'torpedo':{'mass':816.,'explosiveMass':224.,'distToLive':5500.}})
        w=self.payload({'blk':'torpedo.blk','trigger':'torpedoes','bullets':1})['weapons'][0]
        self.assertEqual(w['type'],'torpedo');self.assertEqual(w['mass_kg'],816.)
        self.assertIsNone(w['range_m']);self.assertNotIn('validated_hit_probability',w)

    def test_unknown_or_invalid_device_quantity_does_not_promote_preset(self):
        for quantity in (None,-1,True,1.5):
            with self.subTest(quantity=quantity):
                w={'blk':'tank.blk','trigger':'fuel tanks'}
                if quantity is not None:w['bullets']=quantity
                _,r=self.verify(w)
                self.assertEqual(r['verified'],0)
                self.assertIn('quantity unknown or invalid',r['parser_errors'][0]['error'])
        self.put('gun.blk',{'cannon':True,'bullet':{'bulletName':'a'}})
        _,r=self.verify({'blk':'gun.blk','trigger':'cannon'})
        self.assertEqual(r['verified'],0)
        self.assertIn('ammunition quantity',r['parser_errors'][0]['error'])

    def test_trigger_label_alone_cannot_reclassify_a_resource(self):
        for trigger in ('fuel tanks','targetingPod','torpedoes'):
            with self.subTest(trigger=trigger):
                _,r=self.verify({'blk':'gun.blk','trigger':trigger,'bullets':1})
                self.assertEqual(r['verified'],0)
                self.assertIn('unsupported carried',r['parser_errors'][0]['error'])

    def test_mount_modification_is_required_by_ownership_filter(self):
        l=self.payload({'blk':'gun.blk','trigger':'cannon','reqModification':'gun-pod'})
        self.assertEqual(l['required_modification_ids'],['gun-pod'])
        self.assertIn('modifications_unavailable',loadout_errors(l,self.verified_vehicle,{},unlocked=[],require_unlocked=True))
        self.assertEqual(loadout_errors(l,self.verified_vehicle,{},unlocked=['gun-pod'],require_unlocked=True),[])

    def test_known_device_mass_still_blocks_overload_with_other_mass_unknown(self):
        l=self.payload({'blk':'tank.blk','trigger':'fuel tanks','bullets':2})
        l['weapons'].append({'id':'unknown','count':1,'mass_kg':None})
        self.assertIn('payload_capacity_exceeded',loadout_errors(l,{**self.verified_vehicle,'max_payload_kg':80},{}) )
        self.assertEqual(loadout_errors(l,{**self.verified_vehicle,'max_payload_kg':100},{}),[])


if __name__=='__main__':unittest.main()
