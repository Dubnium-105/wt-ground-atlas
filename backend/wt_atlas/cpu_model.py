"""NumPy CPU port of the supplied V2 straight-path layer model.

Runs against existing ordered ray/material caches, independently of CUDA. This
preserves the source model's omissions. Float32 CPU interpolation is measured
against the supplied results; it is not asserted to reproduce game arithmetic.
"""
from __future__ import annotations
from pathlib import Path
import time
import numpy as np
from .common import read_json, file_hash, content_hash, write_json

F = np.float32


def _curve(points, values):
    points = np.asarray(points, dtype=np.float32)
    return np.interp(values, points[:, 0], points[:, 1]).astype(np.float32)


def _table(rows, ratio, cosine):
    if not rows:
        return np.zeros_like(ratio, dtype=np.float32)
    knots = np.asarray([row[0] for row in rows], dtype=np.float32)
    curves = np.stack([_curve(row[1], cosine) for row in rows])
    hi = np.searchsorted(knots, ratio, side='left').clip(0, len(rows) - 1)
    lo = (hi - 1).clip(0, len(rows) - 1)
    col = np.arange(len(ratio))
    denominator = knots[hi] - knots[lo]
    weight = np.divide(ratio - knots[lo], denominator, out=np.zeros_like(ratio), where=denominator != 0)
    weight = np.clip(weight, 0, 1)
    return (curves[lo, col] + (curves[hi, col] - curves[lo, col]) * weight).astype(np.float32)


def prepare_materials(materials, ammo, numerics):
    kinds, flags, values, family = [], [], [], []
    kind_ids = numerics['layer_kind']; class_bits = numerics['class_bits']; material_bits = numerics['material_flags']
    for material in materials:
        loaded, class_flags = material.get('loaded_class'), material.get('class_flags')
        kind = kind_ids['unknown']; flag = 0
        value = [0, 0, 0, 0, numerics['empty_limit_mm'], numerics['empty_limit_mm'], 0, 0]
        if material['part_id'] < 0:
            kind = kind_ids['excluded']
        elif class_flags is not None:
            if class_flags & class_bits['internal']:
                kind = kind_ids['protected']
            elif not class_flags & class_bits['armor']:
                kind = kind_ids['excluded']
            elif loaded is not None:
                armor = loaded['armor']
                kind = kind_ids['armor'] if armor['enabled'] and armor['armorThickness'] > 0 else kind_ids['excluded']
                if kind == kind_ids['armor']:
                    arrays = armor['arrays']; mechanism = ammo['mechanism_id']
                    if any(mechanism >= len(arrays[key]['values']) for key in ['ArmorQuality', 'ThicknessEquivalentMax', 'EffectiveThicknessMax']):
                        kind = kind_ids['unknown']
                    else:
                        value = [armor['armorThickness'], float(F(armor['armorQuality']) * F(arrays['ArmorQuality']['values'][mechanism])),
                                 armor['minSolidArmorThickness'], 0, arrays['ThicknessEquivalentMax']['values'][mechanism],
                                 arrays['EffectiveThicknessMax']['values'][mechanism], armor['armorThrough'], loaded['scalars']['slopeMinCaliber']]
                        if bool(material['behavior_flags'] & numerics['behavior_bits']['solid']) and not armor['ignoreSolidDimension']:
                            flag |= material_bits['solid']
                        if material['decoded_flags']['allowRicochet']:
                            flag |= material_bits['ricochet']
        if class_flags is not None and class_flags & class_bits['reactive']:
            flag = (flag & ~material_bits['solid']) | material_bits['reactive_non_geometric']
        family_flag = 0
        sources = material.get('source_armor_classes', [])
        decoded = material.get('decoded_flags') or {}
        if any('nera' in text.lower() for text in sources): family_flag |= 1
        if decoded.get('reactiveArmor'): family_flag |= 2
        if 'spaced_armor_air' in sources or 'grille_air' in sources: family_flag |= 4
        if decoded.get('variableThickness'): family_flag |= 8
        kinds.append(kind); flags.append(flag); values.append(value); family.append(family_flag)
    return np.asarray(kinds, np.int32), np.asarray(flags, np.int32), np.asarray(values, np.float32), np.asarray(family, np.uint8)


