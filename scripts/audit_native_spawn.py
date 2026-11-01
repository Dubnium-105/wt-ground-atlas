#!/usr/bin/env python3
"""Verify reviewed client SP evidence and optionally replay supplied components."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from wt_atlas.common import read_json, write_json, file_hash
from wt_atlas.forecast.native_spawn_final import audit_client, compose_final_stage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client-exe', required=True, type=Path)
    parser.add_argument('--game-version', required=True)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--replay', type=Path, help='JSON list of explicitly supplied native arithmetic inputs')
    args = parser.parse_args()
    if args.out.resolve() in {p.resolve() for p in (args.client_exe, args.replay) if p is not None}:
        raise ValueError('Keep evidence sources and output separate')
    report = audit_client(args.client_exe, args.game_version)
    if args.replay:
        rows = read_json(args.replay)
        if not isinstance(rows, list):
            raise ValueError('Replay input must be a list')
        replay = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != {'base_cost', 'weapon_multiplier', 'type_multiplier'}:
                raise ValueError('Each replay requires exactly the three native arithmetic inputs')
            replay.append({'inputs': row, 'result': compose_final_stage(**row),
                'status': 'supplied_inputs_not_verified_battle_cost'})
        report['replay'] = replay
        report['replay_source_sha256'] = file_hash(args.replay)
    write_json(args.out, report)
    print(json.dumps({key: report[key] for key in ('game_version', 'source_sha256',
        'complete_base_formula_verified', 'generated_verified_final_costs', 'publishable')}, indent=2))


if __name__ == '__main__':
    main()
