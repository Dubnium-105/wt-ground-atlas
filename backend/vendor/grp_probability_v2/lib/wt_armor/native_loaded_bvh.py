"""Decode original compact SoA4 leaves and their edge-flag tails.

This reads loader-produced bytes independently of the native queries. The
slab cache uses the existing pinned float32 admission calculation unchanged.
"""
import struct
import numpy as np
from .native_blas_chunk import leaf_faces


def decode_runtime_leaves(chunk):
    raw = chunk['prepared_bytes']
    tree_bytes, root, flags = struct.unpack_from('<III', raw, 36)
    if flags != 1:
        raise ValueError('Expected original edge-flagged runtime chunk')
    payload = raw[48:]
    vertices_offset = (tree_bytes+7)&~7
    output = []
    visited = set()

    def leaf(word, body, short, box, edge_word):
        w1 = struct.unpack_from('<I', payload, body)[0]
        if short:
            w2, w3 = ((w1>>23)&1)<<29, 0
            w1 &= 0xff7fffff
        else:
            w2, w3 = struct.unpack_from('<II', payload, body+4)
        target = body+(w1&0xffffff)*4
        delta = target-vertices_offset
        if delta < 0 or delta % 8:
            raise ValueError('Original compact vertex reference is unaligned')
        base = delta//8
        faces = leaf_faces([word, (w1&0xff000000)|base, w2, w3])
        if any(v < 0 or v >= len(chunk['vertices']) for face in faces for v in face):
            raise ValueError('Original compact vertex index outside source buffer')
        output.append({'faces': faces, 'quantized_box': box, 'edge_flags': edge_word,
            'short': bool(short), 'body_offset': body})

    def node(ref):
        count, offset, short_mask = (ref&3)+1, ref&0x03fffffc, (ref>>26)&15
        if offset in visited or offset+16*count > tree_bytes:
            raise ValueError('Original compact node cycle or truncated node')
        visited.add(offset)
        words = struct.unpack_from('<'+'I'*count, payload, offset+12*count)
        body = offset+16*count
        flags_at = body+sum(4 if short_mask&(1<<i) else 12 for i,w in enumerate(words) if w&0x80000000)
        for i, word in enumerate(words):
            low = [struct.unpack_from('<H', payload, offset+(axis*count+i)*2)[0] for axis in range(3)]
            high = [struct.unpack_from('<H', payload, offset+((axis+3)*count+i)*2)[0] for axis in range(3)]
            if word&0x80000000:
                short = bool(short_mask&(1<<i))
                edge_word = struct.unpack_from('<H', payload, flags_at+2*i)[0]
                leaf(word, body, short, (low,high), edge_word)
                body += 4 if short else 12
            else:
                node(word)

    if root&3 == 0:
        offset = root&0x03fffffc
        leaf(struct.unpack_from('<I',payload,offset)[0],offset+4,False,None,struct.unpack_from('<H',payload,offset+16)[0])
    else:
        node(root)
    faces = [face for leaf in output for face in leaf['faces']]
    if [list(face) for face in faces] != chunk['faces'].tolist():
        raise ValueError('Original compact leaf face order differs from decoded source')
    return output


def install_quantized_cache(chunk, expected_wire_flags=None):
    leaves = decode_runtime_leaves(chunk)
    if expected_wire_flags is not None:
        actual = b''.join(struct.pack('<H', leaf['edge_flags']) for leaf in leaves).hex()
        if actual != expected_wire_flags:
            raise ValueError('Original compact edge flags differ from source wire leaf order')
    root_leaf = len(leaves) == 1 and leaves[0]['quantized_box'] is None
    boxes = None if root_leaf else np.asarray([leaf['quantized_box'] for leaf in leaves], dtype=np.float32)
    cursor, ids = 0, []
    for leaf in leaves:
        ids.append(list(range(cursor, cursor+len(leaf['faces']))))
        cursor += len(leaf['faces'])
    chunk['_quantized_slab_boxes'] = (boxes,ids)
    return leaves
