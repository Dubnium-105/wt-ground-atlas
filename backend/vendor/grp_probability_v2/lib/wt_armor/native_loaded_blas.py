"""Read immutable runtime BLAS bytes emitted by the pinned client's loader.

The surrounding resource remains a declared layout fixture.  This adapter
does not initialize a live vehicle or claim full resource-loader parity.
"""
from pathlib import Path
import gzip
import hashlib
import json
import struct


def sha(data):
    return hashlib.sha256(data).hexdigest()


class LoadedChunks:
    def __init__(self, metadata):
        self.path = Path(metadata)
        self.metadata = json.loads(self.path.read_text(encoding='utf8'))
        m = self.metadata
        if not (m['passed'] and m['all_source_nonempty_nodes_loaded']
                and m['whole_original_node_blas_loader_executed']
                and m['all_source_wire_flags_equal_one']
                and m['all_native_runtime_flags_equal_one']):
            raise ValueError('Incomplete original BLAS load')
        cases = self.path.with_name(m['case_data_file'])
        payload = self.path.with_name(m['runtime_blob_file'])
        if sha(cases.read_bytes()) != m['case_data_sha256']:
            raise ValueError('Original BLAS load cases changed')
        self.blob = payload.read_bytes()
        if sha(self.blob) != m['runtime_blob_sha256']:
            raise ValueError('Original BLAS runtime blob changed')
        with gzip.open(cases, 'rt', encoding='utf8') as file:
            rows = [json.loads(line) for line in file]
        if len(rows) != m['case_count']:
            raise ValueError('Original BLAS load row count differs')
        self.rows = {(r['vehicle'], r['input']['node']): r for r in rows}
        if len(self.rows) != len(rows):
            raise ValueError('Duplicate original BLAS node')

    def apply(self, vehicle, chunks):
        output = []
        seen = 0
        for chunk in chunks:
            value = dict(chunk)
            if len(chunk['faces']):
                row = self.rows[(vehicle, chunk['node_index'])]
                a, n = row['runtime_blob_offset'], row['runtime_blob_bytes']
                data = self.blob[a:a+n]
                if (not row['passed'] or len(data) != n
                        or sha(data) != row['runtime_blob_sha256']
                        or row['input']['triangle_count'] != len(chunk['faces'])
                        or row['input']['vertex_count'] != len(chunk['vertices'])
                        or struct.unpack_from('<I', data, 44)[0] != 1):
                    raise ValueError('Original runtime BLAS identity differs')
                value['prepared_bytes'] = data
                value['original_loader_row'] = row['case']
                seen += 1
            output.append(value)
        if seen != sum(key[0] == vehicle for key in self.rows):
            raise ValueError('Incomplete original runtime BLAS vehicle coverage')
        return output
