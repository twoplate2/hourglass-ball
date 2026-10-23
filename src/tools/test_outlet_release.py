"""Counterexamples for the strict 3.12 outlet evidence contract; no GL."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

import check_outlet_release as gate


def source_fixture():
    old={
        'main.py':b'class HourglassWidget:\n def __init__(self): return 0\n def _reset_run_state(self): return 0\n def redraw(self): return 0\n def update_particles(self): return 0\n',
        'src/app/short_grain_stream.py':b'def source_half_width(x): return 0\ndef particle_vertices(x): return 0\nFRAGMENT_BODY="old"\ndef install():\n def draw(self): return 0\n def build(self): return 0\n def ensure(self): return 0\n',
        'src/app/sand_flow_material.py':b'GRAIN_FRAGMENT_HEADER="old"\nFRAGMENT_SHADER="old"\nclass SandFlowContext:\n def __init__(self): self.x=0\n',
        'src/app/app_version.py':b'"version"\nAPP_VERSION="3.11"\n',
        'src/app/protected.py':b'value=1\n',
    }
    new={k:v.replace(b'return 0',b'return 1').replace(b'"old"',b'"new"').replace(b'self.x=0',b'self.x=1')
         for k,v in old.items()}
    # ensure is outside the reviewed nested build/draw and remains protected.
    new['src/app/short_grain_stream.py']=new['src/app/short_grain_stream.py'].replace(
        b'def ensure(self): return 1',b'def ensure(self): return 0')
    new['src/app/short_grain_stream.py']+=b'def sprite_aspect(x,y): return max(x,y)\n'
    new['main.py']=new['main.py'].replace(b'def __init__(self): return 1',b'def __init__(self): return 0')
    new['main.py']=new['main.py'].replace(b'def _reset_run_state(self): return 1',b'def _reset_run_state(self): return 0')
    new['src/app/app_version.py']=old['src/app/app_version.py'].replace(b'3.11',b'3.12')
    return old,new


class OutletSourceScopeTests(unittest.TestCase):
    def setUp(self):
        self.old,self.new=source_fixture()
        self.pin=patch.object(gate,'APPROVED_NODE_AST',gate.node_hashes(self.new))
        self.pin.start()

    def tearDown(self):
        self.pin.stop()

    def test_only_exact_reviewed_nodes_and_version_are_permitted(self):
        gate.require_source_scope(self.new,self.old)

    def test_missing_independent_pins_never_self_approve(self):
        with patch.object(gate,'APPROVED_NODE_AST',{}),self.assertRaisesRegex(ValueError,'independently pinned'):
            gate.require_source_scope(self.new,self.old)

    def test_unreviewed_rng_or_motion_change_inside_a_node_is_rejected(self):
        new=deepcopy(self.new)
        new['main.py']=new['main.py'].replace(b'def update_particles(self): return 1',
                                             b'def update_particles(self): return random.random()')
        with self.assertRaisesRegex(ValueError,'reviewed source node'):
            gate.require_source_scope(new,self.old)

    def test_other_install_logic_remains_protected(self):
        new=deepcopy(self.new)
        new['src/app/short_grain_stream.py']=new['src/app/short_grain_stream.py'].replace(
            b'def ensure(self): return 0',b'def ensure(self): return 9')
        with self.assertRaisesRegex(ValueError,'protected source outside'):
            gate.require_source_scope(new,self.old)

    def test_new_methods_and_other_runtime_modules_are_rejected(self):
        for name,extra in [('main.py',b'\n def new_function(self): return 7\n'),
                           ('src/app/protected.py',b'value=7\n')]:
            with self.subTest(name=name):
                new=deepcopy(self.new);new[name]+=extra
                with self.assertRaisesRegex(ValueError,'protected'):
                    gate.require_source_scope(new,self.old)

    def test_wrong_version_or_changes_elsewhere_in_version_module_are_rejected(self):
        for value in (b'APP_VERSION="3.13"\n',self.new['src/app/app_version.py']+b'extra=1\n'):
            with self.subTest(value=value):
                new=deepcopy(self.new);new['src/app/app_version.py']=value
                with self.assertRaises(ValueError):gate.require_source_scope(new,self.old)

    def test_added_or_missing_runtime_modules_are_rejected(self):
        for added in (True,False):
            new=deepcopy(self.new)
            if added:new['extra.py']=b''
            else:new.pop('src/app/protected.py')
            with self.subTest(added=added),self.assertRaisesRegex(ValueError,'inventory'):
                gate.require_source_scope(new,self.old)

    def test_adaptive_helper_and_build_are_exactly_pinned(self):
        for old,new in ((b'def sprite_aspect(x,y): return max(x,y)',b'def sprite_aspect(x,y): return 999'),
                        (b'def build(self): return 1',b'def build(self): return 999')):
            candidate=deepcopy(self.new)
            candidate['src/app/short_grain_stream.py']=candidate['src/app/short_grain_stream.py'].replace(old,new)
            with self.subTest(old=old),self.assertRaisesRegex(ValueError,'reviewed source node'):
                gate.require_source_scope(candidate,self.old)


class OutletEvidenceTests(unittest.TestCase):
    def test_member_hashes_reject_changed_extra_missing_or_empty_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);p=folder/'a.txt';p.write_bytes(b'original')
            members={'a.txt':gate.sha(p)}
            gate.check_members(folder,members)
            for value in ({},{'missing':members['a.txt']},{'a.txt':'0'*64}):
                with self.subTest(value=value),self.assertRaises(ValueError):gate.check_members(folder,value)
            (folder/'extra').write_bytes(b'new')
            with self.assertRaisesRegex(ValueError,'inventory'):gate.check_members(folder,members)

    def test_archive_links_traversal_and_duplicates_are_rejected(self):
        for mode in ('link','traversal','duplicate'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                folder=Path(tmp);archive=folder/'evidence.tar.xz'
                with tarfile.open(archive,'w:xz') as tar:
                    info=tarfile.TarInfo('../outside' if mode=='traversal' else 'file')
                    if mode=='link':
                        info.type=tarfile.SYMTYPE;info.linkname='other';tar.addfile(info)
                    else:
                        info.size=1;tar.addfile(info,io.BytesIO(b'x'))
                        if mode=='duplicate':tar.addfile(info,io.BytesIO(b'x'))
                with self.assertRaises(ValueError):gate.safe_extract(archive,folder/'unpack')

    def audit(self,folder,kind='visual'):
        file=folder/'frame.png';file.write_bytes(b'original evidence')
        return dict(schema=1,kind=kind,completed=True,status='passed',source_sha256='source',
                    reviewer='independent',open_blockers=[],normal_size_reviewed=True,
                    dynamic_reviewed=True,review={k:'passed' for k in gate.GOALS},findings=[],
                    scope='outlet joins and adjacent states',unchecked=['Android','audio','performance'],
                    approved_node_ast=gate.APPROVED_NODE_AST,
                    evidence=[dict(path='frame.png',sha256=gate.sha(file))])

    def test_visual_audit_requires_actual_dynamic_and_normal_size_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);audit=self.audit(folder)
            self.assertEqual(gate.require_audit(audit,'visual','source',folder),'independent')
            for key,value in (('dynamic_reviewed',False),('normal_size_reviewed',False),
                              ('source_sha256','other'),('completed',False),('evidence',[]),
                              ('open_blockers',['NS01']),('review',{'NS01':'passed'})):
                bad=deepcopy(audit);bad[key]=value
                with self.subTest(key=key),self.assertRaises(ValueError):
                    gate.require_audit(bad,'visual','source',folder)

    def test_serious_findings_and_changed_audit_evidence_cannot_be_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);audit=self.audit(folder)
            audit['findings']=[dict(severity='P1',status='open',issue='neck seam')]
            with self.assertRaisesRegex(ValueError,'Serious'):gate.require_audit(audit,'visual','source',folder)
            audit['findings']=[];(folder/'frame.png').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'evidence changed'):gate.require_audit(audit,'visual','source',folder)

    def test_source_audit_cannot_define_new_allowed_node_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);audit=self.audit(folder,'source')
            audit['approved_node_ast']={'allow_everything':'0'*64}
            with self.assertRaisesRegex(ValueError,'pinned nodes'):gate.require_audit(audit,'source','source',folder)

    def test_test_reports_are_checked_against_original_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);runs=[]
            for name,count in gate.PURE_TESTS.items():
                file=folder/(name+'.log');file.write_text('Ran %d tests in 0.1s\n\nOK\n'%count,encoding='utf-8')
                runs.append(dict(script='src/tools/'+name,count=count,exit_code=0,
                                 log=file.name,sha256=gate.sha(file)))
            value=dict(schema=1,completed=True,source_sha256='source',visual_verdict=None,runs=runs)
            report=folder/'tests.json'
            def write():report.write_text(json.dumps(value),encoding='utf-8')
            write();self.assertEqual(gate.require_tests(report,'source'),85)
            value['runs'][0]['count']+=1;write()
            with self.assertRaisesRegex(ValueError,'count differs'):gate.require_tests(report,'source')
            value['runs'][0]['count']-=1;value['visual_verdict']='passed';write()
            with self.assertRaisesRegex(ValueError,'claim visual'):gate.require_tests(report,'source')
            value['visual_verdict']=None;value['runs'][0]['exit_code']=1;write()
            with self.assertRaisesRegex(ValueError,'successfully'):gate.require_tests(report,'source')

    def test_matrix_contract_retains_early_tail_colours_and_pause_resume(self):
        cases=gate.expected_cases()
        self.assertEqual(len(cases),49)
        self.assertEqual(sum(len(c['frame_offsets']) for c in cases),53)
        self.assertEqual(len([c for c in cases if c['name'].startswith('c')]),12)
        self.assertEqual({c['period'] for c in cases},{1.,5.,50.})
        self.assertIn('p50-t49.9',{c['name'] for c in cases})
        self.assertEqual(next(c for c in cases if c['name']=='paused')['state'],'paused')

    def test_312_dispatch_uses_new_contract_without_changing_old_rules(self):
        import check_flow_release as old
        with patch.object(old,'current_version',return_value='3.12'),\
             patch.object(gate,'validate',return_value={'contract':'outlet'}) as checked:
            self.assertEqual(old.validate('review.json'),{'contract':'outlet'})
            checked.assert_called_once_with('review.json')
        self.assertEqual(old.BASELINE_VERSION,'3.10')
        self.assertEqual(old.BASELINE_COMMIT,'c869e24ea54732cddaed22b1ead18989b288e33b')

    def shader_fixture(self,folder):
        hidden=dict(IsWindow=True,IsWindowVisible=False,IsIconic=False,window_state_config='hidden')
        geometry={'_cx':270.,'_neck_y':500.,'_taper':{'y_bot':520.,'t_in':20.}}
        base=np.full((924,540,3),120,dtype=np.uint8)
        base[:,::2]=140
        images={name:base.copy() for name in ('current','original-shader','helper-neck',
                'no-flow-current','no-flow-original-shader','no-flow-missing-guard-negative')}
        images['original-shader'][450,270]=180
        images['no-flow-missing-guard-negative'][450,271]=180
        from verify_neck_stream_join import remove_join_block
        import ast
        stream_source=(gate.ROOT/'src/app/short_grain_stream.py').read_bytes()
        body=ast.literal_eval(gate.selected_node(ast.parse(stream_source),'FRAGMENT_BODY').value)
        value=dict(completed=True,passed=True,source_sha256='source',ablation_mode='same-current-join-off',
                   ablation_body_sha256=hashlib.sha256(remove_join_block(body).encode()).hexdigest(),
                   tool_sha256=hashlib.sha256((gate.ROOT/'src/tools/verify_neck_stream_join.py')
                       .read_bytes().replace(b'\r\n',b'\n')).hexdigest(),
                   source_files={'main.py':'raw'},source_files_end={'main.py':'raw'},
                   dimensions=[540,924],hidden_at_creation=hidden,hidden_at_finish=hidden,
                   physics={'sha256':'same'},allowed_diff_roi=[248,443,292,475],
                   neck_comparison_roi=[260,405,280,444],assertions=[{'passed':True}],frames=[])
        for name,array in images.items():
            file=folder/(name+'.png');Image.fromarray(array).save(file)
            value['frames'].append(dict(name=name,file=file.name,sha256=gate.sha(file),hidden=hidden,
                physics_before='same',physics_after='same',shaders={'all_present_shaders_compiled':True},
                short_join_enabled=0. if name.startswith('no-flow-') else 1.))
        path=folder/'proof.json';path.write_text(json.dumps(value),encoding='utf-8')
        args=({'main.py':'canonical','buildozer.spec':'canonical-spec'},
              {'main.py':'raw','buildozer.spec':'raw-spec'},'source',geometry,stream_source)
        return path,value,images,args

    def test_shader_proof_is_rechecked_from_actual_pixels(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);path,proof,images,args=self.shader_fixture(folder)
            self.assertEqual(gate.require_shader_proof(path,*args)['no_flow_changed_pixels'],0)
            for target,xy,message in (
                ('original-shader',(10,10),'only actual outlet'),
                ('helper-neck',(410,270),'actual neck pixels'),
                ('no-flow-original-shader',(450,270),'Inactive flow path')):
                with self.subTest(target=target):
                    image=images[target].copy();image[xy]=200
                    file=folder/(target+'.png');Image.fromarray(image).save(file)
                    changed=deepcopy(proof)
                    next(f for f in changed['frames'] if f['name']==target)['sha256']=gate.sha(file)
                    path.write_text(json.dumps(changed),encoding='utf-8')
                    with self.assertRaisesRegex(ValueError,message):gate.require_shader_proof(path,*args)
                    Image.fromarray(images[target]).save(file)

    def test_self_reported_shader_pass_or_broad_mask_cannot_hide_a_bad_negative(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);path,proof,images,args=self.shader_fixture(folder)
            broad=deepcopy(proof);broad['allowed_diff_roi']=[0,0,540,924]
            path.write_text(json.dumps(broad),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'expanded or moved'):gate.require_shader_proof(path,*args)
            negative='no-flow-missing-guard-negative'
            file=folder/(negative+'.png');Image.fromarray(images['no-flow-current']).save(file)
            next(f for f in proof['frames'] if f['name']==negative)['sha256']=gate.sha(file)
            path.write_text(json.dumps(proof),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'only actual outlet'):gate.require_shader_proof(path,*args)

    def width_fixture(self,folder):
        from measure_stream_width import SCHEMA
        keys=('motion_p5','motion_p50','baseline_motion_p5','baseline_motion_p50')
        metrics={};motions={};evidence={'width':'width-proof.json'};runs={}
        for key in keys:
            ratio=.85 if key=='baseline_motion_p50' else .95 if key=='baseline_motion_p5' else 1.
            rows=[dict(valid_body=True,nearfield_complete=True,neck_width={'p50':40.},
                       near_width={'p50':40.*ratio}) for _ in range(61)]
            value=dict(schema=SCHEMA,completed=True,source_files={'main.py':'a'*64},frames=rows,
                       body_source='isolated_stream_gl',plain=False,
                       nearfield={'valid_frames':61,'ratio':{'p50':ratio}},
                       waist={'diagnostic_only':True})
            metrics[key]=value;motions[key]={'source_files':value['source_files']}
            evidence[key]=key+'/motion.json'
            file=folder/(key+'-metrics.json');file.write_text(json.dumps(value),encoding='utf-8')
            runs[key]=dict(motion=evidence[key],metrics=file.name,sha256=gate.sha(file))
        proof=dict(schema=1,completed=True,source_sha256='source',min_ratio=.92,pixel_slack=0,runs=runs)
        (folder/'width-proof.json').write_text(json.dumps(proof),encoding='utf-8')
        return metrics,motions,evidence,proof

    def test_width_gate_recomputes_saved_metrics_and_requires_a_real_old_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);metrics,motions,evidence,proof=self.width_fixture(folder)
            def read_motion(path):return Path(path).parent.name
            with patch.object(gate,'source_digest',return_value='source'),\
                 patch('measure_stream_width.read_motion',side_effect=read_motion) as loaded,\
                 patch('measure_stream_width.measure_report',side_effect=lambda key:metrics[key]) as recomputed:
                stats=gate.require_width(folder,evidence,motions)
                self.assertEqual(loaded.call_count,4);self.assertEqual(recomputed.call_count,4)
                self.assertFalse(stats['baseline_motion_p5']['negative_rejected'])
                self.assertTrue(stats['baseline_motion_p50']['negative_rejected'])
                self.assertFalse(stats['motion_p50']['negative_rejected'])
                # Editing both saved JSON and its hash still cannot rewrite
                # what the original-image measurement actually returned.
                file=folder/proof['runs']['motion_p5']['metrics']
                saved=json.loads(file.read_text(encoding='utf-8'));saved['nearfield']['ratio']['p50']=1.01
                file.write_text(json.dumps(saved),encoding='utf-8')
                proof['runs']['motion_p5']['sha256']=gate.sha(file)
                (folder/'width-proof.json').write_text(json.dumps(proof),encoding='utf-8')
                with self.assertRaisesRegex(ValueError,'actual PNG measurements'):
                    gate.require_width(folder,evidence,motions)

    def test_width_rule_cannot_be_relaxed_or_old_success_called_a_negative(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);metrics,motions,evidence,proof=self.width_fixture(folder)
            with patch.object(gate,'source_digest',return_value='source'):
                changed=deepcopy(proof);changed['min_ratio']=.8
                (folder/'width-proof.json').write_text(json.dumps(changed),encoding='utf-8')
                with self.assertRaisesRegex(ValueError,'predeclared'):
                    gate.require_width(folder,evidence,motions)
            for key in ('baseline_motion_p50',):
                metrics[key]=deepcopy(metrics['motion_p5'])
                file=folder/proof['runs'][key]['metrics'];file.write_text(json.dumps(metrics[key]),encoding='utf-8')
                proof['runs'][key]['sha256']=gate.sha(file)
            (folder/'width-proof.json').write_text(json.dumps(proof),encoding='utf-8')
            with patch.object(gate,'source_digest',return_value='source'),\
                 patch('measure_stream_width.read_motion',side_effect=lambda path:Path(path).parent.name),\
                 patch('measure_stream_width.measure_report',side_effect=lambda key:metrics[key]):
                with self.assertRaisesRegex(ValueError,'negative was not rejected'):
                    gate.require_width(folder,evidence,motions)

    def test_five_second_reference_is_nonregression_even_when_both_pass_point92(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);metrics,motions,evidence,proof=self.width_fixture(folder)
            candidate=metrics['motion_p5'];candidate['nearfield']['ratio']['p50']=.94
            for frame in candidate['frames']:frame['near_width']['p50']=40*.94
            file=folder/proof['runs']['motion_p5']['metrics']
            file.write_text(json.dumps(candidate),encoding='utf-8')
            proof['runs']['motion_p5']['sha256']=gate.sha(file)
            (folder/'width-proof.json').write_text(json.dumps(proof),encoding='utf-8')
            with patch.object(gate,'source_digest',return_value='source'),\
                 patch('measure_stream_width.read_motion',side_effect=lambda path:Path(path).parent.name),\
                 patch('measure_stream_width.measure_report',side_effect=lambda key:metrics[key]):
                with self.assertRaisesRegex(ValueError,'5-second.*regressed'):
                    gate.require_width(folder,evidence,motions)

    def test_default_visual_evidence_cannot_cover_finest_black(self):
        movies={'video-p5/audit.mp4','video-p50/audit.mp4','video-fine-black/audit.mp4'}
        matrix={'matrix/p50-t8-f000.png'};fine={'motion-fine-black/0000.png'}
        paths={'video-p5/audit.mp4','video-p50/audit.mp4','matrix/p50-t8-f000.png'}
        def audit():return {'evidence':[{'path':p} for p in paths]}
        with self.assertRaisesRegex(ValueError,'default/fine actual videos'):
            gate.require_visual_coverage(audit(),movies,matrix,fine)
        paths.add('video-fine-black/audit.mp4')
        with self.assertRaisesRegex(ValueError,'finest-black frame'):
            gate.require_visual_coverage(audit(),movies,matrix,fine)
        paths.add('motion-fine-black/0000.png')
        gate.require_visual_coverage(audit(),movies,matrix,fine)
        self.assertTrue({'motion_fine_black','video_fine_black'}<=gate.EVIDENCE_KEYS)

    def test_fine_motion_cannot_substitute_default_grain_colour_or_normal_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);hidden=dict(IsWindow=True,IsWindowVisible=False,IsIconic=False,window_state_config='hidden')
            raw={'main.py':'a'*64,'buildozer.spec':'b'*64};manifest={'main.py':'c'*64,'buildozer.spec':'d'*64}
            value=dict(version=gate.VERSION,period=50,grain=1,color=5,completed=True,renderer='particle_sprite_pool',
                       source_files={'main.py':'a'*64},source_files_end={'main.py':'a'*64},
                       sample_rate=60,dimensions=[8,8],continuity={'passed':True},
                       hidden_at_creation=hidden,hidden_at_finish=hidden,frames=[])
            for i in range(61):
                normal=np.full((8,8,3),120,dtype=np.uint8);normal[:,::2]=140;normal[0,0]=i+10
                stream=normal.copy();stream[1,1]=i+20
                file=folder/('%04d.png'%i);mask=folder/('%04d-stream.png'%i)
                Image.fromarray(normal).save(file);Image.fromarray(stream).save(mask)
                value['frames'].append(dict(time=8+i/60,file=file.name,sha256=gate.sha(file),native_hidden=hidden,
                    stream_file=mask.name,stream_sha256=gate.sha(mask),draw_state={'particles':1,'rendered_particles':1}))
            path=folder/'motion.json'
            def write(v):path.write_text(json.dumps(v),encoding='utf-8')
            write(value);gate.require_motion(path,50,manifest,raw,gate.VERSION,grain=1,color=5,isolated=True)
            for change,message in ((lambda v:v.update(grain=3),'configuration'),
                                   (lambda v:v.update(color=0),'configuration'),
                                   (lambda v:v['frames'][0].update(stream_file=v['frames'][0]['file']),'independent isolated'),
                                   (lambda v:v['frames'][0].update(stream_sha256='0'*64),'frame bytes changed')):
                bad=deepcopy(value);change(bad);write(bad)
                with self.subTest(message=message),self.assertRaisesRegex(ValueError,message):
                    gate.require_motion(path,50,manifest,raw,gate.VERSION,grain=1,color=5,isolated=True)


if __name__=='__main__':unittest.main()
