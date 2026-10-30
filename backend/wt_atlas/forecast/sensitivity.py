"""Measured model/search sensitivity, separate from empirical calibration."""
from __future__ import annotations
import copy
import time
from .data import digest, validate_sensitivity_policy
from .engine import Forecast
from .search import Optimizer

CASES = ('aim_narrower','aim_wider','opponent_skill_higher','increased_search_budget','alternate_queue_seed')


def comparison_boards(world):
    groups = {f'{view}/{name}': rows for view, boards in world.get('views', {}).items()
              for name, rows in boards.items()} or world['boards']
    return {name: {tuple(sorted(row['keys'])): row['score'] for row in rows}
            for name, rows in groups.items() if rows}


def compare(baseline, changed, *, minimum_overlap, maximum_score_delta):
    before,after=comparison_boards(baseline),comparison_boards(changed);records=[]
    for name,rows in before.items():
        candidates=after.get(name,{})
        common=rows.keys() & candidates.keys()
        overlap=len(common)/len(rows)
        delta=max((abs(rows[k]-candidates[k]) for k in common),default=None)
        records.append({'board':name,'baseline_candidates':len(rows),'changed_candidates':len(candidates),
                        'overlap':overlap,'maximum_score_delta':delta})
    passed=bool(records) and not baseline['truncated_searches'] and not changed['truncated_searches'] and all(
        r['overlap']>=minimum_overlap and r['maximum_score_delta'] is not None and
        r['maximum_score_delta']<=maximum_score_delta for r in records)
    return {'passed':passed,'baseline_truncated':baseline['truncated_searches'],
            'changed_truncated':changed['truncated_searches'],'boards':records}


def run(engine, baseline, *, inputs_sha256):
    started=time.perf_counter();records=[]
    policy=validate_sensitivity_policy(engine.config.get("sensitivity"))
    # These are declared robustness thresholds, never observational error bars.
    for name in CASES:
        config=copy.deepcopy(engine.config)
        if name in ('aim_narrower','aim_wider'):
            factor=policy['aim_narrower_factor' if name=='aim_narrower' else 'aim_wider_factor']
            for key in ('sigma_x_m','sigma_y_m'):config['aim'][key]*=factor
        elif name=='opponent_skill_higher':
            config['opponent_population']=copy.deepcopy(config.get('opponent_population',config['population']))
            config['opponent_population']['mean']+=policy['opponent_skill_mean_shift']
        elif name=='increased_search_budget':config['search']['max_evaluations']*=policy['search_budget_multiplier']
        else:
            config['search']['seed']=config['search'].get('seed',0)+policy['seed_offset']
            if config['matchmaking'].get('lineup_sampler'):
                config['matchmaking']['lineup_sampler']['seed']+=policy['seed_offset']
        e=Forecast(engine.catalog,engine.atlas,config,engine.cache,engine.effects_path,getattr(engine,'missions_path',None))
        e.build_key=digest([inputs_sha256,name,config]);e.resume=getattr(engine,'resume',False)
        world=Optimizer(e).world()
        records.append({'case':name,'config_sha256':digest(config),'execution':world['execution'],
                        'evaluations':world['evaluations'],**compare(baseline,world,**policy["thresholds"])})
    report={'schema_version':'1.0.0','kind':'measured_model_search_sensitivity',
            'catalog_sha256':digest(engine.catalog),'config_sha256':digest(engine.config),
            'inputs_sha256':inputs_sha256,'policy':copy.deepcopy(policy),'thresholds':policy['thresholds'],
            'cases':records,'passed':all(r['passed'] for r in records),
            'duration_seconds':time.perf_counter()-started,'empirical_validation':False}
    report['sha256']=digest(report)
    return report


def valid(report, catalog, config, inputs_sha256=None, *, world=None):
    if not isinstance(report,dict):return False
    try:
        policy=validate_sensitivity_policy(config.get("sensitivity"))
        return (report.get("policy")==policy and report.get("thresholds")==policy["thresholds"] and report.get('kind')=='measured_model_search_sensitivity' and report.get('passed') is True and
                report.get('catalog_sha256')==digest(catalog) and report.get('config_sha256')==digest(config) and
                (inputs_sha256 is None or report.get('inputs_sha256')==inputs_sha256) and
                report.get('sha256')==digest({k:v for k,v in report.items() if k!='sha256'}) and
                {r['case'] for r in report['cases']}==set(CASES) and len(report['cases'])==len(CASES) and
                all((not world or not world.get('views') or set(comparison_boards(world)) <= {b.get('board') for b in r['boards']})
                    and r['passed'] is True and r['boards'] and not r['baseline_truncated'] and not r['changed_truncated']
                    and all(b['overlap']>=policy['thresholds']['minimum_overlap'] and b['maximum_score_delta'] is not None and 0<=b['maximum_score_delta']<=policy['thresholds']['maximum_score_delta'] for b in r['boards'])
                    for r in report['cases']))
    except (KeyError,TypeError,ValueError):return False
