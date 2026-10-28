"""Original GRP decoder functions; legacy path globals/CLI removed."""
from __future__ import annotations
import struct
import math
import hashlib
import zstandard


def u32(b: bytes, p: int) -> int:
    return struct.unpack_from("<I", b, p)[0]


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def grp_entries(data: bytes):
    if data[:4] != b"GRP2":
        raise ValueError("not a GRP2 file")
    # GRP2's offsets are based at byte 12 in the file, except resource offsets,
    # which are absolute file positions.
    base = 12
    names_ofs = u32(data, 16) - base
    names_count = u32(data, 20)
    entries_ofs = u32(data, 32) - base
    entries_count = u32(data, 36)
    if names_count != entries_count or names_count > 100000:
        raise ValueError("invalid GRP2 directory counts")
    index = struct.unpack_from("<" + "I" * names_count, data, base + names_ofs)
    strings = data[0x40 : base + names_ofs]
    names = []
    for ofs in index:
        start = ofs - 0x40
        end = strings.find(b"\0", start)
        if start < 0 or end < 0:
            raise ValueError("invalid GRP2 name index")
        names.append(strings[start:end].decode("utf-8"))
    entries = []
    for i, name in enumerate(names):
        cls, ofs, real_id, reserved = struct.unpack_from("<IIHH", data, base + entries_ofs + i * 12)
        end = (struct.unpack_from("<I", data, base + entries_ofs + (i + 1) * 12 + 4)[0]
               if i + 1 < names_count else len(data))
        entries.append({"name": name, "class_id": cls, "offset": ofs, "end": end,
                        "real_id": real_id, "reserved": reserved})
    return entries


def collision_body(grp: bytes, entry: dict) -> bytes:
    if entry["class_id"] != 0xACE50000:
        raise ValueError("collision resource has wrong class id")
    ofs = entry["offset"]
    label = u32(grp, ofs)
    if label != 0xACE50003:
        raise ValueError(f"unsupported collision stream label {label:#010x}")
    packed_size = int.from_bytes(grp[ofs + 4 : ofs + 7], "little")
    method = grp[ofs + 7]
    if method != 0x40:
        raise ValueError(f"unsupported packed method {method:#x}; expected ZSTD")
    packed = grp[ofs + 8 : ofs + 8 + packed_size]
    body = zstandard.ZstdDecompressor().decompress(packed)
    return body


def s13(v: int) -> int:
    v &= 0x1FFF
    return v - 0x2000 if v & 0x1000 else v


def unpack_leaf(w0: int, w1: int, w2: int, w3: int):
    o1 = s13(w0)
    o2 = s13(w0 >> 13)
    o3 = s13(((w0 >> 26) & 0x1F) | ((w1 >> 24) << 5))
    o1b = s13(w2 >> 16)
    o2b = s13(w3)
    o3b = s13(w3 >> 13)
    return (w1 & 0xFFFFFF, w2 & 0xFFFF, o1, o2, o3, o1b, o2b, o3b,
            bool(w2 & (1 << 29)), bool(w2 & (1 << 30)))


