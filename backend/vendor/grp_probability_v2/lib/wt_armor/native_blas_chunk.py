"""Prepare an explicit full-body SoA4 BLAS from the pinned v3 wire geometry.

Geometry/quad fields and vertex bytes are preserved. This is a prepared
runtime representation, not proof of original loader byte-for-byte output.
The tree uses full leaves and conservative integer boxes, with no edge flags.
"""
import math
import struct
import numpy as np


def signed13(value):
    value &= 8191
    return value-8192 if value & 4096 else value


def leaf_faces(words):
    w0, w1, w2, w3 = words
    base = w1 & 0xffffff
    a, b = signed13(w0), signed13(w0 >> 13)
    c = signed13(((w0 >> 26) & 31) | ((w1 >> 24) << 5))
    a2, b2, c2 = signed13(w2 >> 16), signed13(w3), signed13(w3 >> 13)
    result = [(base, base+a, base+b)]
    if c != b:
        result.append((base+(b if w2 & (1 << 29) else a), base+(a if w2 & (1 << 29) else b), base+c))
    if a2 != b2:
        base2 = base+(w2 & 65535)
        result.append((base2, base2+a2, base2+b2))
        if c2 != b2:
            result.append((base2+(b2 if w2 & (1 << 30) else a2), base2+(a2 if w2 & (1 << 30) else b2), base2+c2))
    return result


def unpack_vertices(raw, frame):
    quantized = []
    for (word,) in struct.iter_unpack('<Q', raw):
        quantized.append([(word & 0x1fffff)/32., ((word >> 21) & 0x1fffff)/32., (word >> 42)/32.])
    quantized = np.asarray(quantized, dtype=np.float32)
    vertices = np.float32(np.float32(quantized*np.asarray(frame[3:6], dtype=np.float32))
                          + np.asarray(frame[6:9], dtype=np.float32))
    return quantized, vertices


def parse_wire_tree(data, start, tree_bytes, quantized):
    cursor, runtime_bytes, leaf_count = start, 0, 0
    all_faces = []

    def parse(depth):
        nonlocal cursor, runtime_bytes, leaf_count
        if depth > 256 or cursor >= len(data):
            raise ValueError('Invalid serialized BLAS depth/boundary')
        count = data[cursor]
        cursor += 1
        if count in [0, 255, 254, 253]:
            runtime_bytes += 28
            leaf_count += 1
            if count in [254, 253]:
                w0, w1 = struct.unpack_from('<II', data, cursor)
                words = [w0, w1, (1 << 29) if count == 253 else 0, 0]
                cursor += 8
            else:
                words = list(struct.unpack_from('<4I', data, cursor))
                cursor += 16
            faces = [] if count == 255 else leaf_faces(words)
            if faces and (not words[0] & 0x80000000 or min(map(min, faces)) < 0
                          or max(map(max, faces)) >= len(quantized)):
                raise ValueError('Invalid packed leaf geometry')
            all_faces.extend(faces)
            used = np.asarray([quantized[index] for face in faces for index in face])
            box = ([0., 0., 0.], [0., 0., 0.]) if not faces else (used.min(axis=0).tolist(), used.max(axis=0).tolist())
            return {'leaf': words, 'raw_nohit': count == 255, 'box': box, 'children': []}
        if not 1 <= count <= 4:
            raise ValueError('Prepared SoA4 requires wire fanout 1..4')
        runtime_bytes += 16
        children = [parse(depth+1) for _ in range(count)]
        box = ([min(child['box'][0][k] for child in children) for k in range(3)],
               [max(child['box'][1][k] for child in children) for k in range(3)])
        return {'children': children, 'box': box}

    roots = []
    while runtime_bytes < tree_bytes:
        roots.append(parse(0))
    if runtime_bytes != tree_bytes or not roots:
        raise ValueError('Serialized BLAS tree size differs')
    if len(roots) > 4:
        raise ValueError('Prepared BLAS root fanout exceeds four')
    root = roots[0] if len(roots) == 1 else {'children': roots, 'box': (
        [min(child['box'][0][k] for child in roots) for k in range(3)],
        [max(child['box'][1][k] for child in roots) for k in range(3)])}
    return root, cursor, leaf_count, all_faces


