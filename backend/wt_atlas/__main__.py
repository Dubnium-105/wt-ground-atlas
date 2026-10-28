"""Use ``PYTHONPATH=backend python -m wt_atlas --help`` from project root."""
from __future__ import annotations
import argparse
import json
import sys
from .common import load_config, resolved, read_json


def main(argv=None):
    parser = argparse.ArgumentParser(description='Offline acquisition, GRP computation and lightweight atlas export')
    parser.add_argument('--config', default='config/pipeline.json')
    sub = parser.add_subparsers(dest='command', required=True)
    client = sub.add_parser('client', help='Build ground catalog profiles and probability data from an installed client')
    client.add_argument('stage', choices=['prepare', 'sources', 'link', 'compute', 'publish', 'status', 'all'])
    client.add_argument('--source', help='Installed War Thunder directory')
    client.add_argument('--workers', type=int, default=None)
    client.add_argument('--vehicle', nargs='+')
    imported = sub.add_parser('import-atlas', help='Validate and export existing probability NPZ inputs')
    imported.add_argument('--input', required=True); imported.add_argument('--output')
    acquisition = sub.add_parser('acquire', help='Copy local client GRPs or fetch a hash-pinned source manifest')
    acquisition.add_argument('--source', help='Trusted local client directory; overrides source in config')
    decode = sub.add_parser('decode', help='Decode every acquired supported GRP into a private mesh cache')
    decode.add_argument('--vehicle', nargs='+')
    run = sub.add_parser('run', help='Resume acquisition/geometry/material/probability pipeline')
    run.add_argument('--source'); run.add_argument('--vehicle', nargs='+'); run.add_argument('--export', action='store_true')
    sub.add_parser('status', help='Read durable job status')
    exported = sub.add_parser('export', help='Publish lightweight shards from completed jobs')
    exported.add_argument('--output')
    recalc = sub.add_parser('recalculate', help='Recalculate existing layer cache on CPU with an exact ammunition profile')
    recalc.add_argument('--layers', required=True); recalc.add_argument('--ammo', required=True); recalc.add_argument('--output', required=True)
    binding = sub.add_parser('bind-audit', help='Audit exact GRP collision node names against Datamine DamageParts inheritance')
    binding.add_argument('--grp', required=True); binding.add_argument('--unit-config', required=True); binding.add_argument('--output', required=True)
    summary = sub.add_parser('summarize', help='Integrate exported probability shards into a small reference-only decision bundle')
    summary.add_argument('--index'); summary.add_argument('--model-config'); summary.add_argument('--context')
    summary.add_argument('--output'); summary.add_argument('--report'); summary.add_argument('--force', action='store_true')
    src = sub.add_parser('sources', help='Fetch versioned official catalog; optionally refresh and collect Tencent changes')
    src.add_argument('--source-config', default='config/sources.json'); src.add_argument('--catalog-only', action='store_true')
    src.add_argument('--refresh', action='store_true', help='Refresh source caches instead of reusing captured responses')
    src.add_argument('--changes', action='store_true', help='Also fetch and audit the configured Tencent adjustment sheet')
    args = parser.parse_args(argv)
    config, root = load_config(args.config)
    if args.command == 'client':
        from .client import prepare_client, compute_client, publish_client, client_status
        from .client_sources import index_client_sources, link_client_catalog
        workers = args.workers or config['client']['workers']
        if workers < 1: raise ValueError('workers must be positive')
        source = args.source or config['client']['source']
        if args.stage == 'prepare': report = prepare_client(root, config, source, workers, args.vehicle)
        elif args.stage == 'sources': report = index_client_sources(root, config)
        elif args.stage == 'link': report = link_client_catalog(root, config)
        elif args.stage == 'compute': report = compute_client(root, config, workers, args.vehicle)
        elif args.stage == 'publish': report = publish_client(root, config)
        elif args.stage == 'status': report = client_status(root, config)
        else:
            prepare_client(root, config, source, workers, args.vehicle)
            index_client_sources(root, config)
            compute_client(root, config, workers, args.vehicle)
            report = publish_client(root, config)
            report['catalog'] = link_client_catalog(root, config)
    elif args.command == 'import-atlas':
        from .probability import export_atlas
        report = export_atlas(args.input, resolved(root, args.output or config['paths']['static_probability']), config)
    elif args.command in ['acquire', 'decode', 'run', 'status', 'export']:
        from .jobs import acquire, run_jobs, JobStore, export_completed
        if args.command == 'acquire': report = acquire(root, config, args.source)
        elif args.command == 'decode': report = run_jobs(root, config, decode_only=True, vehicle_ids=args.vehicle)
        elif args.command == 'status': report = JobStore(root, config).overview()
        elif args.command == 'export': report = export_completed(root, config, args.output)
        else:
            acquire(root, config, args.source)
            report = run_jobs(root, config, vehicle_ids=args.vehicle)
            if args.export: report['export'] = export_completed(root, config)
    elif args.command == 'recalculate':
        from .cpu_model import recalculate_layers
        report = recalculate_layers(args.layers, args.ammo, args.output, config)
    elif args.command == 'bind-audit':
        from .bindings import damageparts_audit
        result = damageparts_audit(args.grp, args.unit_config, resolved(root, config['paths']['vendor']), args.output,
                                  {'source_version': config['scope']['source_version'], 'datamine_commit': config['scope']['datamine_commit']})
        report = {'resources': [{k: row[k] for k in ['collision_resource', 'counts']} for row in result['resources']], 'physics_profile_ready': result['physics_profile_ready']}
    elif args.command == 'summarize':
        import subprocess
        settings = config['summary']
        command = [settings['node_binary'], str(resolved(root, settings['script'])),
                   '--index', str(resolved(root, args.index or settings['input_index'])),
                   '--config', str(resolved(root, args.model_config or settings['model_config'])),
                   '--out', str(resolved(root, args.output or settings['output'])),
                   '--report', str(resolved(root, args.report or settings['report']))]
        if settings.get('workers'): command += ['--workers', str(settings['workers'])]
        if args.context: command += ['--context', str(resolved(root, args.context))]
        if args.force: command.append('--force')
        result = subprocess.run(command, cwd=root, text=True, capture_output=True, check=False)
        if result.returncode:
            raise RuntimeError('Probability summary builder failed: ' + (result.stderr or result.stdout).strip())
        report = json.loads(result.stdout)
    elif args.command == 'sources':
        from .sources import fetch_snapshot
        source_config = resolved(root, args.source_config)
        catalog = fetch_snapshot(source_config, root=root, details=not args.catalog_only, refresh=args.refresh)
        report = {'snapshot': catalog.get('snapshot'), 'vehicle_count': len(catalog.get('vehicles', []))}
        if args.changes:
            from .tencent import fetch_changes
            changes = fetch_changes(source_config, refresh=args.refresh)
            report['changes'] = changes.get('coverage', {})
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    counts = report.get('status_counts', {}) if isinstance(report, dict) else {}
    return 1 if any(count for status, count in counts.items() if status.startswith('failed')) else 0


if __name__ == '__main__':
    raise SystemExit(main())