class PreparedLayers:
    def __init__(self, metadata, config, verify_hash=True):
        self.metadata_path = Path(metadata)
        self.meta = read_json(metadata)
        self.config = config
        self.numerics = config['numerics']
        self.plan = self.meta['plan']
        packet = self.metadata_path.with_suffix('.npz')
        if verify_hash and file_hash(packet) != self.meta.get('arrays_sha256'):
            raise ValueError('Layer cache hash mismatch')
        if not self.meta.get('all_inward_node_intervals_retained'):
            raise ValueError('V2 requires every inward interval')
        with np.load(packet, allow_pickle=False) as source:
            arrays = {key: source[key] for key in source.files}
        required = ['ray_id', 'node', 'entry_m', 'exit_m', 'incidence_cos', 'extra_entry_m']
        if any(key not in arrays for key in required):
            raise ValueError('Missing layer array')
        if len({arrays[key].shape for key in required}) != 1:
            raise ValueError('Layer array length mismatch')
        self.n = self.plan['width'] * self.plan['height'] * len(self.plan['angles_deg'])
        rays, nodes = arrays['ray_id'], arrays['node']
        if np.any((rays < 0) | (rays >= self.n)) or np.any((nodes < 0) | (nodes >= len(self.meta['materials']))):
            raise ValueError('Layer ray/node index outside metadata bounds')
        if not np.isfinite(arrays['entry_m']).all() or not np.isfinite(arrays['incidence_cos']).all():
            raise ValueError('Invalid ray entry/incidence')
        # +Inf exits deliberately survive: they become unknown for solid armor.
        order = np.lexsort((arrays['entry_m'], rays))
        self.data = {key: np.ascontiguousarray(arrays[key][order]) for key in required if key != 'ray_id'}
        self.offsets = np.r_[0, np.cumsum(np.bincount(rays, minlength=self.n), dtype=np.int64)]
        self.materials = self.meta['materials']

    def calculate(self, ammo, ray_ids=None):
        for key in ['penetration_mm', 'dispersion', 'damage_caliber_mm', 'air_loss_mm_per_m']:
            if not np.isfinite(ammo[key]) or ammo[key] < 0:
                raise ValueError('Invalid ammunition ' + key)
        if ammo['mode'] not in ['KE', 'CE'] or ammo['dispersion'] >= 1:
            raise ValueError('Unsupported mode or dispersion')
        curve = np.asarray(ammo.get('normalized_distance_curve', []), np.float32).reshape(-1, 2)
        if len(curve) and (not np.isfinite(curve).all() or np.any(np.diff(curve[:, 0]) <= 0) or np.any(np.diff(curve[:, 1]) > 0)):
            raise ValueError('Distance curve must be finite and monotone')
        selected = np.arange(self.n) if ray_ids is None else np.asarray(ray_ids, np.int64)
        if np.any((selected < 0) | (selected >= self.n)):
            raise ValueError('Selected ray ID outside grid')
        mat = prepare_materials(self.materials, ammo, self.numerics)
        probability = np.full(len(selected), np.nan, np.float32)
        charge = probability.copy(); status = np.empty(len(selected), np.uint8); risk = status.copy()
        batch = self.config['compute']['cpu_ray_batch']
        if not isinstance(batch, int) or batch <= 0:
            raise ValueError('cpu_ray_batch must be positive')
        for start in range(0, len(selected), batch):
            stop = min(start + batch, len(selected))
            result = self._batch(selected[start:stop], ammo, mat, curve)
            for dest, source in zip([probability, charge, status, risk], result):
                dest[start:stop] = source
        if ray_ids is None:
            shape = (len(self.plan['angles_deg']), self.plan['height'], self.plan['width'])
            return tuple(array.reshape(shape) for array in [probability, charge, status, risk])
        return probability, charge, status, risk

    def _batch(self, ids, ammo, material, curve):
        kinds, mat_flags, mat_values, family = material
        number = len(ids); begins = self.offsets[ids]; counts = self.offsets[ids + 1] - begins
        depth = int(counts.max(initial=0)); status = np.where(counts > 0, 1, 0).astype(np.uint8)
        probability = np.full(number, np.nan, np.float32); required = probability.copy(); risk = np.zeros(number, np.uint8)
        if not depth:
            return probability, required, status, risk
        j = np.arange(depth)
        active = j[None, :] < counts[:, None]
        idx = np.minimum(begins[:, None] + j[None, :], len(self.data['node']) - 1)
        node = self.data['node'][idx]; entry = self.data['entry_m'][idx]
        kind = kinds[node]
        target = np.min(np.where(active & (kind == self.numerics['layer_kind']['protected']), entry, np.inf), axis=1)
        has_target = np.isfinite(target)
        status[has_target] = 3
        preceding = active & (entry < target[:, None]) & has_target[:, None]
        bad = np.any(preceding & (kind == self.numerics['layer_kind']['unknown']), axis=1)
        armor = preceding & (kind == self.numerics['layer_kind']['armor'])
        cost = np.zeros(number, np.float32); survive = np.ones(number, np.float32)
        covered = np.zeros(number, np.float32); started = np.zeros(number, bool)
        charges = np.zeros_like(entry, np.float32)
        caliber = F(ammo['damage_caliber_mm']); threshold = F(self.numerics['min_effective_thickness'])
        maxscale = F(self.numerics['max_geometry_scale']); mincos = F(self.numerics['min_cosine'])
        flag_bits = self.numerics['material_flags']
        for step in range(depth):
            mask = armor[:, step].copy(); indices = idx[:, step]; ns = node[:, step]
            if not mask.any(): continue
            m = mat_values[ns]; flags = mat_flags[ns]
            solid = (flags & flag_bits['solid']) != 0
            geometric = (flags & flag_bits['reactive_non_geometric']) == 0
            exit_ = self.data['exit_m'][indices]
            extra = self.data['extra_entry_m'][indices]
            invalid = mask & ((extra < target) | (solid & ~np.isfinite(exit_)))
            bad |= invalid
            mask &= ~invalid
            cosine = self.data['incidence_cos'][indices]
            base = np.minimum(m[:, 0] * m[:, 1], m[:, 4]); normal = base.copy(); los = base.copy()
            # Avoid inf*0 from absent exits without reinterpreting the sentinel.
            chord = (np.where(np.isfinite(exit_), exit_, entry[:, step]) - entry[:, step]) * F(self.numerics['millimetres_per_metre'])
            solid_los = np.maximum(m[:, 3] * m[:, 0], m[:, 1] * chord)
            los = np.where(solid, solid_los, los)
            normal = np.where(solid, np.minimum(np.maximum(cosine * los, m[:, 2]), m[:, 4]), normal)
            scaled = np.minimum(np.minimum(F(1) / np.maximum(cosine, mincos), maxscale) * base, m[:, 5])
            los = np.where(~solid & geometric, scaled, los)
            thin = (ammo['mode'] == 'KE') & (caliber >= F(self.numerics['relative_thin_caliber_ratio']) * normal)
            los = np.where(thin, normal, los)
            if ammo['mode'] == 'KE' and ammo['slope_rows']:
                slope_mask = (~thin) & geometric & (caliber > m[:, 7]) & (normal > 0)
                ratio = np.divide(caliber, normal, out=np.zeros_like(normal), where=normal > 0)
                inv = np.clip(_table(ammo['slope_rows'], ratio, cosine), mincos, F(1))
                slope_los = np.minimum(normal * np.minimum(F(1) / inv, maxscale), m[:, 5])
                los = np.where(slope_mask, slope_los, los)
            layer_charge = np.where(normal > threshold, np.where(solid, los, np.maximum(los, m[:, 6])), F(0))
            layer_charge = np.where(mask, layer_charge, F(0)).astype(np.float32)
            charges[:, step] = layer_charge
            if ammo['mode'] == 'CE':
                gaps = np.where(mask & started & (entry[:, step] > covered), entry[:, step] - covered, F(0))
                cost += gaps * F(ammo['air_loss_mm_per_m'])
            last = np.where(solid, exit_, entry[:, step])
            covered = np.where(mask, np.where(started, np.maximum(covered, last), last), covered)
            started |= mask
            cost += layer_charge
            ric_mask = mask & ((flags & flag_bits['ricochet']) != 0) & (normal > threshold)
            if ammo['ricochet_rows']:
                ratio = np.divide(caliber, normal, out=np.zeros_like(normal), where=normal > 0)
                ric = np.clip(_table(ammo['ricochet_rows'], ratio, cosine), 0, 1)
                survive *= np.where(ric_mask, F(1) - ric, F(1))
            risk |= np.where(armor[:, step], family[ns], 0).astype(np.uint8)
        status[bad & has_target] = 2
        penetration = F(ammo['penetration_mm']); variation = penetration * F(ammo['dispersion'])
        if variation > 0:
            prob = np.clip((penetration + variation - cost) / (variation + variation), 0, 1)
        else:
            prob = (penetration >= cost).astype(np.float32)
        if ammo['mode'] == 'KE' and len(curve) > 1:
            def reaches(initial):
                shift = np.zeros(number, np.float32); survives = np.ones(number, bool)
                for step in range(depth):
                    include = armor[:, step] & (charges[:, step] > 0) & survives
                    if not include.any(): continue
                    multiplier = _curve(curve, entry[:, step] + shift)
                    remaining = initial * multiplier - charges[:, step]
                    survives &= ~include | (remaining > 0)
                    ratio = np.divide(remaining, initial, out=np.zeros_like(remaining), where=initial > 0)
                    inv = np.interp(ratio, curve[::-1, 1], curve[::-1, 0]).astype(np.float32)
                    next_shift = inv - entry[:, step]
                    shift = np.where(include & (next_shift > shift), next_shift, shift)
                return survives
            low = np.full(number, penetration - variation, np.float32)
            high = np.full(number, penetration + variation, np.float32)
            low_pass, high_pass = reaches(low), reaches(high)
            if variation > 0:
                left, right = low.copy(), high.copy()
                for _ in range(self.numerics['search_iterations']):
                    middle = (left + right) / F(2)
                    passed = reaches(middle)
                    right = np.where(passed, middle, right); left = np.where(passed, left, middle)
                prob = (high - (left + right) / F(2)) / (variation + variation)
                prob = np.where(low_pass, F(1), np.where(~high_pass, F(0), prob))
            else:
                prob = high_pass.astype(np.float32)
        computed = status == 3
        probability[computed] = (prob * survive)[computed]
        required[computed] = cost[computed]
        return probability, required, status, risk


