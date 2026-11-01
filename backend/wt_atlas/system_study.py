"""Package source-bound system-member research reports for static display."""
import hashlib
import html
import math
from pathlib import Path

from .common import file_hash, read_json
from .forecast.data import number, safe_file
from .forecast.delivery import delivery_issue
from .forecast.aim import skill_nodes
from .forecast.system_physics import declared_members, unknown_exposure_bounds


def prepare_study(catalog_path, ammunition_path, bindings_path, config_path):
    catalog_path=Path(catalog_path).resolve()
    ammunition_path=Path(ammunition_path).resolve()
    bindings_path=Path(bindings_path).resolve()
    config_path=Path(config_path).resolve()
    catalog=read_json(catalog_path);version=catalog['snapshot']['client_source']['version']
    catalog_sha=file_hash(catalog_path);ammunition_sha=file_hash(ammunition_path);bindings_sha=file_hash(bindings_path);config_sha=file_hash(config_path)
    bindings=read_json(bindings_path).get('bindings',{})
    config=read_json(config_path)
    expected_scenarios={(s['id'],s['physical_distance_m'],s['yaw_deg']) for s in config['scenarios']}
    if (not expected_scenarios or len(expected_scenarios)!=len(config['scenarios']) or
            len({s['id'] for s in config['scenarios']})!=len(config['scenarios'])):raise ValueError('Duplicate or missing configured scenario')
    profiles=read_json(ammunition_path);profile_by_name={p['name']:p for p in profiles}
    if len(profiles)!=len(profile_by_name):raise ValueError('Duplicate system research profile')
    vehicles={v['id']:v for v in catalog.get('vehicles',[])}
    return locals()


