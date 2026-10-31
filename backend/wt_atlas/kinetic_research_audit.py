"""Validate independently recorded CPU/CUDA research parity receipts."""
import re
from .forecast.data import number
from .kinetic_missile_points import DISTANCES


def summarize_checks(report):
    if (report.get('status') != 'passed' or report.get('publishable') is not False or
        report.get('game_probability_validated') is not False or report.get('source_probability_maps_read') != 0):
        raise ValueError('Research parity report is not a completed conditional calculation')
    rows = report.get('checks')
    if not isinstance(rows, list) or len(rows) != len(DISTANCES) or {r.get('distance_m') for r in rows} != set(DISTANCES):
        raise ValueError('Four exact research distances required')
    sampled = partial = 0; maximum = 0.
    for row in rows:
        count, computed, uncertain = (row.get(k) for k in ('sampled_rays', 'computed_rays', 'partial_probability_rays'))
        if (any(not isinstance(x, int) or isinstance(x, bool) for x in (count, computed, uncertain)) or
            count < 1 or not 0 <= uncertain <= computed <= count):
            raise ValueError('Invalid parity ray counts')
        delta = row.get('max_probability_delta')
        if not number(delta) or not 0 <= delta <= 2 / 65536:
            raise ValueError('CPU/CUDA parity tolerance exceeded')
        result = row.get('research_map', {})
        if (result.get('reference_only') is not True or
            any(not isinstance(result.get(k), str) or not re.fullmatch('[0-9a-f]{64}', result[k])
                for k in ('map_id', 'array_sha256'))):
            raise ValueError('Reference-only map identity missing')
        sampled += count; partial += uncertain; maximum = max(maximum, delta)
    return {'sampled_rays': sampled, 'partial_probability_rays': partial, 'max_probability_delta': maximum,
            'checks_with_partial_probability': sum(row['partial_probability_rays'] > 0 for row in rows)}
