"""Opt-in, consented statistics calibration with player AND match disjoint splits.

Predicted probabilities must be for the same declared binary endpoint as outcomes.
K/D, ratings and community votes are never converted to physical penetration.
"""
import math
from datetime import datetime,timezone
from collections import defaultdict
import numpy as np
from scipy.optimize import minimize
from scipy.special import expit,logit
from .data import digest,number


def connected_groups(rows):
    parent={}
    def find(k):
        parent.setdefault(k,k)
        if parent[k]!=k:parent[k]=find(parent[k])
        return parent[k]
    for r in rows:
        a,b=find(('player',r['player_id'])),find(('match',r['match_id']))
        parent[a]=b
    groups=defaultdict(list)
    for r in rows:groups[find(('match',r['match_id']))].append(r)
    return sorted(groups.values(),key=lambda rs:(max(r['timestamp'] for r in rs),digest(rs)))


def grouped_split(rows,train_fraction=.6,validation_fraction=.2):
    groups=connected_groups(rows)
    if len(groups)<3:raise ValueError('Need at least three independent player/match components')
    ordered=sorted(r['timestamp'] for r in rows);n=len(ordered)
    first=ordered[max(1,int(train_fraction*n))];second=ordered[min(n-1,int((train_fraction+validation_fraction)*n))]
    result={'train':[],'validation':[],'test':[]}
    for group in groups:
        low=min(r['timestamp'] for r in group);high=max(r['timestamp'] for r in group)
        key='train' if high<first else 'validation' if low>=first and high<second else 'test' if low>=second else None
        if key:result[key].extend(group)
    if any(not rows for rows in result.values()):raise ValueError('No disjoint chronological train/validation/test split')
    return result


def metrics(rows,predictions,bins=10):
    y=np.array([r['outcome'] for r in rows]);p=np.clip(np.asarray(predictions,dtype=float),1e-12,1-1e-12)
    curve=[]
    for i in range(bins):
        mask=(p>=i/bins)&(p<((i+1)/bins) if i<bins-1 else p<=1)
        curve.append({'bin':[i/bins,(i+1)/bins],'count':int(mask.sum()),'predicted':float(p[mask].mean()) if mask.any() else None,'observed':float(y[mask].mean()) if mask.any() else None})
    return {'nll':float(-(y*np.log(p)+(1-y)*np.log(1-p)).mean()),'brier':float(np.mean((p-y)**2)),'calibration_curve':curve}


def calibrate(packet,config):
    import copy
    packet=copy.deepcopy(packet)
    for field in ('source','sampling_window','game_version','mode','sample_count','deduplication','bias_notes','authorization','endpoint'):
        if not packet.get(field):raise ValueError('Missing statistics provenance: '+field)
    if packet['mode']!='ground_rb' or packet['endpoint'] not in ('penetration','damage','mission'):raise ValueError('Wrong statistics mode/endpoint')
    rows=packet['observations']
    if packet['sample_count']!=len(rows) or len(rows)<config['minimum_samples']:raise ValueError('Insufficient/inconsistent sample size')
    identities=set()
    for r in rows:
        for field in ('observation_id','player_id','match_id','timestamp','stratum'):
            if not r.get(field):raise ValueError('Missing grouped observation field')
        if r['observation_id'] in identities:raise ValueError('Duplicate observation')
        identities.add(r['observation_id'])
        timestamp=datetime.fromisoformat(r['timestamp'].replace('Z','+00:00'))
        if timestamp.tzinfo is None:raise ValueError('Observation timestamp needs timezone')
        r['timestamp']=timestamp.astimezone(timezone.utc).isoformat()
        if r.get('game_version')!=packet['game_version'] or not number(r.get('prediction')) or not 0<=r['prediction']<=1 or not number(r.get('outcome')) or r.get('outcome') not in (0,1):raise ValueError('Invalid observed endpoint')
    split=grouped_split(rows,config['train_fraction'],config['validation_fraction']);strata=sorted({r['stratum'] for r in split['train']});positions={s:i+2 for i,s in enumerate(strata)}
    def predict(params,rs):
        base=np.array([r['prediction'] for r in rs]);offset=np.array([params[positions[r['stratum']]] if r['stratum'] in positions else 0 for r in rs])
        return expit(params[0]+params[1]*logit(np.clip(base,1e-9,1-1e-9))+offset)
    y=np.array([r['outcome'] for r in split['train']])
    def loss(params):
        p=np.clip(predict(params,split['train']),1e-12,1-1e-12)
        return float(-(y*np.log(p)+(1-y)*np.log(1-p)).mean()+config['shrinkage']*np.sum(params[2:]**2))
    initial=np.zeros(len(strata)+2);initial[1]=1
    fitted=minimize(loss,initial,method='L-BFGS-B',bounds=[(-5,5),(0.1,5)]+[(-3,3)]*len(strata),options={'maxiter':config['max_iterations']})
    result={k:metrics(rs,predict(fitted.x,rs),config['bins']) for k,rs in split.items()}
    baseline={k:metrics(rs,[r['prediction'] for r in rs],config['bins']) for k,rs in split.items()}
    # Bootstrap independent connected groups, retaining clustered dependence.
    tests=split['test'];groups=connected_groups(tests)
    rng=np.random.default_rng(config['seed']);draws=[]
    for _ in range(config['bootstrap_draws']):
        sample=[r for key in rng.choice(len(groups),len(groups),replace=True) for r in groups[key]]
        draws.append(metrics(sample,predict(fitted.x,sample),config['bins'])['brier'])
    interval=np.quantile(draws,[.025,.975]).tolist() if draws else None
    return {'status':'fitted_requires_independent_review','automatically_accepted':False,'empirical_win_rate_validated':False,
        'provenance':{k:v for k,v in packet.items() if k!='observations'},'input_sha256':digest(packet),
        'optimizer_success':bool(fitted.success),'parameters':{'intercept':float(fitted.x[0]),'slope':float(fitted.x[1]),'stratum_offsets':{s:float(fitted.x[i]) for s,i in positions.items()}},
        'split':{k:{'samples':len(rs),'players':sorted({r['player_id'] for r in rs}),'matches':sorted({r['match_id'] for r in rs})} for k,rs in split.items()},
        'metrics':result,'baseline':baseline,'test_brier_cluster_bootstrap_95_interval':interval,
        'drift':{'test_minus_validation_brier':result['test']['brier']-result['validation']['brier'],
                 'unseen_test_strata':sorted({r['stratum'] for r in tests}-set(strata))},
        'excluded_cross_window_observations':len(rows)-sum(len(rs) for rs in split.values()),
        'limitations':['Connected groups spanning a time boundary are excluded.','Bootstrap interval covers this sample; physical missing-data bounds remain separate.']}


def community_annotations(packet,vehicle_ids):
    """Qualitative, attributable commentary, intentionally independent of scoring."""
    required=('source','sampling_window','game_version','mode','sample_count','deduplication','bias_notes','authorization')
    if any(not packet.get(k) for k in required):raise ValueError('Incomplete community provenance')
    rows=packet.get('annotations',[])
    if packet['sample_count']!=len(rows) or packet['mode']!='ground_rb':raise ValueError('Invalid community sample')
    result=[]
    for row in rows:
        if row.get('vehicle_id') not in vehicle_ids or not row.get('source_url') or not isinstance(row.get('labels'),list) or not row.get('disputes'):raise ValueError('Unattributed community annotation')
        result.append({k:row[k] for k in ('vehicle_id','source_url','labels','disputes')})
    return {'provenance':{k:packet[k] for k in required},'annotations':result,'used_for_scoring':False,'sha256':digest(packet)}