def decode_tree(data: bytes, pos: int, tree_bytes: int, vertices: list[tuple[float, float, float]]):
    """Read the disk preorder forest, accounting for runtime size as Dagor does."""
    runtime_bytes = 0
    leaves = 0
    faces: list[tuple[int, int, int]] = []

    def node(offset: int, depth: int):
        nonlocal runtime_bytes, leaves
        if depth > 256 or offset >= len(data):
            raise ValueError("BVH depth/stream boundary exceeded")
        cc = data[offset]
        offset += 1
        runtime_bytes += 28 if cc in (0, 0xFF, 0xFE, 0xFD) else 16
        if cc in (0xFF, 0xFE, 0xFD, 0):
            leaves += 1
            if cc in (0xFE, 0xFD):
                w0, w1 = struct.unpack_from("<II", data, offset)
                offset += 8
                w2 = (1 << 29) if cc == 0xFD else 0
                w3 = 0
            else:
                w0, w1, w2, w3 = struct.unpack_from("<IIII", data, offset)
                offset += 16
            if cc == 0xFF:
                if ((w0 & 0x7FFFFFFF) | w1 | w2 | w3) != 0:
                    raise ValueError("nonempty RAW leaf")
                return offset
            if not (w0 & 0x80000000):
                raise ValueError("leaf missing QUAD_LEAF_FLAG")
            base, delta_b, o1, o2, o3, o1b, o2b, o3b, flip_a, flip_b = unpack_leaf(w0, w1, w2, w3)
            tris = [(base, base + o1, base + o2)]
            if o3 != o2:
                tris.append((base + (o2 if flip_a else o1), base + (o1 if flip_a else o2), base + o3))
            if o1b != o2b:
                base_b = base + delta_b
                tris.append((base_b, base_b + o1b, base_b + o2b))
                if o3b != o2b:
                    tris.append((base_b + (o2b if flip_b else o1b), base_b + (o1b if flip_b else o2b), base_b + o3b))
            for tri in tris:
                if min(tri) < 0 or max(tri) >= len(vertices):
                    raise ValueError(f"leaf vertex index out of range: {tri}")
                faces.append(tri)
            return offset
        if cc == 0:
            raise ValueError("unreachable leaf marker")
        if cc > 252:
            raise ValueError(f"unsupported BVH fanout {cc}")
        for _ in range(cc):
            offset = node(offset, depth + 1)
        return offset

    while runtime_bytes < tree_bytes:
        pos = node(pos, 0)
    if runtime_bytes != tree_bytes:
        raise ValueError(f"runtime tree size mismatch: {runtime_bytes} != {tree_bytes}")
    return pos, leaves, faces


def vec21(raw: bytes, pos: int) -> tuple[float, float, float]:
    word = struct.unpack_from("<Q", raw, pos)[0]
    return ((word & 0x1FFFFF) / 32.0,
            ((word >> 21) & 0x1FFFFF) / 32.0,
            ((word >> 42) & 0x1FFFFF) / 32.0)


def mat43_transform(tm: tuple[float, ...], p: tuple[float, float, float]):
    # Dagor TMatrix stores four Point3 rows; point*matrix uses column components.
    x, y, z = p
    return (tm[0]*x + tm[3]*y + tm[6]*z + tm[9],
            tm[1]*x + tm[4]*y + tm[7]*z + tm[10],
            tm[2]*x + tm[5]*y + tm[8]*z + tm[11])


