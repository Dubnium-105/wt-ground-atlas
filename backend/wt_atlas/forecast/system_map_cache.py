"""Bounded, source-revalidating map cache for offline system research."""
from collections import OrderedDict
from pathlib import Path
import numpy as np

from ..common import file_hash
from .data import safe_file


class SystemMapCache:
    """Cache decoded arrays without weakening Atlas checksum validation."""
    def __init__(self, atlas, budget_bytes):
        if type(budget_bytes) is not int or budget_bytes<1:raise ValueError('Invalid system map cache budget')
        self.atlas=atlas;self.budget=budget_bytes;self.cached_bytes=0;self.cache=OrderedDict()
        self.metadata_stamps={};self.hits=0

    def __getattr__(self,name):return getattr(self.atlas,name)

    def validate(self,map_id):
        self.atlas.verify(map_id)
        metadata=self.atlas.meta[map_id];path=safe_file(self.atlas.root,'arrays/'+map_id+'.json')
        stat=path.stat();stamp=(stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)
        if self.metadata_stamps.get(map_id)!=stamp:
            if file_hash(path)!=metadata['metadata_sha256']:raise ValueError('System map metadata checksum failure')
            self.metadata_stamps[map_id]=stamp

    def load(self,map_id):
        self.validate(map_id)
        if map_id in self.cache:
            self.cache.move_to_end(map_id);self.hits+=1;return self.cache[map_id][0]
        arrays=self.atlas.load(map_id)
        # Atlas normally retains its last map; this cache owns retention instead.
        self.atlas._loaded_id=None;self.atlas._loaded=None
        size=sum(value.nbytes for value in arrays.values() if isinstance(value,np.ndarray))
        for value in arrays.values():
            if isinstance(value,np.ndarray):value.setflags(write=False)
        if size<=self.budget:
            while self.cache and self.cached_bytes+size>self.budget:
                _,(_,old_size)=self.cache.popitem(last=False);self.cached_bytes-=old_size
            self.cache[map_id]=(arrays,size);self.cached_bytes+=size
        return arrays
