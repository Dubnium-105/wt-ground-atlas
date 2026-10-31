from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from wt_atlas.forecast.version_source_audit import (UNIT_PREFIX,WEAPON_PREFIX,audit,source_tree,weapon_path,unit_path)


class VersionSourceAuditTests(unittest.TestCase):
    def setUp(self):
        self.old_sha='a'*40;self.new_sha='b'*40
        self.old={'commit':self.old_sha,'root_tree':'c'*40,'entries':{
            UNIT_PREFIX+'one.blkx':{'sha':'1'*40,'mode':'100644'},
            UNIT_PREFIX+'two.blkx':{'sha':'2'*40,'mode':'100644'},
            WEAPON_PREFIX+'gun.blkx':{'sha':'3'*40,'mode':'100644'},
            WEAPON_PREFIX+'alternate.blkx':{'sha':'4'*40,'mode':'100644'}}}
        self.new={'commit':self.new_sha,'root_tree':'d'*40,'entries':dict(self.old['entries'])}
        self.profile={'name':'round','provenance':{'datamine_commit':self.old_sha,
            'source_weapon_path':'gamedata/weapons/groundmodels_weapons/gun.blk',
            'penetration_source_ammo_ids':['one::AP']}}
        self.bindings={'one':{'identity':{'datamine_commit':self.old_sha}}}

    def test_unchanged_source_is_only_evidence_not_physics_promotion(self):
        result=audit(self.old,self.new,[self.profile],self.bindings)
        self.assertEqual(result['profile_counts'],{'unchanged_declared_blobs':1})
        self.assertEqual(result['target_counts'],{'unchanged':1})
        self.assertFalse(result['client_grp_equivalence_verified'])
        self.assertFalse(result['promoted_physics_version'])
        self.assertFalse(result['publishable'])

    def test_every_ownership_weapon_and_unit_is_checked(self):
        self.profile['provenance']['recovery_bindings']={'one::AP':{
            'weapon_path':'gamedata/weapons/groundmodels_weapons/alternate.blk'}}
        self.profile['provenance']['penetration_source_ammo_ids'].append('two::AP')
        self.new['entries'][WEAPON_PREFIX+'alternate.blkx']={'sha':'5'*40,'mode':'100644'}
        self.new['entries'][UNIT_PREFIX+'two.blkx']={'sha':'6'*40,'mode':'100644'}
        result=audit(self.old,self.new,[self.profile],self.bindings)
        self.assertEqual(result['profile_counts'],{'changed_source':1})
        self.assertEqual({r['path'] for r in result['profiles'][0]['changed_or_missing']},
            {WEAPON_PREFIX+'alternate.blkx',UNIT_PREFIX+'two.blkx'})

    def test_missing_paths_and_wrong_source_commit_remain_unverified(self):
        del self.new['entries'][WEAPON_PREFIX+'gun.blkx']
        self.assertEqual(audit(self.old,self.new,[self.profile],self.bindings)['profile_counts'],{'missing_source':1})
        self.profile['provenance']['datamine_commit']='f'*40
        self.bindings['one']['identity']['datamine_commit']='f'*40
        result=audit(self.old,self.new,[self.profile],self.bindings)
        self.assertEqual(result['profile_counts'],{'source_commit_mismatch':1})
        self.assertEqual(result['target_counts'],{'source_commit_mismatch':1})
        self.assertIsNone(weapon_path('gamedata/weapons/groundmodels_weapons/../escape.blk'))
        self.assertIsNone(unit_path('../escape'))

    def test_reads_actual_commit_objects_from_local_git_without_a_checkout(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            def git(*args):return subprocess.run(['git','-C',str(root),*args],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout.decode().strip()
            git('init','-q')
            file=root/UNIT_PREFIX/'one.blkx';file.parent.mkdir(parents=True);file.write_text('first',encoding='utf-8')
            git('add','.');git('-c','user.name=Fixture','-c','user.email=fixture@example.test','-c','commit.gpgsign=false','commit','-q','-m','first')
            first=git('rev-parse','HEAD');file.write_text('second',encoding='utf-8')
            git('add','.');git('-c','user.name=Fixture','-c','user.email=fixture@example.test','-c','commit.gpgsign=false','commit','-q','-m','second')
            second=git('rev-parse','HEAD');before=source_tree(root,first);after=source_tree(root,second)
            self.assertNotEqual(before['entries'][UNIT_PREFIX+'one.blkx'],after['entries'][UNIT_PREFIX+'one.blkx'])
            self.assertNotEqual(before['root_tree'],after['root_tree'])
            with self.assertRaisesRegex(ValueError,'Exact Git commit'):
                source_tree(root,'HEAD')


if __name__=='__main__':unittest.main()
