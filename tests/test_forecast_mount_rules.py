import copy
import unittest
from test_forecast_rule_snapshot import SOURCE
from wt_atlas.forecast.mount_rules import import_mount_rule,check_mounts,audit_vehicle_mounts
from wt_atlas.forecast.capabilities import loadout_errors


def unit():
    # Synthetic source grammar: coupled tanks, one missile vs. rocket exclusion.
    return {'weapons':{'preset_alpha':{},'custom_presets':{'slot':[
        {'index':1,'tank_left':{'tank':1,'DependentWeaponPreset':{'tank_right':4}},'gun':{'gun':1}},
        {'index':2,'rockets':{'rocket_container':2}},
        {'index':3,'missile':{'missile':1,'BannedWeaponPreset':{'rockets':2}}},
        {'index':4,'tank_right':{'tank':1,'DependentWeaponPreset':{'tank_left':1}}}]}}}


class MountRulesTests(unittest.TestCase):
    def test_dependencies_exclusions_and_counts(self):
        rule=import_mount_rule(unit(),SOURCE)
        check=lambda mounts:check_mounts([{'slot':s,'preset':p} for s,p in mounts],rule)
        self.assertEqual(check([(1,'tank_left'),(4,'tank_right'),(2,'rockets')]),
                         {'errors':[],'selected_weapons':{'rocket_container':2,'tank':2}})
        self.assertEqual(check([(1,'tank_left')])['errors'],['required_mount_preset_missing'])
        self.assertEqual(check([(2,'rockets'),(3,'missile')])['errors'],['banned_mount_preset_combination'])
        self.assertEqual(check([(3,'missile')])['errors'],[])
        self.assertEqual(check([(1,'gun'),(1,'tank_left')])['errors'],['duplicate_mount_slot'])
        self.assertEqual(check([(5,'gun')])['errors'],['mount_preset_not_in_source'])
        self.assertEqual(check([])['errors'],['mount_selection_missing'])

    def test_source_shape_cannot_silently_drop_constraints(self):
        for change in ('unknown','duplicate','boolean_count'):
            u=unit();slots=u['weapons']['custom_presets']['slot']
            if change=='unknown':slots[1]['rockets']['FutureRestriction']={'x':1}
            if change=='duplicate':slots.append(copy.deepcopy(slots[0]))
            if change=='boolean_count':slots[1]['rockets']['rocket_container']=True
            with self.subTest(change=change),self.assertRaises(ValueError):import_mount_rule(u,SOURCE)

    def test_missing_required_preset_rejects_only_affected_choices(self):
        u=unit();u['weapons']['custom_presets']['slot'][0]['tank_left']['DependentWeaponPreset']={'absent':4}
        rule=import_mount_rule(u,SOURCE)
        self.assertEqual(rule['unresolved_relations'],[{'slot':1,'preset':'tank_left','relation':'requires',
            'target':{'slot':4,'preset':'absent'},'reason':'referenced_preset_absent'}])
        self.assertEqual(check_mounts([{'slot':1,'preset':'gun'}],rule)['errors'],[])
        self.assertEqual(check_mounts([{'slot':1,'preset':'tank_left'},{'slot':4,'preset':'tank_right'}],rule)['errors'],
                         ['required_mount_preset_missing'])
        result=check_mounts([{'slot':1,'preset':'tank_left'},{'slot':4,'preset':'absent'}],rule)
        self.assertIn('mount_preset_not_in_source',result['errors'])
        self.assertIn('required_mount_preset_missing',result['errors'])

    def test_missing_exclusion_is_retained_without_inventing_a_target(self):
        u=unit();u['weapons']['custom_presets']['slot'][2]['missile']['BannedWeaponPreset']={'absent':99}
        rule=import_mount_rule(u,SOURCE)
        self.assertEqual(rule['unresolved_relations'][0]['relation'],'excludes')
        self.assertEqual(rule['slots']['3']['missile']['excludes'],[{'slot':99,'preset':'absent'}])
        self.assertEqual(check_mounts([{'slot':3,'preset':'missile'}],rule)['errors'],[])
        self.assertIn('mount_preset_not_in_source',check_mounts([{'slot':99,'preset':'absent'}],rule)['errors'])

    def test_actual_missing_selection_is_not_removed_from_the_source_audit(self):
        u=unit();u['weapons']['custom_presets']['slot'][0]['tank_left']['DependentWeaponPreset']={'absent':4}
        v={'id':'fixture','loadouts':[{'id':'preset_alpha','legal':False,
            'mounts':[{'slot':1,'preset':'tank_left'}],'weapons':[]}]}
        result=audit_vehicle_mounts(v,u,SOURCE)
        self.assertEqual(result['status'],'constraints_imported')
        self.assertEqual(v['mount_rule_audit']['unresolved_relations'],result['unresolved_relations'])
        self.assertEqual(result['loadouts'][0]['errors'],['required_mount_preset_missing'])
        self.assertFalse(v['loadouts'][0]['legal'])

    def test_repeated_preset_references_keep_every_slot(self):
        u=unit();slots=u['weapons']['custom_presets']['slot']
        slots.append({'index':5,'rockets':{'rocket_container':1}})
        slots[2]['missile']['BannedWeaponPreset']={'rockets':[2,5]}
        rule=import_mount_rule(u,SOURCE)
        for slot in (2,5):
            result=check_mounts([{'slot':3,'preset':'missile'},{'slot':slot,'preset':'rockets'}],rule)
            self.assertEqual(result['errors'],['banned_mount_preset_combination'])

    def test_audit_preserves_quarantine_and_optimizer_enforces_imported_mounts(self):
        v={'id':'fixture','loadouts':[{'id':'preset_alpha','legal':False,'source_status':'unvalidated',
            'mounts':[{'slot':2,'preset':'rockets'},{'slot':3,'preset':'missile'}],'weapons':[]}]}
        report=audit_vehicle_mounts(v,unit(),SOURCE)
        self.assertEqual(report['loadouts'][0]['status'],'source_constraints_rejected')
        self.assertFalse(v['loadouts'][0]['legal'])
        self.assertEqual(v['loadouts'][0]['source_status'],'unvalidated')
        l={**v['loadouts'][0],'legal':True}
        self.assertIn('banned_mount_preset_combination',loadout_errors(l,v,{}))
        l['mounts']=[{'slot':3,'preset':'missile'}]
        self.assertEqual(loadout_errors(l,v,{}),[])

    def test_no_custom_mounts_or_unknown_format_never_promotes_payload(self):
        v={'id':'fixture','loadouts':[{'id':'preset_alpha','legal':False}]}
        self.assertEqual(audit_vehicle_mounts(v,{'weapons':{'preset_alpha':{}}},SOURCE)['loadouts'][0]['status'],'payload_unverified')
        u=unit();u['weapons']['custom_presets']['slot'][0]['gun']['NewConstraint']=[]
        self.assertEqual(audit_vehicle_mounts(v,u,SOURCE)['status'],'unsupported_source_constraints')
        self.assertFalse(v['loadouts'][0]['legal'])

if __name__=='__main__':unittest.main()
