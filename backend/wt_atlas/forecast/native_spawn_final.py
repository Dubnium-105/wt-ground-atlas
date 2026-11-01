"""Read-only evidence and replay of the reviewed client's final SP arithmetic.

This is the last arithmetic stage, not the battle's base-cost calculation.
Inputs must already be the native integer base and returned script multipliers;
the Python component port is not silently treated as a native float32 result.
"""
import hashlib
import math
from pathlib import Path

from ..common import file_hash


REVIEWED_CLIENT = {
    'game_version': '2.59.0.92',
    'sha256': '17b75d78a20d657b96c9027ac06af211203b94327fbf747813e5367d12e60807',
    'size_bytes': 131560160,
    'ranges': [
        ('registration', '0x17f7a80', 25128576, 8864, '450e269182523d1d393efa011cf45de13728fce72d1b9d1c21f4d14e5ea2f00f'),
        ('wrapper', '0x17f9d20', 25137440, 1127, '2f32edac6123d590970b7a7bd81abc7b09a1f573225164bb4459c92e01935512'),
        ('adapter', '0x15c5050', 22824016, 1443, '3d2800c0f09cac78951ae9cd4094183709c76fd0cfaa7149cfd1bc7c9bb197bf'),
        ('cost_core', '0x2e26f0', 3021552, 4676, '3be0928b9424359d14e3051ef5816beb6071532633fb39c3e3359f8413efeaac'),
        ('sign_mask', '0x6b9d150', 112837456, 16, '8a5f16f43d7be9d5da1bb71656fa69fcc035482b234cfd571954d046f0ca954a'),
        ('round_bias', '0x714ecc0', 118808256, 16, '6669aab039c7913afbf2e871bdf79ccc932eb3860afc7b2d0e6e216da1c95fe5'),
    ],
}
# The binary contains the predecessor of 0.5, not an exact 0.5 constant.
# Preserve it so values immediately below a half boundary do not round up.
ROUND_BIAS = float.fromhex('0x1.fffffffffffffp-2')
INT32_MAX = (1 << 31) - 1


def compose_final_stage(base_cost, weapon_multiplier, type_multiplier):
    """Replay MULSD, MULSD, signed bias, truncation for nonnegative SP.

    Does not resolve a base cost, persistent override, script callback, selected
    ammunition, respawn count, mission, or BR. No rounding occurs between the
    two multiplications. Replaying supplied inputs does not verify those inputs.
    """
    if type(base_cost) is not int or not 0 <= base_cost <= INT32_MAX:
        raise ValueError('Native base cost must be a nonnegative int32')
    multipliers = []
    for value in (weapon_multiplier, type_multiplier):
        if type(value) not in (int, float):
            raise ValueError('Native returned multipliers must be finite and nonnegative')
        try:
            value = float(value)
        except OverflowError as error:
            raise ValueError('Native returned multiplier exceeds float64') from error
        if not math.isfinite(value) or value < 0:
            raise ValueError('Native returned multipliers must be finite and nonnegative')
        multipliers.append(value)
    value = float(base_cost) * multipliers[0]
    value *= multipliers[1]
    if not math.isfinite(value):
        raise ValueError('Final SP arithmetic overflow')
    result = math.trunc(value + math.copysign(ROUND_BIAS, value))
    if not 0 <= result <= INT32_MAX:
        raise ValueError('Final SP is outside the nonnegative int32 domain')
    return result


def audit_client(executable, game_version):
    """Verify the exact reviewed file; never load, launch or modify the client."""
    path = Path(executable)
    if game_version != REVIEWED_CLIENT['game_version']:
        raise ValueError('Unreviewed client version')
    if path.stat().st_size != REVIEWED_CLIENT['size_bytes'] or file_hash(path) != REVIEWED_CLIENT['sha256']:
        raise ValueError('Unreviewed client executable hash or size')
    verified = []
    with path.open('rb') as stream:
        for name, rva, offset, size, expected in REVIEWED_CLIENT['ranges']:
            stream.seek(offset)
            data = stream.read(size)
            if len(data) != size or hashlib.sha256(data).hexdigest() != expected:
                raise ValueError('Reviewed client range changed: ' + name)
            verified.append({'name': name, 'rva': rva, 'offset': offset, 'size': size, 'sha256': expected})
    return {
        'schema_version': '1.0.0', 'game_version': game_version,
        'source_sha256': REVIEWED_CLIENT['sha256'], 'source_size_bytes': REVIEWED_CLIENT['size_bytes'],
        'method': 'read_only_static_review_and_exact_file_hash', 'verified_ranges': verified,
        'endpoint': 'native_final_arithmetic_with_supplied_runtime_inputs',
        'scope': 'normal_composition_path_excludes_early_return_and_persistent_override',
        'arithmetic': {'order': ['integer_base_to_float64', 'multiply_returned_weapon_multiplier',
            'multiply_returned_type_multiplier', 'add_sign_adjusted_bias', 'truncate_to_int32'],
            'round_bias_hex': ROUND_BIAS.hex(), 'intermediate_rounding': False},
        'call_chain': ['shop_get_spawn_score', 'rva:0x17f9d20', 'rva:0x15c5050', 'rva:0x2e26f0'],
        'runtime_dependencies': ['battle_base_cost_table_or_get_unit_spawn_score_callback',
            'persistent_weapon_costs_override', 'selected_weapon_and_bullet_script_results',
            'get_spawn_score_type_mul_with_actual_mission_and_respawn_history'],
        'complete_base_formula_verified': False, 'live_native_outputs_observed': 0,
        'generated_verified_final_costs': 0, 'promoted_spawn_rules': False, 'publishable': False,
        'client_executed': False, 'source_modified': False,
    }
