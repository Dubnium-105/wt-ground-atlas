"""Portable adapter around the accepted V2 full-interval geometry builder.

The numerical interval core is copied from build_layered_cache_v2.py.
No native game execution or external project directory is required.
"""
from pathlib import Path
import hashlib
import json
import math
import time

import numpy as np
import grp_decoder as decode
from wt_armor.gpu_local_geometry import GpuLocalMesh
from wt_armor.native_loaded_blas import LoadedChunks
from wt_armor.native_loaded_bvh import install_quantized_cache
from wt_armor.native_blas_chunk import collision_chunks


def sha_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def rays(plan, group):
    """All yaw views, H,W,7 float32. Geometry-only, ammunition independent."""
    w, h = plan['width'], plan['height']
    horizontal = plan['world_width_m']/2 - (np.arange(w, dtype=np.float64)+.5)*plan['pixel_pitch_m']
    height = plan['top_y_m'] - (np.arange(h, dtype=np.float64)+.5)*plan['pixel_pitch_m']
    result = np.empty((len(plan['angles_deg']), h, w, 7), np.float32)
    cx, cz = group['center_xz_m']; hx, hz = group['half_xz_m']
    for i, angle in enumerate(plan['angles_deg']):
        a = math.radians(angle); c, s = math.cos(a), math.sin(a)
        if angle % 90 == 0:
            c, s = {0:(1,0),90:(0,1),180:(-1,0),270:(0,-1)}[angle]
        d = abs(c)*hx + abs(s)*hz + plan['camera_margin_m']
        result[i,:,:,0] = cx+c*d-s*horizontal[None,:]
        result[i,:,:,1] = height[:,None]
        result[i,:,:,2] = cz+s*d+c*horizontal[None,:]
        result[i,:,:,3:] = [-c,0,-s,plan['ray_limit_m']]
    return result


def request_fingerprint(grp_path, profile_path, data, plan):
    lib = Path(__file__).resolve().parent
    files = [profile_path, data / "runtime" / "loader.json", data / "runtime" / "constants.json"]
    loader = read_json(data / "runtime" / "loader.json")
    files += [data / "runtime" / loader[key] for key in ("case_data_file", "runtime_blob_file")]
    files += [lib / name for name in ("grp_geometry.py", "grp_decoder.py")]
    files += sorted((lib / "wt_armor").glob("*.py"))
    content = {"grp_sha256": sha_file(grp_path), "plan": plan,
               "dependencies": [(path.name, sha_file(path)) for path in files]}
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def build_or_load(grp_path, profile_path, data, plan, cache, rebuild=False, ray_batch=128):
    profile = read_json(profile_path)
    fingerprint = request_fingerprint(grp_path, profile_path, data, plan)
    if sha_file(grp_path) != profile["grp_sha256"]:
        raise ValueError("GRP does not match the version-pinned profile")
    directory = cache / profile["vehicle"] / fingerprint[:20]
    metadata = directory / (profile["vehicle"] + ".layers.json")
    packet = metadata.with_suffix(".npz")
    if not rebuild and metadata.is_file() and packet.is_file():
        previous = read_json(metadata)
        if (previous.get("request_fingerprint") == fingerprint
                and previous.get("all_inward_node_intervals_retained")
                and sha_file(packet) == previous.get("arrays_sha256")):
            print(profile["vehicle"] + ": reuse geometry cache", flush=True)
            return metadata, True
    build(grp_path, profile, data, plan, directory, fingerprint, ray_batch)
    return metadata, False