def parse_collision(body: bytes):
    if len(body) < 108:
        raise ValueError("short collision v3 body")
    counts = struct.unpack_from("<11I", body, 64)
    n = counts[0]
    pos = 108 + n * 8  # bind records
    nodes = []
    for i in range(n):
        v = struct.unpack_from("<IIHhHHHBB6ff", body, pos + i * 48)
        name_ofs, indices, behavior, physmat, inside, prim_ofs, planes, flags, typ = v[:9]
        bbox = v[9:15]
        radius = v[15]
        nodes.append({"index": i, "name_ofs": name_ofs, "indices_count": indices,
                      "behavior_flags": behavior, "phys_mat_id": physmat,
                      "inside_node": inside, "prim_ofs": prim_ofs,
                      "planes_count": planes, "flags": flags, "type": typ,
                      "bbox": list(bbox), "radius": radius})
    pos += n * 48
    authored = []
    if counts[1] != n:
        raise ValueError("authored transform count != node count")
    for i in range(n):
        authored.append(struct.unpack_from("<12f", body, pos + i * 48))
    pos += counts[1] * 48
    matrices = []
    for count in counts[2:4]:
        arr = [struct.unpack_from("<12f", body, pos + i * 48) for i in range(count)]
        matrices.append(arr)
        pos += count * 48
    # Collision BLAS chunks follow in node order and are present for nodes with triangle indices.
    total_faces = 0
    chunks = []
    for node_rec in nodes:
        if not node_rec["indices_count"]:
            continue
        frame = struct.unpack_from("<9f", body, pos)
        pos += 36
        magic, version, stride, leaf_size, tree_bytes, vert_count, *box = struct.unpack_from("<iHBBII6f", body, pos)
        pos += 40
        if magic != 0x694C4264 or version not in (1, 2) or stride != 8 or leaf_size != 28:
            raise ValueError(f"invalid bvhIO header at {pos-40}")
        io_flags = 0
        if version >= 2:
            io_flags = u32(body, pos)
            pos += 4
            if io_flags & ~1:
                raise ValueError(f"unknown bvhIO flags {io_flags:#x}")
        verts_raw = body[pos : pos + vert_count * 8]
        if len(verts_raw) != vert_count * 8:
            raise ValueError("short BLAS vertex array")
        pos += len(verts_raw)
        sc = frame[3:6] # invScale
        bmin = frame[6:9]
        verts = []
        for vi in range(vert_count):
            q = vec21(verts_raw, vi * 8)
            verts.append(tuple(q[k] * sc[k] + bmin[k] for k in range(3)))
        pos, leaf_count, faces = decode_tree(body, pos, tree_bytes, verts)
        if io_flags & 1:
            pos += leaf_count * 2
        if len(faces) * 3 != node_rec["indices_count"]:
            raise ValueError(f"node {node_rec['index']} triangle count {len(faces)} != indicesCount/3 {node_rec['indices_count']//3}")
        local_tm = authored[node_rec["index"]]
        # Collision mesh points are stored in the resource model frame when authored pose is identity;
        # for non-IDENT poses, TMatrix maps the node-local geometry to that bind frame.
        fxyz = [mat43_transform(local_tm, p) for p in verts]
        chunks.append({"node_index": node_rec["index"], "verts": fxyz, "raw_verts": verts, "faces": faces,
                       "leaf_count": leaf_count, "tree_bytes": tree_bytes,
                       "frame": list(frame), "bvh_box": box})
        total_faces += len(faces)
    if counts[4] and total_faces == 0:
        raise ValueError("non-empty NODE_BLAS count but no decoded geometry")
    # Remaining wire arrays: TLAS(5), phys material pool(6), capsules(7), convex planes(8), names(9), mat names(10).
    pos += counts[5]
    pos += counts[6] * 2
    pos += counts[7] * 28
    pos += counts[8] * 16
    names = body[pos : pos + counts[9]]
    pos += counts[9]
    mat_names = body[pos : pos + counts[10]]
    pos += counts[10]
    if len(names) != counts[9] or len(mat_names) != counts[10]:
        raise ValueError("truncated terminal arrays")
    if pos > len(body):
        raise ValueError("collision stream overrun")
    for nr in nodes:
        ofs = nr["name_ofs"]
        if ofs == 0:
            nr["name"] = ""
        elif ofs >= len(names):
            raise ValueError("node name offset out of range")
        else:
            end = names.find(b"\0", ofs)
            if end < 0:
                raise ValueError("unterminated node name")
            nr["name"] = names[ofs:end].decode("utf-8", "replace")
    if body[pos:].strip(b"\0"):
        # Tail extension records are accepted only with valid (tag, byte-count) framing.
        tail = pos
        while tail < len(body):
            if len(body) - tail < 8:
                raise ValueError("truncated collision tail header")
            tag, size = struct.unpack_from("<II", body, tail)
            tail += 8
            if not tag or tail + size > len(body):
                raise ValueError("invalid collision tail record")
            tail += size
        pos = tail
    return {"counts": list(counts), "nodes": nodes, "authored_tm": [list(tm) for tm in authored],
            "authored_itm": [list(tm) for tm in matrices[0]], "rel_geom_tm": [list(tm) for tm in matrices[1]],
            "chunks": chunks, "name_bytes": counts[9], "mat_name_bytes": counts[10],
            "body_bytes": len(body), "parsed_bytes": pos}
