"""Compare configured random units with the exact source-profile gap inventory."""
from collections import Counter


def compare(evidence, catalog, gaps):
    if evidence['source_version'] != gaps['source_version']:
        raise ValueError('Reinforcement and profile-gap source versions differ')
    ids = [v['id'] for v in catalog['vehicles']]
    if len(ids) != len(set(ids)): raise ValueError('Duplicate catalog ID')
    idset = set(ids); folded = {}
    for identifier in ids: folded.setdefault(identifier.casefold(), []).append(identifier)
    records = gaps['vehicles']; missing = {r['vehicle_id']: r for r in records}
    if len(records) != len(missing): raise ValueError('Duplicate profile-gap ID')
    if not set(missing).issubset(idset): raise ValueError('Profile gaps include IDs outside the catalog')
    rows = []; seen = set()
    for country, groups in sorted(evidence['configured_candidates'].items()):
        for group, data in sorted(groups.items()):
            for identifier in data['candidates']:
                if identifier in seen: raise ValueError('Repeated configured candidate ID')
                seen.add(identifier)
                suggestions = sorted(folded.get(identifier.casefold(), [])) if identifier not in idset else []
                match = 'exact' if identifier in idset else ('case_only' if len(suggestions) == 1 else 'unmatched')
                gap = missing.get(identifier) if match == 'exact' else None
                rows.append({'country': country, 'group': group, 'configured_id': identifier,
                    'catalog_match': match, 'case_only_suggestions': suggestions,
                    'in_exact_profile_gap_inventory': bool(gap),
                    'gap_record_status': gap['record_status'] if gap else None})
    counts = Counter(row['catalog_match'] for row in rows)
    group_gaps = Counter(row['group'] for row in rows if row['in_exact_profile_gap_inventory'])
    return {'source_version': evidence['source_version'], 'candidate_count': len(rows),
            'catalog_match_counts': dict(sorted(counts.items())),
            'exact_profile_gap_count': sum(group_gaps.values()),
            'exact_profile_gaps_by_group': dict(sorted(group_gaps.items())), 'candidates': rows,
            'scope': 'Configured client entries only; case-only names are suggestions, not verified identities. Profile gaps do not establish ground-target eligibility or runtime availability.',
            'publishable': False, 'formal_rankings': 0}