def prepare_runtime_chunk(root, vertex_bytes, frame):
    output = bytearray()
    patches = []

    def resolve(node):
        while len(node['children']) == 1:
            node = node['children'][0]
        return node

    def emit(node):
        node = resolve(node)
        children = [resolve(child) for child in node['children']]
        if not children:
            if node['raw_nohit']:
                raise ValueError('Raw no-hit leaf cannot be the entire BLAS')
            offset = len(output)
            output.extend(struct.pack('<5I', *node['leaf'], 0))
            patches.append((offset+4, node['leaf'][1], node['leaf'][1] & 0xffffff))
            return offset
        n = len(children)
        offset = len(output)
        output.extend(b'\0'*(16*n+12*sum(not child['children'] for child in children)))
        body = offset+16*n
        for i, child in enumerate(children):
            for axis in range(3):
                low = min(65535, max(0, math.floor(child['box'][0][axis])-1))
                high = min(65535, max(0, math.ceil(child['box'][1][axis])+1))
                struct.pack_into('<H', output, offset+(axis*n+i)*2, low)
                struct.pack_into('<H', output, offset+((axis+3)*n+i)*2, high)
            if child['children']:
                word = emit(child)
            else:
                word, w1, w2, w3 = child['leaf']
                struct.pack_into('<III', output, body, w1, w2, w3)
                patches.append((body, w1, 0 if child['raw_nohit'] else w1 & 0xffffff))
                body += 12
            struct.pack_into('<I', output, offset+12*n+i*4, word)
        return offset | (n-1)

    root_ref = emit(root)
    tree_bytes = len(output)
    vertices_offset = (tree_bytes+7) & ~7
    output.extend(b'\0'*(vertices_offset-tree_bytes))
    output.extend(vertex_bytes)
    for body, previous, base_vertex in patches:
        relative_bytes = vertices_offset+base_vertex*8-body
        if relative_bytes < 0 or relative_bytes % 4 or relative_bytes//4 > 0xffffff:
            raise ValueError('Prepared vertex-relative base is out of range')
        struct.pack_into('<I', output, body, (previous & 0xff000000) | (relative_bytes//4))
    return struct.pack('<9fIII', *frame, tree_bytes, root_ref, 0)+output


def collision_chunks(body, nodes):
    counts = struct.unpack_from('<11I', body, 64)
    cursor = 108+counts[0]*8+counts[0]*48+sum(counts[1:4])*48
    result = []
    for node in nodes:
        if not node['indices_count']:
            continue
        frame = struct.unpack_from('<9f', body, cursor)
        cursor += 36
        magic, version, stride, leaf_size, tree_bytes, vertex_count, *bounds = struct.unpack_from('<iHBBII6f', body, cursor)
        cursor += 40
        if magic != 0x694c4264 or version not in [1, 2] or stride != 8 or leaf_size != 28:
            raise ValueError('Unknown serialized BLAS header')
        flags = struct.unpack_from('<I', body, cursor)[0] if version == 2 else 0
        cursor += 4 if version == 2 else 0
        if flags & ~1:
            raise ValueError('Unknown serialized BLAS flags')
        raw = body[cursor:cursor+vertex_count*8]
        cursor += vertex_count*8
        quantized, vertices = unpack_vertices(raw, frame)
        root, cursor, leaves, faces = parse_wire_tree(body, cursor, tree_bytes, quantized)
        if len(faces)*3 != node['indices_count']:
            raise ValueError('Packed face count differs')
        if flags & 1:
            cursor += leaves*2
        result.append({'node_index': node['index'], 'node_flags': node['flags'],
            'behavior_flags': node['behavior_flags'], 'frame': frame,
            'vertices': vertices, 'faces': np.asarray(faces, dtype=np.int64),
            'prepared_bytes': prepare_runtime_chunk(root, raw, frame), 'leaf_count': leaves,
            'wire_tree_bytes': tree_bytes})
    return result


def native_triangle_hits(vertices, faces, origin, direction, max_distance, both,
                         minimum_barycentric_numerator, maximum_barycentric_sum):
    """Scalar float32 port of the original leaf's oriented triangle decisions.

The asymmetric numerator/sum limits are explicit pinned binary constants.
This returns face-order projections, not native traversal order or tri_ref.
"""
    f = np.float32
    triangles = np.asarray(vertices, dtype=np.float32)[faces]
    origin, direction = np.asarray(origin, dtype=np.float32), np.asarray(direction, dtype=np.float32)
    p = f(triangles[:, 0]-origin)
    r, e = f(triangles[:, 0]-triangles[:, 1]), f(triangles[:, 2]-triangles[:, 0])

    def cross_reversed(a, b):
        return np.column_stack([f(f(a[:, 2]*b[:, 1])-f(a[:, 1]*b[:, 2])),
            f(f(a[:, 0]*b[:, 2])-f(a[:, 2]*b[:, 0])), f(f(a[:, 1]*b[:, 0])-f(a[:, 0]*b[:, 1]))])

    def dot_reverse(a, b):
        return f(f(f(a[:, 2]*b[:, 2])+f(a[:, 1]*b[:, 1]))+f(a[:, 0]*b[:, 0]))

    test_normal = cross_reversed(e, r)
    q = cross_reversed(p, np.broadcast_to(direction, p.shape))
    det = dot_reverse(np.broadcast_to(direction, p.shape), test_normal)
    sign = np.where(np.signbit(det) & both, np.uint32(0x80000000), np.uint32(0))
    det = (det.view(np.uint32) ^ sign).view(np.float32)
    u = (dot_reverse(e, q).view(np.uint32) ^ sign).view(np.float32)
    v = (dot_reverse(r, q).view(np.uint32) ^ sign).view(np.float32)
    tn = (dot_reverse(p, test_normal).view(np.uint32) ^ sign).view(np.float32)
    with np.errstate(divide='ignore', invalid='ignore'):
        inv = f(f(1.)/det)
        t = f(tn*inv)
        total = f(f(u+v)*inv)
    valid = ((det > f(0.)) & (t >= f(0.)) & (t < f(max_distance))
             & (u >= f(minimum_barycentric_numerator)) & (v >= f(minimum_barycentric_numerator))
             & (total <= f(maximum_barycentric_sum)))
    normal = cross_reversed(e, f(triangles[:, 1]-triangles[:, 0]))
    return [{'face_index': int(i), 'normal_unnormalized': normal[i].astype(float).tolist(),
             'distance_m': float(t[i]), 'barycentric_numerators': [float(u[i]), float(v[i])],
             'barycentric_sum': float(total[i])} for i in np.flatnonzero(valid)]
