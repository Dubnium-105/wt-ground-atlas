"""Measured model/search sensitivity, separate from empirical calibration."""
from __future__ import annotations
import copy
import time
from .data import digest, file_hash
from .engine import Forecast
from .search import Optimizer

CASES = ('aim_narrower','aim_wider','opponent_skill_higher','double_search_budget','alternate_queue_seed')


def compare(baseline, changed, minimum_overlap=.5, maximum_score_delta=10.):
    def boards(world):
        return {name:{tuple(sorted(row['keys'])):row['score'] for row in rows}
                for name,rows in world['boards'].items() if rows}
    before,after=boards(baseline),boards(changed);records=[]
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
    # These are declared robustness thresholds, never observational error bars.
    for name in CASES:
        config=copy.deepcopy(engine.config)
        if name in ('aim_narrower','aim_wider'):
            factor=.8 if name=='aim_narrower' else 1.2
            for key in ('sigma_x_m','sigma_y_m'):config['aim'][key]*=factor
        elif name=='opponent_skill_higher':
            config['opponent_population']=copy.deepcopy(config.get('opponent_population',config['population']))
            config['opponent_population']['mean']+=.5
        elif name=='double_search_budget':config['search']['max_evaluations']*=2
        else:
            config['search']['seed']=config['search'].get('seed',0)+1
            if config['matchmaking'].get('lineup_sampler'):
                config['matchmaking']['lineup_sampler']['seed']+=1
        e=Forecast(engine.catalog,engine.atlas,config,engine.cache,engine.effects_path,getattr(engine,'missions_path',None))
        e.build_key=digest([inputs_sha256,name,config]);e.resume=getattr(engine,'resume',False)
        world=Optimizer(e).world()
        records.append({'case':name,'config_sha256':digest(config),'execution':world['execution'],
                        'evaluations':world['evaluations'],**compare(baseline,world)})
    report={'schema_version':'1.0.0','kind':'measured_model_search_sensitivity',
            'catalog_sha256':digest(engine.catalog),'config_sha256':digest(engine.config),
            'inputs_sha256':inputs_sha256,'thresholds':{'minimum_overlap':.5,'maximum_score_delta':10.},
            'cases':records,'passed':all(r['passed'] for r in records),
            'duration_seconds':time.perf_counter()-started,'empirical_validation':False}
    report['sha256']=digest(report)
    return report


def valid(report, catalog, config, inputs_sha256=None):
    if not isinstance(report,dict):return False
    try:
        return (report.get('kind')=='measured_model_search_sensitivity' and report.get('passed') is True and
                report.get('catalog_sha256')==digest(catalog) and report.get('config_sha256')==digest(config) and
                (inputs_sha256 is None or report.get('inputs_sha256')==inputs_sha256) and
                report.get('sha256')==digest({k:v for k,v in report.items() if k!='sha256'}) and
                {r['case'] for r in report['cases']}==set(CASES) and len(report['cases'])==len(CASES) and
                all(r['passed'] is True and r['boards'] and not r['baseline_truncated'] and not r['changed_truncated']
                    and all(b['overlap']>=.5 and b['maximum_score_delta'] is not None and 0<=b['maximum_score_delta']<=10 for b in r['boards'])
                    for r in report['cases']))
    except (KeyError,TypeError,ValueError):return False
