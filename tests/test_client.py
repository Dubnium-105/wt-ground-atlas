"""Client import regressions; synthetic numbers remain test fixtures."""
import copy
from pathlib import Path
import struct
import shutil
import tempfile
import unittest
import numpy as np
from wt_atlas.common import read_json, write_json
from wt_atlas.client import make_materials, scan_resources
from wt_atlas.geometry import build_cpu_layers
from wt_atlas.native_binding.grp_directory import parse_grp_directory
from wt_atlas.probability import export_atlas

ROOT = Path(__file__).resolve().parents[1]
CONFIG = read_json(ROOT/'config/pipeline.json')


def param(name, kind, value):
    return {'name': name, 'type': kind, 'value': value}


class ClientContracts(unittest.TestCase):
    def test_patch_overrides_resources_but_retains_unpatched_base_resources(self):
        vendor=ROOT/'backend/vendor/grp_probability_v2/examples/grp'
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for directory,name in [('content/base/res/tanks','ussr_t_72b'),('patch/content/base/res/tanks','germ_leopard_2a5'),('warthunder_yupcache','ussr_t_72b')]:
                path=root/directory/'same.grp';path.parent.mkdir(parents=True)
                shutil.copyfile(vendor/(name+'.grp'),path)
            resources,report=scan_resources(root)
            self.assertEqual(report['active_grp_files'],2)
            self.assertEqual(report['patch_overrides'],1)
            self.assertEqual(len(resources['t_72b_collision']),1)
            self.assertTrue(all('/patch/' in Path(p).as_posix() for name,hits in resources.items() if name!='t_72b_collision' for p in hits))
            shutil.copyfile(vendor/'ussr_t_72b.grp',root/'patch/content/base/res/tanks/same.grp')
            resources,report=scan_resources(root)
            self.assertEqual(report['collision_resource_overrides'],1)
            self.assertEqual(len(resources['t_72b_collision']),1)
            self.assertIn('/patch/',Path(resources['t_72b_collision'][0]).as_posix())

    def test_grp_name_ids_are_independent_of_resource_ordinals(self):
        data = bytearray(288)
        struct.pack_into('<4sIII', data, 0, b'GRP2', 240, 240, 272)
        struct.pack_into('<II', data, 16, 128, 3)
        struct.pack_into('<II', data, 32, 160, 2)
        struct.pack_into('<II', data, 48, 208, 0)
        position = 64
        for i, name in enumerate(['external', 'collision_a', 'collision_b']):
            value = name.encode()+b'\0'
            data[position:position+len(value)] = value
            struct.pack_into('<I', data, 128+i*4, position)
            position += len(value)
        struct.pack_into('<IIHH', data, 160, 0xACE50000, 256, 2, 0)
        struct.pack_into('<IIHH', data, 172, 0xACE50000, 272, 1, 0)
        directory = parse_grp_directory(bytes(data))
        self.assertEqual([e['name'] for e in directory['entries']], ['collision_b','collision_a'])
        self.assertEqual([e['end'] for e in directory['entries']], [272,288])

    def test_material_inheritance_and_shared_mesh_keep_configuration_values(self):
        plate = {'name':'steel','params':[param('hitTestType','t','armor'),param('armorQuality','r',.8)],'children':[]}
        context = {'classes':[plate], 'class_map':{'steel':plate}, 'defaults':{}, 'template_ids':{},
                   'mechanisms':{'names':['generic'],'parents':[None]}}
        tree = {'name':'DamageParts','params':[param('formatVersion','i',2),param('armorClass','t','steel')],
                'children':[{'name':'plate_dm','params':[param('armorThickness','r',20)],'children':[]}]}
        nodes=[{'index':0,'name':'plate_dm','behavior_flags':3},{'index':1,'name':'unbound','behavior_flags':3},
               {'index':2,'name':'ex_decor','behavior_flags':3}]
        first,_=make_materials(copy.deepcopy(tree),nodes,context)
        tree['children'][0]['params'][0]['value']=40
        second,_=make_materials(tree,nodes,context)
        self.assertEqual(first[0]['loaded_class']['armor']['armorThickness'],20)
        self.assertEqual(second[0]['loaded_class']['armor']['armorThickness'],40)
        self.assertEqual(first[0]['source_armor_classes'],['steel'])
        self.assertEqual(first[1]['part_id'],-1)
        self.assertGreaterEqual(first[2]['part_id'],0)
        self.assertEqual(first[2]['class_flags'],0)

    def test_compiled_raster_matches_reference_including_oblique_views(self):
        vendor=ROOT/'backend/vendor/grp_probability_v2'
        config=copy.deepcopy(CONFIG); config['compute'].update(angles_deg=[0,13,90],pixel_pitch_m=.1)
        with tempfile.TemporaryDirectory() as tmp:
            arrays=[]
            for compiled in [False,True]:
                config['compute']['compiled_raster']=compiled
                meta,_=build_cpu_layers(vendor/'examples/grp/ussr_t_72b.grp',vendor/'data/vehicles/ussr_t_72b.json',vendor,tmp,config)
                with np.load(meta.with_suffix('.npz')) as packet:
                    arrays.append({k:packet[k] for k in packet.files})
            for key in arrays[0]:
                np.testing.assert_array_equal(arrays[0][key],arrays[1][key],err_msg=key)

    def test_mixed_vehicle_grids_publish_their_own_metric_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'arrays').mkdir();(root/'provenance').mkdir();(root/'geometry_layers').mkdir()
            write_json(root/'provenance/plan.json',{'angles_deg':[0],'width':2,'height':1,'pixel_pitch_m':.1,'world_width_m':.2,'top_y_m':1,'bottom_y_m':.9})
            write_json(root/'provenance/ammunition.json',[{'name':'test','mode':'KE','distance_m':0}])
            write_json(root/'provenance/model_scope.json',{})
            write_json(root/'format.json',{'per_ammunition_arrays':{'status':{'values':CONFIG['status_legend']}}})
            write_json(root/'summary.json',{'client_version':'fixture','groups':[{'vehicle':v,'grp_sha256':v,'groups':[]} for v in ['small','large']]})
            for name,width,top in [('small',2,1.),('large',7,3.)]:
                shape=(1,1,width)
                np.savez(root/'arrays'/(name+'.test.npz'),penetration_probability=np.ones(shape,np.float32),status=np.full(shape,3,np.uint8),
                         material_response_flags=np.zeros(shape,np.uint8),angles_deg=[0],pixel_pitch_m=.1,world_width_m=width*.1,top_y_m=top,bottom_y_m=top-.1)
            export_atlas(root,root/'out',CONFIG)
            rows={r['target_id']:r for r in read_json(root/'out/index.json')['entries']}
            self.assertEqual(rows['large']['width'],7)
            self.assertEqual(rows['large']['top_y_m'],3.)
            self.assertAlmostEqual(rows['large']['world_width_m'],.7)
            self.assertEqual(rows['small']['top_y_m'],1.)


if __name__=='__main__':
    unittest.main()