def build_study(report_paths, catalog_path, ammunition_path, bindings_path, config_path, root, *, _context=None):
    root=Path(root).resolve();paths=[Path(p).resolve() for p in report_paths]
    if not paths or len(set(paths))!=len(paths):raise ValueError('Missing or duplicate system research reports')
    ctx=_context or prepare_study(catalog_path,ammunition_path,bindings_path,config_path)
    catalog_path,ammunition_path,bindings_path,config_path=(ctx[k] for k in ('catalog_path','ammunition_path','bindings_path','config_path'))
    catalog,version,catalog_sha,ammunition_sha,bindings_sha,config_sha=(ctx[k] for k in ('catalog','version','catalog_sha','ammunition_sha','bindings_sha','config_sha'))
    bindings,config,expected_scenarios,profile_by_name,vehicles=(ctx[k] for k in ('bindings','config','expected_scenarios','profile_by_name','vehicles'))
    common={catalog_path:catalog_sha,ammunition_path:ammunition_sha,bindings_path:bindings_sha,config_path:config_sha}
    systems=[];identities=None;scenario_keys=None;seen=set();hashes={}
    def label(identifier):return html.unescape(str(vehicles[identifier].get('name') or identifier))
    for path in paths:
        before=file_hash(path);report=read_json(path);inputs=report.get('input_sha256',{});code=report.get('source_code_sha256_lf',{})
        if (report.get('schema_version')!='1.0.0' or report.get('publishable') is not False or
            report.get('formal_rankings')!=0 or report.get('target_selection_weights')!='unknown' or
            not inputs or not code):raise ValueError('Invalid system research report')
        catalog_keys=[k for k in inputs if Path(k.replace('\\','/')).name==catalog_path.name]
        if len(catalog_keys)!=1 or inputs[catalog_keys[0]]!=catalog_sha:raise ValueError('System research catalog mismatch')
        ammunition_keys=[k for k in inputs if Path(k.replace('\\','/')).name==ammunition_path.name]
        if len(ammunition_keys)!=1 or inputs[ammunition_keys[0]]!=ammunition_sha:raise ValueError('System research ammunition mismatch')
        binding_keys=[k for k in inputs if Path(k.replace('\\','/')).name==bindings_path.name]
        if len(binding_keys)!=1 or inputs[binding_keys[0]]!=bindings_sha:raise ValueError('System research geometry bindings mismatch')
        config_keys=[k for k in inputs if Path(k.replace('\\','/')).name==config_path.name]
        if len(config_keys)!=1 or inputs[config_keys[0]]!=config_sha:raise ValueError('System research configuration mismatch')
        for name,sha in inputs.items():
            source=Path(name.replace('\\','/'));source=source if source.is_absolute() else root/source
            if (common[source.resolve()] if _context and source.resolve() in common else file_hash(source))!=sha:raise ValueError('System research input changed')
        for name,sha in code.items():
            source=safe_file(root,name)
            if hashlib.sha256(source.read_bytes().replace(b'\r\n',b'\n')).hexdigest()!=sha:
                raise ValueError('System research code changed')
        identity=(report.get('attacker_id'),report.get('ammo_id'),report.get('skill_profile'),inputs,code)
        attackers=[v for v in catalog.get('vehicles',[]) if v.get('id')==identity[0] and v.get('kind')=='ground']
        if len(attackers)!=1 or sum(a.get('id')==identity[1] for a in attackers[0].get('ammo',[]))!=1:
            raise ValueError('System research attacker or ammunition is not owned')
        prior=config.get('skill_profiles',{}).get(identity[2])
        if not isinstance(prior,dict) or prior.get('distribution')!='normal_assumption':raise ValueError('System research skill profile mismatch')
        expected_nodes=skill_nodes(prior)
        selected=next(a for a in attackers[0]['ammo'] if a.get('id')==identity[1])
        if selected.get('ordered_belt'):raise ValueError('System research requires homogeneous ammunition')
        if identities is not None and identity!=identities:raise ValueError('System research inputs differ')
        identities=identity;system=report.get('system_id')
        if not isinstance(system,str) or not system or system in seen:raise ValueError('Duplicate or missing system')
        declared={m['vehicle_id']:(m['role'],m['count']) for m in declared_members(catalog,system)}
        seen.add(system);rows=[];keys=set()
        for row in report.get('rows',[]):
            distance,yaw=row.get('physical_distance_m'),row.get('yaw_deg');bound=row.get('exposure_bounds',{})
            scenario_id=row.get('scenario_id');key=(scenario_id,distance,yaw)
            if (not isinstance(scenario_id,str) or not scenario_id or not number(distance) or distance<0 or not number(yaw) or not 0<=yaw<360 or key in keys or
                bound.get('mean') is not None or bound.get('target_selection_weights')!='unknown' or
                not number(bound.get('lower')) or not number(bound.get('upper')) or
                not 0<=bound['lower']<=bound['upper']<=1):raise ValueError('Invalid system research scenario')
            keys.add(key);members=[]
            profile=profile_by_name.get(row.get('profile_id'));provenance=(profile or {}).get('provenance',{})
            if (profile is None or profile.get('mode') not in ('KE','CE') or profile.get('reference_only') or
                profile.get('distance_m')!=distance or identity[1] not in provenance.get('penetration_source_ammo_ids',[]) or
                provenance.get('client_version')!=version or provenance.get('datamine_commit')!=catalog['snapshot'].get('datamine_reference') or
                delivery_issue(profile,distance)):
                raise ValueError('System research projectile source or distance mismatch')
            for member in row.get('members',[]):
                lo,hi=member.get('lower'),member.get('upper');status=member.get('status')
                if (status not in ('source_bound_map','missing_map') or not number(lo) or not number(hi) or
                    not 0<=lo<=hi<=1 or not isinstance(member.get('vehicle_id'),str) or
                    member.get('vehicle_id') not in declared or member.get('role') not in ('basic','component') or type(member.get('count')) is not int or member['count']<1):
                    raise ValueError('Invalid system research member')
                if status=='missing_map' and (lo,hi)!=(0.,1.):raise ValueError('Missing map must remain unknown')
                if status=='source_bound_map' and any(not isinstance(member.get(k),str) or len(member[k])!=64 for k in ('map_id','array_sha256','target_sha256')):
                    raise ValueError('System research member source identity missing')
                if status=='source_bound_map' and bindings.get(member['vehicle_id'],{}).get('target_sha256')!=member['target_sha256']:
                    raise ValueError('System research geometry binding changed')
                nodes=member.get('by_skill',[]);weight=math.fsum(n.get('weight',0) for n in nodes)
                if (len(nodes)!=len(expected_nodes) or any(not math.isclose(n.get('skill',float('nan')),skill,rel_tol=0.,abs_tol=1e-10) or
                    not math.isclose(n.get('weight',float('nan')),w,rel_tol=0.,abs_tol=1e-10) for n,(skill,w) in zip(nodes,expected_nodes)) or
                    not math.isclose(weight,1.,rel_tol=0.,abs_tol=1e-10) or
                    not math.isclose(math.fsum(n['weight']*n['lower'] for n in nodes)/weight,lo,rel_tol=0.,abs_tol=1e-10) or
                    not math.isclose(math.fsum(n['weight']*n['upper'] for n in nodes)/weight,hi,rel_tol=0.,abs_tol=1e-10)):
                    raise ValueError('System research skill integration differs')
                members.append({**{k:member.get(k) for k in ('vehicle_id','role','count','status','lower','upper','map_id','array_sha256','target_sha256')},'name':label(member['vehicle_id'])})
            if not members:raise ValueError('System research members missing')
            if len(members)!=len(declared) or {m['vehicle_id']:(m['role'],m['count']) for m in members}!=declared:
                raise ValueError('System research membership differs from catalog')
            actual=unknown_exposure_bounds(row['members'])
            if (not math.isclose(actual['lower'],bound['lower'],rel_tol=0.,abs_tol=1e-10) or
                    not math.isclose(actual['upper'],bound['upper'],rel_tol=0.,abs_tol=1e-10)):
                raise ValueError('System research selection bounds differ')
            rows.append({'scenario_id':scenario_id,'distance_m':distance,'yaw_deg':yaw,'lower':bound['lower'],'upper':bound['upper'],
                         'profile_id':row.get('profile_id'),'members':members})
        if not rows or keys!=expected_scenarios or scenario_keys is not None and keys!=scenario_keys:raise ValueError('System research scenarios differ')
        scenario_keys=keys;systems.append({'system_id':system,'name':label(system),'report_sha256':before,'rows':rows});hashes[str(path)]=before
    if any(file_hash(Path(path))!=sha for path,sha in hashes.items()) or not _context and any(file_hash(path)!=sha for path,sha in common.items()):
        raise ValueError('System research source changed during packaging')
    return {'schema_version':'1.0.0','source_version':version,'catalog_sha256':catalog_sha,'ammunition_sha256':ammunition_sha,'bindings_sha256':bindings_sha,'config_sha256':config_sha,
            'attacker_id':identities[0],'attacker_name':label(identities[0]),'ammo_id':identities[1],
            'ammo_name':html.unescape(str(next(a for a in vehicles[identities[0]]['ammo'] if a['id']==identities[1]).get('name') or identities[1])),
            'skill_profile':identities[2],
            'input_sha256':identities[3],'source_code_sha256_lf':identities[4],
            'systems':systems,'target_selection_weights':'unknown','publishable':False,'formal_rankings':0}
