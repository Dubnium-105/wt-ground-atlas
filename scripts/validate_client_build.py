"""Validate the completed client batch and its current static build identities."""
from pathlib import Path
import gzip
import json
import hashlib
from collections import Counter

ROOT=Path(__file__).resolve().parents[1]

def read(path):
    return json.loads(path.read_text(encoding='utf-8'))

def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()

def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n',encoding='utf-8')

def main():
    catalog=read(ROOT/'data/snapshots/catalog.json')
    targets={v['id'] for v in catalog['vehicles'] if v['kind']=='ground'}
    client=read(ROOT/'reports/client-compute.json')
    atlas_path=ROOT/'web/data/probability/index.json';atlas=read(atlas_path)
    summary=read(ROOT/'reports/probability-summaries.json')
    manifest=read(ROOT/'web/data/manifest.json')
    index=read(ROOT/'web/data/recommendations/index.json')
    hashes={key:index.get(key)==sha(ROOT/path) for key,path in {
        'catalog_sha256':'data/snapshots/catalog.json','scenario_sha256':'data/snapshots/changes.json',
        'config_sha256':'config/model.json','engine_sha256':'shared/engine.mjs',
        'probability_summaries_sha256':'data/snapshots/decision-probabilities.json','target_layers_sha256':'data/snapshots/target-layers.json'}.items()}
    layers=read(ROOT/'data/snapshots/target-layers.json')
    vehicle_map={v['id']:v for v in catalog['vehicles']}
    foreign_ammo=[]
    errors=[];outside=[];counts=Counter()
    for item in index['files']:
        path=ROOT/'web/data/recommendations'/item['url'];raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=item['sha256'] or gzip.decompress(path.with_suffix(path.suffix+'.gz').read_bytes())!=raw:
            errors.append(item['url'])
        payload=json.loads(raw)
        counts[item['version']]+=len(payload['candidates'])
        for candidate in [variant for c in payload['candidates'] for variant in [c,*c.get('alternatives_at_br',[])]]:
            for vehicle_id,ammo_id in candidate.get('ammo_choices',{}).items():
                if ammo_id and ammo_id not in {a['id'] for a in vehicle_map[vehicle_id].get('ammo',[])}:foreign_ammo.append([vehicle_id,ammo_id])
            score=candidate.get('score')
            if score is not None and not candidate['lower']<=score<=candidate['upper']:
                outside.append({'file':item['url'],'br':candidate.get('candidate_cap')})
    integrity={'input_hashes_match':hashes,'foreign_ammunition_choices':foreign_ammo,'file_hash_or_gzip_errors':errors,'scores_outside_reported_bounds':outside,
        'countries':len({item['nation'] for item in index['files']}),'version_shards':len(index['files']),
        'candidate_counts':dict(counts),'impact_nation_br_union_count':len(index['impact']),
        'applied_changes':index['applied_change_count'],'rejected_changes':index['rejected_change_count'],
        'probability_summary_count':summary['records'],'reference_only_count':summary['reference_records'],'build_key':index['build_key']}
    expected_views=sum(len(entry['shards']) for entry in atlas['entries'])
    checks={
        'all_ground_targets_have_sampled_layers':targets=={t['target_id'] for t in layers['targets']},
        'own_ammunition_used_for_every_candidate':not foreign_ammo,
        'sampled_layer_export_hash_matches':manifest['target_layer_summary']['sha256']==sha(ROOT/'data/snapshots/target-layers.json'),
        'all_catalog_ground_targets_present':targets=={entry['target_id'] for entry in atlas['entries']},
        'all_client_jobs_complete':client['status_counts']=={'complete':len(targets)} and not client['issues'],
        'unique_physical_entry_ids':len({entry['id'] for entry in atlas['entries']})==len(atlas['entries']),
        'summary_covers_every_view':summary['records']==expected_views and summary['entry_count']==len(atlas['entries']),
        'summary_hash_matches':summary['bundle_sha256']==sha(ROOT/'data/snapshots/decision-probabilities.json'),
        'summary_verified_current_shards':summary['all_shard_hashes_verified'] and summary['source_index_sha256']==sha(atlas_path),
        'public_index_hash_matches':manifest['probability']['sha256']==sha(atlas_path),
        'public_target_count_matches':manifest['probability_summary']['targets']==len(targets),
        'catalog_client_coverage_matches':manifest['client_summary']['status_counts']==client['status_counts'],
        'reference_only_preserved':summary['verified_records']==0 and all(e['reference_ammunition_only'] for e in atlas['entries']),
        'recommendation_inputs_and_files_match':all(hashes.values()) and not errors and not outside,
    }
    report={'passed':all(checks.values()),'checks':checks,'targets':len(targets),'ammunition_profiles':len(atlas['entries']),
        'views':expected_views,'source_version':atlas['source_version'],'model_version':summary['physical_model_version'],
        'reference_ammunition':sorted({e['ammo_id'] for e in atlas['entries']}),
        'pixel_pitch_m':sorted({e['pixel_pitch_m'] for e in atlas['entries']}),
        'source_shard_hash_verification_report':'reports/probability-summaries.json','build_key':index['build_key'],
        'quantization':read(ROOT/'web/data/probability/export-report.json')}
    write(ROOT/'reports/decision-integrity.json',integrity)
    write(ROOT/'reports/client-backend-validation.json',report)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if not report['passed']:
        raise SystemExit(1)

if __name__=='__main__':main()
