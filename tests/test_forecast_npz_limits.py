import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import numpy as np
from test_forecast import ROOT
from wt_atlas.npz_io import load_npz_bounded


class BoundedNpzTests(unittest.TestCase):
    def test_real_numeric_arrays_and_scalar_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'data.npz'
            np.savez_compressed(path,p=np.arange(24,dtype=np.float32).reshape(2,3,4),pitch=np.float64(.1))
            data=load_npz_bounded(path,('p','pitch'),4096)
            self.assertEqual(data['p'].shape,(2,3,4));self.assertEqual(float(data['pitch']),.1)

    def test_huge_claimed_shape_rejected_before_numpy_allocation(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'forged.npz';header=io.BytesIO()
            np.lib.format.write_array_header_1_0(header,{'descr':'<f4','fortran_order':False,'shape':(2**40,)})
            with zipfile.ZipFile(path,'w') as archive:archive.writestr('p.npy',header.getvalue())
            with patch('wt_atlas.npz_io.np.load',side_effect=AssertionError('must reject before allocation')):
                with self.assertRaisesRegex(ValueError,'declared shape'):load_npz_bounded(path,('p',),4096)

    def test_truncated_and_trailing_payloads_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'bad.npz';raw=io.BytesIO();np.save(raw,np.ones((2,3),np.float32))
            for payload in (raw.getvalue()[:-1],raw.getvalue()+b'extra'):
                with zipfile.ZipFile(path,'w') as archive:archive.writestr('p.npy',payload)
                with self.assertRaisesRegex(ValueError,'declared shape'):load_npz_bounded(path,('p',),4096)

    def test_object_arrays_and_expanded_budget_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'bad.npz';np.savez(path,p=np.array([{'not':'numeric'}],dtype=object))
            with self.assertRaisesRegex(ValueError,'object dtype'):load_npz_bounded(path,('p',),4096)
            np.savez_compressed(path,p=np.zeros(1024,np.float32))
            with self.assertRaisesRegex(ValueError,'budget'):load_npz_bounded(path,('p',),1024)

    def test_missing_and_duplicate_members_rejected(self):
        import warnings
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'bad.npz';np.savez(path,p=np.ones(1,np.float32))
            with self.assertRaisesRegex(ValueError,'Missing'):load_npz_bounded(path,('unknown',),4096)
            with warnings.catch_warnings():
                warnings.simplefilter('ignore',UserWarning)
                with zipfile.ZipFile(path,'a') as archive:archive.writestr('p.npy',b'duplicate')
            with self.assertRaisesRegex(ValueError,'duplicate'):load_npz_bounded(path,('p',),4096)