def build(grp_path, profile, data, plan, out, fingerprint, ray_batch):
    vehicle = profile["vehicle"]
    start = time.perf_counter()
    raw = grp_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != profile["grp_sha256"]:
        raise ValueError("Input GRP changed during reading")
    resources = decode.grp_entries(raw)
    resource = next((entry for entry in resources if entry["name"] == profile["collision_resource"]), None)
    if resource is None:
        raise ValueError("Expected collision resource is absent")
    body = decode.collision_body(raw, resource)
    parsed = decode.parse_collision(body)
    nodes = [{"index": node["index"], "name": node["name"], "indices_count": node["indices_count"],
              "behavior_flags": node["behavior_flags"]} for node in parsed["nodes"]]
    if nodes != profile["collision_nodes"]:
        raise ValueError("GRP collision node identity differs from material/binding profile")
    loader = LoadedChunks(data / "runtime" / "loader.json")
    chunks = loader.apply(vehicle, collision_chunks(body, parsed["nodes"]))
    materials = profile["materials"]
    if [m["node"] for m in materials] != list(range(len(nodes))):
        raise ValueError("Material mapping must cover every collision node in source order")
    group = profile["view_group"]

    constants=json.loads((data/'runtime/constants.json').read_text(encoding='utf8'))['values'];w,h=plan['width'],plan['height']
    records={k:[] for k in ['ray_id','node','entry_m','exit_m','incidence_cos','extra_entry_m']}
    meshes=[];transforms=[]
    for chunk in chunks:
        install_quantized_cache(chunk,loader.rows[(vehicle,chunk['node_index'])]['input']['wire_leaf_flags_hex'])
        mesh=GpuLocalMesh(chunk,constants);meshes.append(mesh)
        tm=np.asarray(parsed['authored_tm'][chunk['node_index']],np.float64).reshape(4,3).T
        inv=np.linalg.inv(tm[:,:3]);vertices=chunk['_original_ray_vertices'][0]
        world=vertices.astype(np.float64)@tm[:,:3].T+tm[:,3]
        transforms.append((inv,tm[:,3],world))
    for ai,angle in enumerate(plan['angles_deg']):
        view_rays=rays(dict(plan,angles_deg=[angle]),group)[0]
        a=math.radians(angle);c,s=math.cos(a),math.sin(a)
        if angle%90==0:c,s={0:(1,0),90:(0,1),180:(-1,0),270:(0,-1)}[angle]
        cx,cz=group['center_xz_m'];dworld=np.asarray([-c,0,-s]);count_before=sum(len(v) for v in records['ray_id'])
        for mesh,(inv,t,world),chunk in zip(meshes,transforms,chunks):
            ni=chunk['node_index']
            horizontal=-(world[:,0]-cx)*s+(world[:,2]-cz)*c
            px=(plan['world_width_m']/2-horizontal)/plan['pixel_pitch_m']-.5;py=(plan['top_y_m']-world[:,1])/plan['pixel_pitch_m']-.5
            x0=max(0,int(np.floor(px.min()))-2);x1=min(w,int(np.ceil(px.max()))+3)
            y0=max(0,int(np.floor(py.min()))-2);y1=min(h,int(np.ceil(py.max()))+3)
            if x0>=x1 or y0>=y1:continue
            yy,xx=np.mgrid[y0:y1,x0:x1];ids=(yy*w+xx).ravel();worldrays=view_rays.reshape(-1,7)[ids]
            localdir=inv@dworld;scale=np.linalg.norm(localdir);localdir=(localdir/scale).astype('f4')
            origins=((worldrays[:,:3].astype(np.float64)-t)@inv.T).astype('f4')
            orient=mesh.normals@localdir;inward=orient<0;outward=orient>0
            for at in range(0,len(ids),ray_batch):
                subset=ids[at:at+ray_batch];o=origins[at:at+ray_batch]
                queries=[{'origin':p.tolist(),'direction':localdir.tolist(),'max_distance':float(np.float32(plan['ray_limit_m']*scale)),'force_both':True} for p in o]
                dist=mesh.distances(queries);qi,face=np.nonzero(np.isfinite(dist))
                if not len(qi):continue
                order=np.lexsort((face,dist[qi,face],qi));qi,face=qi[order],face[order];distance=dist[qi,face]
                rr=[];ff=[];tt=[];ee=[]
                bounds=np.r_[0,np.flatnonzero(qi[1:]!=qi[:-1])+1,len(qi)]
                for begin,end in zip(bounds[:-1],bounds[1:]):
                    # Merge near-identical shared-triangle crossings only; no
                    # widened barycentric acceptance or invented outer shell.
                    unique=[int(begin)]
                    for j in range(int(begin)+1,int(end)):
                        if float(distance[j]-distance[unique[-1]])/scale>1e-5:unique.append(j)
                    for j,pos in enumerate(unique):
                        fi=face[pos]
                        if not inward[fi]:continue
                        nxt=unique[j+1] if j+1<len(unique) else None
                        ex=np.inf if nxt is None or not outward[face[nxt]] else distance[nxt]
                        rr.append(subset[qi[pos]]);ff.append(fi);tt.append(distance[pos]);ee.append(ex)
                if not rr:continue
                rid=np.asarray(rr,np.int32);fi=np.asarray(ff,np.int32);entry=np.asarray(tt,np.float32);exit_=np.asarray(ee,np.float32)
                norm=mesh.normals[fi].astype(np.float64)@inv
                norm/=np.linalg.norm(norm,axis=1)[:,None];cosine=np.clip(-(norm@dworld),0,1)
                records['ray_id'].append((rid+ai*w*h).astype('i4'));records['node'].append(np.full(len(rid),ni,'i4'))
                records['entry_m'].append((entry/scale).astype('f4'));records['exit_m'].append((exit_/scale).astype('f4'))
                records['incidence_cos'].append(cosine.astype('f4'));records['extra_entry_m'].append(np.full(len(rid),np.inf,'f4'))
        print(vehicle,angle,'entries',sum(len(v) for v in records['ray_id'])-count_before,flush=True)
    arrays={k:np.concatenate(v) if v else np.empty(0,'i4' if k in ['ray_id','node'] else 'f4') for k,v in records.items()}

    out.mkdir(parents=True, exist_ok=True)
    packet = out / (vehicle + ".layers.npz")
    np.savez_compressed(packet, **arrays)
    meta = {
        "vehicle": vehicle, "plan": plan, "materials": materials, "entries": len(arrays["ray_id"]),
        "build_seconds": time.perf_counter() - start,
        "geometry_model": "Every inward crossing and its nearest distinct same-node outward exit; shared-triangle crossings clustered within 10 micrometres. Mathematical authored-pose inverse; not full original collector parity.",
        "all_inward_node_intervals_retained": True, "edge_cluster_tolerance_m": 1e-5,
        "grp_sha256": hashlib.sha256(raw).hexdigest(), "arrays_sha256": sha_file(packet),
        "request_fingerprint": fingerprint, "profile_provenance": profile["provenance"],
        "builder_sha256": sha_file(Path(__file__)), "ray_batch": ray_batch,
        "collision_node_count": len(nodes), "mesh_node_count": len(chunks),
        "decoded_triangles": sum(len(chunk["faces"]) for chunk in chunks),
        "parsed_collision_bytes": parsed["parsed_bytes"], "collision_body_bytes": len(body),
    }
    (out / (vehicle + ".layers.json")).write_text(json.dumps(meta, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(f"{vehicle}: geometry complete, {meta['entries']} intervals, {meta['build_seconds']:.3f} s", flush=True)
