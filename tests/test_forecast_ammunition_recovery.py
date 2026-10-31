import copy
import csv
import gzip
import hashlib
from html import escape
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT / 'backend'))
from wt_atlas.ammunition_recovery import (Localization, MountedAmmunition, ke_profile, consolidate,
    recover, source_rows, verify_wiki, wiki_tables)
from wt_atlas.common import file_hash, write_json


def blk(data, indent=''):
    rows = []
    for key, value in data.items():
        if isinstance(value, tuple):
            rows.append(indent + key + ':p2 = ' + ', '.join(map(str, value))); continue
        for v in value if isinstance(value, list) else [value]:
            if isinstance(v, dict): rows += [indent + key + ' {', blk(v, indent + '  '), indent + '}']
            elif isinstance(v, bool): rows.append(indent + key + ':b = ' + ('yes' if v else 'no'))
            elif isinstance(v, (int, float)): rows.append(indent + key + ':r = ' + str(v))
            else: rows.append(indent + key + ':t = "' + v + '"')
    return '\n'.join(rows) + '\n'


class AmmunitionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.lang = self.root / 'lang'; self.lang.mkdir()
        self.localization({'belt/name': '.50 API-T', 'mixed/name': 'Mixed', 'round_m20': 'M20'})
        self.bullet = {'bulletName': 'round_m20', 'bulletType': 'ap_i_t_ball_M20',
            'caliber': .0127, 'mass': .04, 'speed': 900., 'slopeEffectPreset': 'ap', 'ricochetPreset': 'default',
            'damage': {'kinetic': {'damageType': 'generic'}}}
        self.unit = {'commonWeapons': {'Weapon': [{'blk': 'gamedata/weapons/gun.blk', 'trigger': 'gunner0'},
            {'blk': 'gamedata/weapons/gun.blk', 'trigger': 'gunner1'}]},
            'modifications': {'belt': {}, 'mixed': {}}}
        self.weapon = {'isBulletBelt': True, 'bullet': self.bullet, 'belt': {'bullet': self.bullet},
            'mixed': {'bullet': [self.bullet, {**self.bullet, 'bulletName': 'other', 'mass': .06}]}}
        self.put('gamedata/units/tankmodels/vehicle.blk', self.unit)
        self.put('gamedata/weapons/gun.blk', self.weapon)
        self.ammo = {'id': 'vehicle::API-T', 'name': '.50 API-T', 'caliber_mm': 12.7,
            'weapon_id': 'vehicle::weapon:0', 'provenance': {'parent_weapon_name': 'Test gun'},
            'penetration': [{'distance_m': 10., 'angle_deg': 0., 'mm': 29.},
                            {'distance_m': 100., 'angle_deg': 0., 'mm': 27.}]}
        self.tables = {('Test gun', '.50 API-T'): {'points': self.ammo['penetration'], 'maximum_only': True}}
        self.response = {'slope': {'ap': {'table': {'caliberToArmor': 1., 'slopeEffect0': [0., 4.], 'slopeEffect90': [90., 1.]}}},
            'ricochet': {'default': {'table': {'caliberToArmor': 1., 'ricochetProbability0': [0., 1.], 'ricochetProbability90': [90., 0.]}}},
            'dispersion': .1, 'hashes': {}}
        self.source = {'sha256': 'a' * 64, 'url': 'https://wiki.warthunder.com/unit/vehicle'}

    def tearDown(self): self.temp.cleanup()
    def put(self, name, data):
        path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(blk(data), encoding='utf-8')
    def localization(self, values):
        for name in ('units_modifications.csv', 'units_weaponry.csv'):
            with (self.lang / name).open('w', encoding='utf-8-sig', newline='') as stream:
                writer = csv.writer(stream, delimiter=';'); writer.writerow(['<ID|readonly|noverify>', '<English>'])
                if name == 'units_modifications.csv': writer.writerows(values.items())
    def adapter(self): return MountedAmmunition(self.root, Localization(self.lang))
    def binding(self):
        adapter = self.adapter()
        return adapter.bind(adapter.candidates({'id': 'vehicle'}), self.ammo)
    def profile(self, binding=None, ammo=None, tables=None):
        return ke_profile(ammo or self.ammo, binding or self.binding(), tables or self.tables,
            self.source, self.response, 'test', 'a' * 40)

    def test_homogeneous_maximum_table_has_exact_source_and_owned_profile(self):
        p = self.profile(); self.assertEqual(p['normalized_distance_curve'], [[10., 1.], [100., 27 / 29]])
        self.assertEqual(p['penetration_mm'], 29); self.assertEqual(p['damage_caliber_mm'], 12.7)
        self.assertEqual(p['provenance']['penetration_source_ammo_ids'], ['vehicle::API-T'])
        evidence = p['provenance']['recovery_bindings']['vehicle::API-T']
        self.assertEqual(evidence['selector'], 'belt'); self.assertEqual(len(evidence['mounts']), 2)
        self.assertEqual(evidence['ordered_projectiles'], ['round_m20'])
        self.assertTrue(evidence['wiki_maximum_only']); self.assertIn('damage_caliber_mm', p['provenance']['model_assumptions'])

    def test_mixed_belt_maximum_is_never_used_as_each_round(self):
        adapter = self.adapter(); mixed = {**self.ammo, 'name': 'Mixed'}
        binding = adapter.bind(adapter.candidates({'id': 'vehicle'}), mixed)
        self.assertEqual([b['bulletName'] for b in binding['bullets']], ['round_m20', 'other'])
        with self.assertRaisesRegex(ValueError, 'mixed_belt'): self.profile(binding=binding)

    def test_matching_component_name_cannot_select_a_mixed_belt(self):
        self.weapon.pop('bullet'); self.weapon.pop('belt'); self.put('gamedata/weapons/gun.blk', self.weapon)
        adapter = self.adapter()
        with self.assertRaisesRegex(ValueError, 'selection_missing'):
            adapter.bind(adapter.candidates({'id': 'vehicle'}), {**self.ammo, 'name': 'M20'})

    def test_unmounted_and_undeclared_selectors_do_not_bind(self):
        self.unit['modifications'].pop('belt'); self.put('gamedata/units/tankmodels/vehicle.blk', self.unit)
        adapter = self.adapter()
        with self.assertRaisesRegex(ValueError, 'selection_missing'):
            adapter.bind(adapter.candidates({'id': 'vehicle'}), self.ammo)

    def test_same_display_name_on_different_mounted_weapons_is_ambiguous(self):
        self.put('gamedata/weapons/other.blk', self.weapon)
        self.unit['commonWeapons']['Weapon'].append({'blk': 'gamedata/weapons/other.blk'})
        self.put('gamedata/units/tankmodels/vehicle.blk', self.unit); adapter = self.adapter()
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            adapter.bind(adapter.candidates({'id': 'vehicle'}), self.ammo)

    def test_same_projectile_in_default_and_named_selector_is_equivalent(self):
        adapter = self.adapter()
        match = adapter.bind(adapter.candidates({'id': 'vehicle'}), {**self.ammo, 'name': 'M20'})
        self.assertEqual(match['equivalent_selectors'], ['@default', 'belt'])

    def test_repeated_source_identity_is_reported_without_aborting_other_selections(self):
        self.weapon['belt']['bullet'] = {**self.bullet, 'bulletName': ['one', 'two']}
        self.put('gamedata/weapons/gun.blk', self.weapon); adapter = self.adapter()
        candidates = adapter.candidates({'id': 'vehicle'})
        self.assertTrue(candidates)
        self.assertEqual(adapter.rejections[0]['reason'], 'projectile_identity_missing_or_repeated')
        with self.assertRaisesRegex(ValueError, 'selection_missing'): adapter.bind(candidates, self.ammo)

    def test_no_fuzzy_case_or_caliber_name_matching(self):
        adapter = self.adapter()
        with self.assertRaisesRegex(ValueError, 'selection_missing'):
            adapter.bind(adapter.candidates({'id': 'vehicle'}), {**self.ammo, 'name': '.50 api-t'})
        with self.assertRaisesRegex(ValueError, 'caliber_mismatch'):
            self.profile(ammo={**self.ammo, 'caliber_mm': 20.})

    def test_subcaliber_requires_explicit_damage_caliber(self):
        binding = self.binding(); binding['bullets'][0] = {**self.bullet, 'bulletType': 'apds_tank'}
        with self.assertRaisesRegex(ValueError, 'damage_caliber_missing'): self.profile(binding=binding)
        binding['bullets'][0]['damageCaliber'] = .008
        self.assertEqual(self.profile(binding=binding)['damage_caliber_mm'], 8.)

    def test_unverified_damage_mechanism_does_not_default_to_generic(self):
        binding = self.binding(); binding['bullets'][0] = {**self.bullet, 'damage': {}}
        with self.assertRaisesRegex(ValueError, 'mechanism_not_verified'): self.profile(binding=binding)

    def test_catalog_table_change_is_rejected(self):
        ammo = copy.deepcopy(self.ammo); ammo['penetration'][0]['mm'] = 999.
        with self.assertRaisesRegex(ValueError, 'differs_from_exact'): self.profile(ammo=ammo)

    def test_consolidation_preserves_every_mount_proof_and_distinguishes_source_projectiles(self):
        a = self.profile(); b = self.profile(ammo={**self.ammo, 'id': 'other::API-T'})
        merged, renamed = consolidate([a, b]); self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0]['provenance']['recovery_bindings']), 2)
        self.assertEqual(renamed[a['name']], renamed[b['name']])
        b['provenance']['recovery_bindings']['other::API-T']['projectile_sha256'] = '0' * 64
        self.assertEqual(len(consolidate([a, b])[0]), 2)

    def test_source_and_localization_conflicts_are_rejected(self):
        adapter = self.adapter(); adapter.candidates({'id': 'vehicle'})
        self.put('gamedata/weapons/gun.blk', {'bullet': self.bullet})
        with self.assertRaisesRegex(ValueError, 'changed during'): adapter.verify_unchanged()
        with (self.lang / 'units_weaponry.csv').open('a', encoding='utf-8') as stream: stream.write('belt/name;different\n')
        with self.assertRaisesRegex(ValueError, 'Conflicting exact'): Localization(self.lang)

    def test_response_points_must_be_valid_and_nonconflicting(self):
        table = self.response['slope']['ap']['table']; table['slopeEffectDuplicate'] = [90., 2.]
        with self.assertRaisesRegex(ValueError, 'conflicting_native'): source_rows(self.response['slope'], 'ap', 'slopeEffect', True)

    def raw_wiki(self):
        table = '<div>Armor penetration (max.)<table><tr><th>Distance / Angle</th><th>0</th></tr><tr><th>10 m</th><td>29</td></tr><tr><th>100 m</th><td>27</td></tr></table></div>'
        pop = '<div class="game-unit_popover-header"><span>.50 API-T</span></div><button data-bs-title="Armor penetration" data-bs-content="' + escape(table, quote=True) + '"></button>'
        return ('<div class="game-unit_weapon"><div class="game-unit_weapon-title">Test gun</div><button data-feature-popover="' + escape(pop, quote=True) + '"></button></div>').encode()

    def test_wiki_table_keeps_parent_weapon_and_maximum_semantics(self):
        self.assertEqual(wiki_tables(self.raw_wiki()), self.tables)

    def fixture_catalog(self):
        raw = self.raw_wiki(); (self.root / 'unit_vehicle.html.gz').write_bytes(gzip.compress(raw))
        url = self.source['url']
        vehicle = {'id': 'vehicle', 'kind': 'ground', 'wiki_url': url,
            'provenance': [{'url': url, 'sha256': hashlib.sha256(raw).hexdigest()}],
            'ammo': [self.ammo], 'weapon_profiles': [{'id': 'vehicle::weapon:0', 'reload_type': 'automatic', 'fire_rate_rpm': 575.}]}
        return {'snapshot': {'client_source': {'version': 'test'}, 'datamine_game_version': 'test', 'datamine_reference': 'a' * 40}, 'vehicles': [vehicle]}

    def test_wrong_wiki_hash_fails(self):
        vehicle = self.fixture_catalog()['vehicles'][0]; vehicle['provenance'][0]['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'Wiki source hash'): verify_wiki(vehicle, self.root)

    def integration_inputs(self):
        catalog = self.fixture_catalog(); write_json(self.root / 'catalog.json', catalog); write_json(self.root / 'ammo.json', [])
        for name, data in [('slope_effect', self.response['slope']), ('ricochet', self.response['ricochet'])]:
            data = copy.deepcopy(data)
            for preset in data.values():
                for table in preset.values():
                    for k, v in table.items():
                        if isinstance(v, list): table[k] = tuple(v)
            self.put('gamedata/damage_model/' + name + '.blk', data)
        self.put('gamedata/damage_model/damage_system.blk', {'pierceDispersion': .1})
        (self.root / 'pack').write_bytes(b'fixture')
        write_json(self.root / 'capture.json', {'packs': {'aces.vromfs.bin': {'version': 'test', 'saved': str(self.root / 'pack'), 'sha256': file_hash(self.root / 'pack'), 'extracted': str(self.root)}}})
        write_json(self.root / 'language.json', {'version': 'test', 'saved': str(self.root / 'pack'), 'source_sha256': file_hash(self.root / 'pack'), 'extracted': str(self.root)})
        from wt_atlas.forecast.rule_snapshot import SOURCE_PATHS
        rules = self.root / 'rules'; files = {}
        for path in SOURCE_PATHS:
            target = rules / path; target.parent.mkdir(parents=True, exist_ok=True)
            data = b'test' if path == 'version' else b'{}'; target.write_bytes(data)
            files[path] = {'url': 'https://raw.githubusercontent.com/gszabi99/War-Thunder-Datamine/' + 'a' * 40 + '/' + path,
                'sha256': file_hash(target), 'git_blob_sha': hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()}
        write_json(rules / 'capture.json', {'commit': 'a' * 40, 'files': files})
        return [self.root / n for n in ('catalog.json', 'ammo.json', 'capture.json', 'language.json')] + [self.root, rules]

    def test_recovery_pipeline_preserves_inputs_and_restores_bound_weapon_timing(self):
        args = self.integration_inputs(); before = [file_hash(p) for p in args[:4]]
        catalog, profiles, report = recover(*args)
        self.assertEqual(report['counts']['recovered_profiles'], 1); self.assertEqual(len(profiles), 1)
        self.assertEqual(catalog['vehicles'][0]['ammo'][0]['weapon_profile']['fire_rate_rpm'], 575.)
        self.assertEqual(before, [file_hash(p) for p in args[:4]])
        self.assertEqual(report['computed_maps'], 0); self.assertEqual(report['formal_rankings'], 0)

    def test_version_mismatch_and_pack_hash_failure_reject_before_import(self):
        args = self.integration_inputs(); (self.root / 'pack').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'): recover(*args)
        args = self.integration_inputs(); write_json(args[3], {'version': 'other'})
        with self.assertRaisesRegex(ValueError, 'share a version'): recover(*args)

    def test_incremental_import_reuses_verified_profiles_and_rejects_changed_sources(self):
        args = self.integration_inputs(); catalog, profiles, _ = recover(*args)
        write_json(args[0], catalog); write_json(args[1], profiles)
        _, again, report = recover(*args)
        self.assertEqual(again, profiles); self.assertEqual(report['counts']['recovered_profiles'], 0)
        self.weapon['belt']['bullet'] = {**self.bullet, 'mass': .09}
        self.put('gamedata/weapons/gun.blk', self.weapon)
        with self.assertRaisesRegex(ValueError, 'Existing recovered client source changed'): recover(*args)

    def test_pinned_datamine_file_tampering_is_rejected(self):
        args = self.integration_inputs(); (args[-1] / 'version').write_text('other', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'checksum or URL mismatch'): recover(*args)


if __name__ == '__main__': unittest.main()
