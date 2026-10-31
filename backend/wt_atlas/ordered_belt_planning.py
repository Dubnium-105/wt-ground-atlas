"""Plan every missing component of exact mixed selections against a verified index."""
from collections import defaultdict
from pathlib import Path
import json
import sqlite3
from .common import read_json, file_hash
from .forecast.data import Atlas, br_step, number
from .forecast.belts import projectile_groups


def include_components(general, ordered):
    """Keep general requests and add every missing mixed-selection component."""
    for key in ('catalog_sha256','ammunition_sha256','config_sha256'):
        if general[key] != ordered[key]: raise ValueError('Planner input changed between passes')
    combined = defaultdict(lambda: {'attackers':set(), 'yaws':set()})
    for report in (general, ordered):
        for row in report['candidate_rebuild_requests']:
            target = combined[(row['target_vehicle_id'],row['profile_id'])]
            target['attackers'].update(row['attacker_ids']); target['yaws'].update(row['required_yaws_deg'])
    return {**general, 'candidate_request_count':len(combined), 'ordered_belt_selections':ordered['selections'],
        'candidate_rebuild_requests':[{'target_vehicle_id':t,'profile_id':p,'attacker_ids':sorted(r['attackers']),
            'required_yaws_deg':sorted(r['yaws'])} for (t,p),r in sorted(combined.items())]}


def plan(catalog_path, ammunition_path, index_path, config_path):
    catalog = read_json(catalog_path); config = read_json(config_path)
    class EmptyIndex:
        metadata = {}; lookup = {}
    atlas = Atlas(Path(index_path).parent, ammunition_path, catalog, index=EmptyIndex())
    db = sqlite3.connect(Path(index_path).resolve().as_uri()+'?mode=ro', uri=True)
    try:
        if db.execute("SELECT count(*) FROM maps WHERE selected=1 AND status!='verified'").fetchone()[0]:
            raise ValueError('Finish inventory array verification before planning')
        available = {(t,p,d):set(json.loads(y or '[]')) for t,p,d,y in db.execute(
            "SELECT target,ammo,distance,angles FROM maps WHERE selected=1 AND status='verified'")}
    finally: db.close()
    grounds = [v for v in catalog['vehicles'] if v.get('kind')=='ground' and
        number(v.get('br',{}).get('current')) and v.get('system_role')!='component']
    requests = defaultdict(lambda: {'attackers':set(), 'yaws':set()}); rows = []
    for attacker in grounds:
        bindings = atlas.bindings(attacker)
        for ammo in attacker.get('ammo', []):
            if not ammo.get('ordered_belt'): continue
            groups = projectile_groups(ammo, [p for a,p in bindings if a['id']==ammo['id']])
            required = complete = 0; missing_ranges = set()
            for target in grounds:
                if (attacker['nation']==target['nation'] or abs(br_step(attacker['br']['current'])-
                        br_step(target['br']['current'])) > config['matchmaking']['spread_steps']): continue
                for scenario in config['scenarios']:
                    distance,yaw = scenario['physical_distance_m'],scenario['yaw_deg']
                    required += 1; all_present = True
                    for index,group in enumerate(groups):
                        matches = [p for p in group if p['distance_m']==distance]
                        if len(matches)!=1:
                            missing_ranges.add((index,distance)); all_present=False; continue
                        profile = matches[0]
                        if yaw not in available.get((target['id'],profile['name'],distance),set()):
                            all_present=False
                            row=requests[(target['id'],profile['name'])]
                            row['attackers'].add(attacker['id']);row['yaws'].add(yaw)
                    complete += int(all_present)
            rows.append({'vehicle_id':attacker['id'],'ammo_id':ammo['id'],
                'required_directed_scenarios':required,'complete_directed_scenarios':complete,
                'missing_component_ranges':[{'projectile_index':i,'distance_m':d} for i,d in sorted(missing_ranges)]})
    return {'schema_version':'1.0.0','catalog_sha256':file_hash(catalog_path),
        'ammunition_sha256':file_hash(ammunition_path),'config_sha256':file_hash(config_path),
        'inventory':str(Path(index_path).resolve()),'existing_verified_maps':len(available),
        'candidate_request_count':len(requests),'selections':rows,
        'candidate_rebuild_requests':[{'target_vehicle_id':t,'profile_id':p,
            'attacker_ids':sorted(r['attackers']),'required_yaws_deg':sorted(r['yaws'])} for (t,p),r in sorted(requests.items())],
        'scope':'All components of source-bound mixed selections; plan is not computed coverage or a ranking.'}
