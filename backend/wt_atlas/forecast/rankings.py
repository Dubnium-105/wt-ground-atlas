"""Independent ranking views over searched lineups, before global truncation."""
from .data import number

VIEWS = ('global', 'br', 'nation', 'uptier', 'ground_only', 'combined_arms')


def order(row, view='global'):
    value = row.get('uptier_lower_utility') if view == 'uptier' else row.get('score')
    if not number(value):
        value = row.get('estimate', float('-inf'))
    return (-int(row.get('rankable', False)), -value,
            -row.get('evidence_coverage', 0), len(row.get('members', [])),
            tuple(sorted(row.get('keys', [row.get('lineup_key', '')]))))


def select(rows, view='global', *, limit=None, diversity=None, rankable_only=True):
    if view not in VIEWS:
        raise ValueError('Unknown ranking view')
    rows = [r for r in rows if not rankable_only or (r.get('rankable') and number(r.get('score')))]
    if view in ('ground_only', 'combined_arms'):
        rows = [r for r in rows if r.get('composition') == view]
    if view == 'uptier':
        rows = [r for r in rows if number(r.get('uptier_lower_utility'))]
    result, parents_seen, groups = [], [], set()
    for row in sorted(rows, key=lambda r: order(r, view)):
        parents = frozenset(m['vehicle_id'] for m in row['members'])
        # Select every BR/nation before parent de-duplication: one vehicle may
        # have legitimate configurations in different BR environments.
        group = row['actual_br'] if view == 'br' else row['nation'] if view == 'nation' else None
        if group is not None:
            if group in groups:
                continue
        elif any(parents == p or (diversity is not None and len(parents & p) / len(parents | p) >= diversity)
                 for p in parents_seen):
            continue
        groups.add(group)
        parents_seen.append(parents)
        result.append(row)
        if limit is not None and len(result) >= limit:
            break
    return result


def table_candidates(table, view=None):
    if view in table.get('category_candidates', {}):
        return table['category_candidates'][view]
    if view is not None:
        return table['candidates']
    unique = {}
    for row in table['candidates'] + [r for rows in table.get('category_candidates', {}).values() for r in rows]:
        unique[row['lineup_key']] = row
    return list(unique.values())


def build_views(tables, config):
    views = {view: {} for view in VIEWS}
    for slots in config['search']['slots']:
        for acquisition in config['search']['acquisition_profiles']:
            selected = [t for t in tables if t['slots'] == slots and t['acquisition'] == acquisition and not t['budget_exhausted']]
            for view in VIEWS:
                rows = [r for t in selected for r in table_candidates(t, view) if len(r["members"]) == slots]
                limit = None if view in ('br', 'nation') else config['search']['global_limit']
                views[view][f'{slots}/{acquisition}'] = [
                    {**r, 'rank': i + 1} for i, r in enumerate(select(rows, view, limit=limit))]
    return views