def recalculate_layers(metadata, ammo_path, output, config, ray_ids=None):
    started = time.perf_counter()
    model = PreparedLayers(metadata, config)
    profiles = read_json(ammo_path)
    if isinstance(profiles, dict): profiles = [profiles]
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    reports = []
    for ammo in profiles:
        from .common import safe_id
        safe_id(ammo['name'])
        probability, charge, state, flags = model.calculate(ammo, ray_ids)
        key = content_hash({'layers': file_hash(metadata), 'arrays': model.meta['arrays_sha256'], 'ammo': ammo,
                            'numerics': config['numerics'], 'model': file_hash(__file__),
                            'ray_ids': None if ray_ids is None else list(map(int, ray_ids))})
        dest = output / (model.meta['vehicle'] + '.' + ammo['name'] + '.npz')
        np.savez_compressed(dest, penetration_probability=probability, model_required_charge_mm=charge,
                            status=state, material_response_flags=flags,
                            angles_deg=model.plan['angles_deg'], pixel_pitch_m=model.plan['pixel_pitch_m'],
                            **{name: model.plan[name] for name in ['world_width_m', 'top_y_m', 'bottom_y_m'] if name in model.plan},
                            **({'ray_ids': ray_ids} if ray_ids is not None else {}))
        reports.append({'target_id': model.meta['vehicle'], 'ammo_id': ammo['name'], 'file': dest.name,
                        'sha256': file_hash(dest), 'physical_distance_m': ammo.get('distance_m'),
                        'ammo_profile_sha256': content_hash(ammo), 'model_version': config['scope']['cpu_model_version'],
                        'cache_key': key, 'ray_count': int(state.size), 'status_counts': {str(v): int(np.sum(state == v)) for v in np.unique(state)},
                        'source_distance_is_metadata_not_automatic_ballistics': True})
    report = {'results': reports, 'elapsed_seconds': time.perf_counter() - started,
              'backend': 'numpy-cpu', 'native_game_parity_claimed': False}
    write_json(output / 'recalculation-report.json', report)
    return report
