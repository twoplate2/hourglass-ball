"""3.12 outlet release: fixed source scope and source-bound PC visual evidence.

The old 3.11 contract remains in check_flow_release. Numerical checks below do
not replace the independent normal-size and dynamic visual review.
"""
import ast
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import tempfile

import numpy as np
from PIL import Image

from check_motion_shape_release import digest_text, relative_file, require_hidden, safe_extract, sha
from check_short_grain_release import require_capture
from material_visual_probe import normalize_cases
from release_decision import ROOT, check_current, current_version, runtime_manifest, source_digest

VERSION = '3.12'
BASELINE_VERSION = '3.11'
BASELINE_COMMIT = '139040bebf540da9a0905e001c0ef4d23fa1fb89'
GOALS = ('NS01','NS03','N03','UN01','S03','S04','S05','SL01','states')
# Independently confirmed by /root/neck_seam_diagnosis after source freeze.
# Review JSON cannot add or redefine allowed nodes; these are scope guards,
# not appearance or motion acceptance.
APPROVED_NODE_AST = {
    'main.py:HourglassWidget.redraw': '8a276b587258f7af3e6bfddcac914e25071e0e5d180282731d8db5ada721a02d',
    'main.py:HourglassWidget.update_particles': '5d4c727812f81ec8a213f039d32910e29dd621c0c49532072464a1416f181ce6',
    'src/app/short_grain_stream.py:source_half_width': '6a8e5c9e62da637c49ed859289217607d927ca2853accfd5dc0329274412efe5',
    'src/app/short_grain_stream.py:particle_vertices': '365eae6356cd8d40e71c0c01a5b760b832c057a226b71c2db52145134922eaba',
    'src/app/short_grain_stream.py:FRAGMENT_BODY': '52237bab4e66b8e95261cdb242d38754ac3492998e009ba01d0f279686f176a5',
    'src/app/short_grain_stream.py:install.build': '0f1e9868df7169ddd2e86f90c796ed096b4bccc90ac4707639332a5109c16726',
    'src/app/short_grain_stream.py:install.draw': 'a027dfd48d3eda5986ae93fa916aab26cf8f0fd9361a2a61f8cde5b8aac79b40',
    'src/app/short_grain_stream.py:sprite_aspect': 'ccb149e27ac8bfcb958589441d79a2476155ab4c5699a36e150513d6e858b6fd',
    'src/app/sand_flow_material.py:GRAIN_FRAGMENT_HEADER': '60d23f0a260fe9577c42e11a3fa6dcf2c501a5f6f8fb8ec2b3eaefdbd3d5b939',
    'src/app/sand_flow_material.py:FRAGMENT_SHADER': '332a6867ce85d954e54a68ce48845ddb7539eca495fc9dc03ae5d701225a92c0',
    'src/app/sand_flow_material.py:SandFlowContext.__init__': '0716b830fb3eee83d5792308be418f5451d8a84385a4f29f601772f773d9eef9',
}
SOURCE_NODES = {
    'main.py': ('HourglassWidget.redraw','HourglassWidget.update_particles'),
    'src/app/short_grain_stream.py': ('source_half_width','particle_vertices','FRAGMENT_BODY','install.build','install.draw'),
    'src/app/sand_flow_material.py': ('GRAIN_FRAGMENT_HEADER','FRAGMENT_SHADER','SandFlowContext.__init__'),
}
ADDED_SOURCE_NODES = {'src/app/short_grain_stream.py': ('sprite_aspect',)}
PURE_TESTS = {'test_neck_stream_seam.py':7,'test_particle_continuity.py':20,
              'test_particle_material.py':4,'test_stream_aperture.py':4,
              'test_splash_colours.py':10,'test_splash33_impulse.py':5,
              'test_short_grain.py':10,'test_neck_geometry.py':7,'test_release_version.py':5,
              'test_stream_width.py':13}
TOOLS = {'src/tools/'+name for name in PURE_TESTS} | {
    'src/tools/check_outlet_release.py','src/tools/test_outlet_release.py',
    'src/tools/check_flow_release.py','src/tools/release_decision.py',
    'src/tools/check_motion_shape_release.py','src/tools/check_short_grain_release.py',
    'src/tools/material_visual_probe.py','src/tools/verify_short_grain_runtime.py',
    'src/tools/capture_stream_motion.py','src/tools/record_flow_review.py',
    'src/tools/verify_neck_stream_join.py','src/tools/measure_neck_stream_seam.py',
    'src/tools/measure_stream_width.py','src/tools/test_stream_width.py'}
EVIDENCE_KEYS = {'matrix','motion_p5','motion_p50','baseline_motion_p5',
                 'baseline_motion_p50','width','video_p5','video_p50','shader','tests',
                 'motion_fine_black','video_fine_black'}


def read(path):
    value=json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(value,dict):raise ValueError('Expected JSON object: '+str(path))
    return value


def require(condition,message):
    if not condition:raise ValueError(message)


def ast_sha(node):
    return hashlib.sha256(ast.dump(node).encode('utf-8')).hexdigest()


def selected_node(tree,selector):
    parent=tree
    for part in selector.split('.'):
        nodes=[n for n in parent.body if
               isinstance(n,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==part
               or isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id==part for t in n.targets)]
        require(len(nodes)==1,'Expected one reviewed node: '+selector)
        parent,node=nodes[0],nodes[0]
    return node


def node_hashes(sources):
    return {name+':'+selector:ast_sha(selected_node(ast.parse(sources[name]),selector))
            for mapping in (SOURCE_NODES,ADDED_SOURCE_NODES)
            for name,selectors in mapping.items() for selector in selectors}


def require_source_scope(sources,baseline):
    require(set(sources)==set(baseline),'Runtime inventory changed')
    required={name+':'+selector for mapping in (SOURCE_NODES,ADDED_SOURCE_NODES)
              for name,selectors in mapping.items() for selector in selectors}
    require(set(APPROVED_NODE_AST)==required and all(digest_text(h) for h in APPROVED_NODE_AST.values()),
            'Final source nodes have not been independently pinned')
    for name,original in baseline.items():
        candidate=sources[name]
        if name=='src/app/app_version.py':
            current,old=ast.parse(candidate),ast.parse(original)
            node=selected_node(current,'APP_VERSION');prior=selected_node(old,'APP_VERSION')
            require(ast.literal_eval(node.value)==VERSION,'Wrong outlet version')
            current.body[current.body.index(node)]=copy.deepcopy(prior)
            require(ast.dump(current)==ast.dump(old),'Version module changed outside APP_VERSION')
        elif name in SOURCE_NODES:
            current,old=ast.parse(candidate),ast.parse(original)
            for selector in ADDED_SOURCE_NODES.get(name,()):
                node=selected_node(current,selector)
                require(ast_sha(node)==APPROVED_NODE_AST[name+':'+selector],
                        'Changed reviewed source node: '+name+':'+selector)
                require(not any(isinstance(n,(ast.FunctionDef,ast.ClassDef))
                                and n.name==selector for n in old.body),
                        'New source helper unexpectedly exists in baseline')
                current.body.remove(node)
            for selector in SOURCE_NODES[name]:
                node=selected_node(current,selector);prior=selected_node(old,selector)
                require(ast_sha(node)==APPROVED_NODE_AST[name+':'+selector],
                        'Changed reviewed source node: '+name+':'+selector)
                require(ast.dump(node)!=ast.dump(prior),'Missing declared outlet change: '+selector)
                parent=current
                for part in selector.split('.')[:-1]:parent=selected_node(parent,part)
                parent.body[parent.body.index(node)]=copy.deepcopy(prior)
            require(ast.dump(current)==ast.dump(old),'Changed protected source outside outlet scope: '+name)
        else:
            require(candidate.replace(b'\r\n',b'\n')==original.replace(b'\r\n',b'\n'),
                    'Changed protected runtime: '+name)


def baseline_sources():
    names=subprocess.check_output(['git','ls-tree','-r','--name-only',BASELINE_COMMIT,
        '--','src/app'],cwd=ROOT,text=True).splitlines()
    names=['main.py','buildozer.spec']+[n for n in names if n.endswith('.py') and n.count('/')==2]
    return {name:subprocess.check_output(['git','show',BASELINE_COMMIT+':'+name],cwd=ROOT) for name in names}


def check_members(folder,members):
    require(isinstance(members,dict) and bool(members),'Missing per-member archive hashes')
    actual={p.relative_to(folder).as_posix() for p in Path(folder).rglob('*') if p.is_file()}
    require(actual==set(members),'Archive member inventory differs')
    for name,digest in members.items():
        path=relative_file(folder,name)
        require(digest_text(digest) and sha(path)==digest,'Archive member changed: '+name)


def require_identity(report,manifest,raw_manifest,version):
    require(report.get('version')==version,'Captured version differs')
    expected={k:v for k,v in raw_manifest.items() if k!='buildozer.spec'}
    require(report.get('source_files')==expected==report.get('source_files_end'),
            'Capture did not run the exact archived source bytes')
    require(set(expected)==set(manifest)-{'buildozer.spec'},'Captured source inventory differs')


def expected_cases():
    cases=[]
    for period,times in ((50,(0,.05,.1,.2,.3,.5,1,5,8,25,45,49,49.9,50,51,56)),
                         (5,(0,.05,.1,.25,.5,1,2.5,4.5,4.9,5,6.5)),
                         (1,(0,.05,.1,.25,.5,.95,1,2.5))):
        for t in times:
            cases.append(dict(name='p%s-t%s'%(period,format(t,'g')),period=period,elapsed=t,
                              state='initial' if t==0 else 'running',frame_offsets=[0]))
    cases.extend(dict(name='c%d-g%d'%(c,g),period=50,elapsed=8,color=c,grain=str(g),frame_offsets=[0])
                 for c in range(6) for g in (1,6))
    cases.extend([dict(name='paused',period=50,elapsed=8,state='paused',pause_seconds=.5,
                       frame_offsets=[0,.2,.5]),
                  dict(name='resume',period=50,elapsed=8,pause_seconds=.5,
                       frame_offsets=[0,1/60,2/60])])
    return normalize_cases({'cases':cases})


def require_frame(path,frame,dimensions):
    file=relative_file(path.parent,frame['file'])
    require(sha(file)==frame['sha256'],'Original frame bytes changed: '+str(file))
    with Image.open(file) as im:
        require(list(im.size)==dimensions,'Original frame dimensions changed')
        rgb=np.asarray(im.convert('RGB'))
        require(any(np.ptp(rgb[:,:,channel])>0 for channel in range(3)),'Original frame is blank')
    return file


def require_matrix(path,manifest,raw_manifest):
    report=read(path);require_capture(report,53,'particle_sprite_pool')
    require_identity(report,manifest,raw_manifest,VERSION)
    require(report.get('dimensions')==[540,924],'Matrix must include normal PC dimensions')
    require(report.get('normalized_cases')==expected_cases(),'Missing or changed adjacent-state matrix')
    expected_names={case['name']+'-f%03d'%i for case in expected_cases()
                    for i in range(len(case['frame_offsets']))}
    require({f['name'] for f in report['frames']}==expected_names,'Matrix frame names differ')
    for frame in report['frames']:
        require_frame(path,frame,report['dimensions'])
        require(frame.get('draw_preserved_physics') is True
                and frame.get('stream_draw_preserved_physics') is True,
                'Drawing mutated particle state')
        require(abs(sum(frame['transfer'].values())-1)<1e-8,'Sand mass is not conserved')
        require(frame['shaders'].get('all_present_shaders_compiled') is True,'Matrix shader failure')
    paused=[f for f in report['frames'] if f['case']=='paused']
    require(len(paused)==3 and len({f['sha256'] for f in paused})==1,'Paused actual pixels differ')
    return dict(frames=len(report['frames']),cases=len(report['cases']))


def require_motion(path,period,manifest,raw_manifest,version,grain=3,color=0,isolated=False):
    report=read(path)
    require(report.get('completed') is True,'Motion capture is incomplete')
    require('benchmark' not in report,'This appearance release does not collect performance measurements')
    require_identity(report,manifest,raw_manifest,version)
    require(report.get('period')==period and report.get('grain')==grain and report.get('color',0)==color
            and report.get('renderer')=='particle_sprite_pool','Wrong motion configuration')
    rows=report.get('frames',[])
    require(len(rows)==61 and report.get('sample_rate')==60
            and abs(rows[-1]['time']-rows[0]['time']-1)<1e-8,'Expected one second of 60 Hz lossless frames')
    expected_start=2.5 if period==5 else 8.
    require(abs(rows[0]['time']-expected_start)<1e-8,'Wrong stable motion start')
    require(report.get('continuity',{}).get('passed') is True,'Missing actual particle continuity trace')
    require(len({f.get('file') for f in rows})==61 and len({f.get('sha256') for f in rows})>1,
            'Motion sequence repeats files or frozen rendered pixels')
    if isolated:
        stream_names={f.get('stream_file') for f in rows}
        require(len(stream_names)==61 and None not in stream_names
                and not (stream_names & {f['file'] for f in rows}),
                'Fine-grain sequence lacks independent isolated GL image paths')
        require(len({f.get('stream_sha256') for f in rows})>1,'Fine-grain isolated GL sequence is frozen')
    for i,frame in enumerate(rows):
        require(abs(frame['time']-(expected_start+i/60))<1e-8,'Nonuniform logical motion timestamps')
        require_hidden(frame['native_hidden'])
        require_frame(path,frame,report['dimensions'])
        if isolated:
            require('stream_file' in frame and 'stream_sha256' in frame,
                    'Fine-grain stream has no actual isolated GL image')
            require_frame(path,dict(file=frame['stream_file'],sha256=frame['stream_sha256']),report['dimensions'])
        draw=frame.get('draw_state',{})
        require(draw.get('particles',0)>0 and draw.get('particles')==draw.get('rendered_particles'),
                'Missing live particle sprites')
    require_hidden(report.get('hidden_at_creation',{}));require_hidden(report.get('hidden_at_finish',{}))
    return report


def require_audit(audit,kind,digest,folder):
    require(audit.get('schema')==1 and audit.get('kind')==kind and audit.get('completed') is True
            and audit.get('status')=='passed' and audit.get('source_sha256')==digest,
            'Incomplete or wrong-source independent '+kind+' audit')
    require(isinstance(audit.get('reviewer'),str) and bool(audit['reviewer'])
            and audit.get('open_blockers')==[],'Independent audit has unresolved blockers')
    require(isinstance(audit.get('unchecked'),list) and bool(audit.get('scope')),
            'Independent audit must state its scope and unchecked items')
    require(all(f.get('severity') in ('P0','P1','P2','P3') for f in audit.get('findings',[])),
            'Audit finding has no recognized severity')
    require(all(f.get('status')=='closed' for f in audit.get('findings',[])
                if f.get('severity') in ('P0','P1')),'Serious audit finding is unresolved')
    require(isinstance(audit.get('evidence'),list) and bool(audit['evidence']),
            'Independent audit has no evidence')
    for item in audit['evidence']:
        file=relative_file(folder,item['path'])
        require(digest_text(item.get('sha256')) and sha(file)==item['sha256'],'Audit evidence changed')
    if kind=='source':
        require(audit.get('approved_node_ast')==APPROVED_NODE_AST,'Source audit differs from pinned nodes')
    else:
        require(audit.get('normal_size_reviewed') is True and audit.get('dynamic_reviewed') is True,
                'Static or enlarged-only inspection cannot approve visual appearance')
        require(all(audit.get('review',{}).get(k)=='passed' for k in GOALS),'Missing visual join or adjacent-state review')
    return audit['reviewer']


def require_visual_coverage(audit,movies,matrix_frames,fine_frames):
    paths={item['path'] for item in audit['evidence']}
    require(movies<=paths and bool(paths & matrix_frames),
            'Independent visual audit must bind all default/fine actual videos and a normal-size stage image')
    require(bool(paths & fine_frames),
            'Independent visual audit must bind an actual finest-black frame, not only default grains')


def require_tests(path,digest):
    report=read(path)
    require(report.get('schema')==1 and report.get('completed') is True
            and report.get('source_sha256')==digest and report.get('visual_verdict') is None,
            'Pure tests lack source identity or claim visual acceptance')
    runs=report.get('runs',[])
    required={'src/tools/'+k:v for k,v in PURE_TESTS.items()}
    require(len(runs)==len(required) and {r.get('script') for r in runs}==set(required),'Missing related pure-test runs')
    total=0
    for run in runs:
        file=relative_file(path.parent,run['log'])
        require(sha(file)==run.get('sha256'),'Pure-test log changed')
        log=file.read_text(encoding='utf-8')
        counts=re.findall(r'Ran (\d+) tests? in ',log)
        require(run.get('exit_code')==0 and len(counts)==1 and re.search(r'^OK\s*$',log,re.M)
                and not re.search(r'^FAILED\b',log,re.M),'Pure-test execution did not complete successfully')
        count=int(counts[0]);require(count==run.get('count') and count>=required[run['script']],
                                    'Pure-test count differs from the actual log')
        total+=count
    return total


def require_width(folder,evidence,motions):
    from measure_stream_width import read_motion, measure_report, require_nearfield
    file=relative_file(folder,evidence['width']);proof=read(file)
    require(proof.get('schema')==1 and proof.get('completed') is True
            and proof.get('source_sha256')==source_digest()
            and proof.get('min_ratio')==.92 and proof.get('pixel_slack')==0,
            'Width proof changed its source or predeclared near-field rule')
    require(set(proof.get('runs',{}))==set(motions),'Missing measured width comparisons')

    def equal(saved,actual):
        if isinstance(actual,dict):
            return isinstance(saved,dict) and set(saved)==set(actual) and all(equal(saved[k],v) for k,v in actual.items())
        if isinstance(actual,list):
            return isinstance(saved,list) and len(saved)==len(actual) and all(equal(a,b) for a,b in zip(saved,actual))
        if isinstance(actual,(int,float)) and not isinstance(actual,bool):
            return (isinstance(saved,(int,float)) and not isinstance(saved,bool)
                    and math.isfinite(saved) and math.isclose(saved,actual,rel_tol=1e-12,abs_tol=1e-12))
        return saved==actual

    stats={}
    for key in motions:
        run=proof['runs'][key]
        require(run.get('motion')==evidence[key],'Width metrics reference a different capture')
        metrics_file=relative_file(folder,run['metrics'])
        require(sha(metrics_file)==run.get('sha256'),'Saved width metrics changed')
        # Re-read hash-bound original PNGs. Saved numbers or "passed" flags
        # cannot turn a rejected old image into the required negative control.
        recomputed=measure_report(read_motion(relative_file(folder,evidence[key])))
        require(equal(read(metrics_file),recomputed),'Saved width metrics differ from actual PNG measurements')
        require(recomputed.get('source_files')==motions[key]['source_files'],
                'Width measurement source identity differs from motion evidence')
        require(recomputed['nearfield']['valid_frames']==61,'Width comparison contains invalid or truncated bodies')
        failed=False
        try:require_nearfield(recomputed,min_ratio=.92,pixel_slack=0)
        except AssertionError as error:
            failed=True
            require('near-field median ratio' in str(error),'Width negative failed for the wrong reason')
        if key=='baseline_motion_p50':
            require(failed,'Published 3.11 width negative was not rejected')
        elif not key.startswith('baseline_'):
            require(not failed,'New outlet still has an abrupt visible width reduction')
            require(recomputed.get('body_source')=='isolated_stream_gl' and recomputed.get('plain') is False,
                    'New stream body must exclude splashes using actual isolated GL frames')
        stats[key]=dict(nearfield=recomputed['nearfield'],waist=recomputed['waist'],
                        body_source=recomputed['body_source'],negative_rejected=failed)
    # The published 5-second outlet already satisfies .92 at its wider aperture.
    # Preserve that fact: it is a non-regression reference, not a fake negative.
    require(stats['motion_p5']['nearfield']['ratio']['p50']+1e-12
            >=stats['baseline_motion_p5']['nearfield']['ratio']['p50'],
            'The 5-second visible outlet width regressed against published 3.11')
    # Preserve full profiles in the hash-bound evidence, not in push/CI stdout.
    return {key:{name:value for name,value in metrics.items()
                 if name not in ('frames','profiles','source_files')}
            for key,metrics in stats.items()}


def require_shader_proof(path,manifest,raw_manifest,digest,geometry,stream_source):
    proof=read(path)
    require(proof.get('completed') is True and proof.get('passed') is True
            and proof.get('source_sha256')==digest,'Incomplete shared-shader proof')
    from verify_neck_stream_join import remove_join_block
    body=ast.literal_eval(selected_node(ast.parse(stream_source),'FRAGMENT_BODY').value)
    require(proof.get('ablation_mode')=='same-current-join-off'
            and proof.get('ablation_body_sha256')==hashlib.sha256(remove_join_block(body).encode()).hexdigest(),
            'Join proof is not a pure current-shader material ablation')
    require(proof.get('tool_sha256')==hashlib.sha256((ROOT/'src/tools/verify_neck_stream_join.py')
            .read_bytes().replace(b'\r\n',b'\n')).hexdigest(),'Shader proof tool differs from reviewed source')
    require_identity(dict(proof,version=VERSION),manifest,raw_manifest,VERSION)
    require_hidden(proof.get('hidden_at_creation',{}));require_hidden(proof.get('hidden_at_finish',{}))
    require(proof.get('dimensions')==[540,924],'Shader proof dimensions differ')
    names={f['name']:f for f in proof.get('frames',[])}
    wanted={'current','original-shader','helper-neck','no-flow-current',
            'no-flow-original-shader','no-flow-missing-guard-negative'}
    require(len(proof.get('frames',[]))==6 and set(names)==wanted,'Missing shader ablation frames')
    images={}
    for name,frame in names.items():
        file=require_frame(path,frame,proof['dimensions']);require_hidden(frame['hidden'])
        require(frame.get('physics_before')==frame.get('physics_after')==proof['physics']['sha256'],
                'Shader ablation mutated actual physics')
        require(frame.get('shaders',{}).get('all_present_shaders_compiled') is True,'Ablation shader did not compile')
        require(frame.get('short_join_enabled')==(0. if name.startswith('no-flow-') else 1.),
                'Shader proof did not exercise the expected active/inactive path')
        with Image.open(file) as im:images[name]=np.asarray(im.convert('RGB'))
    require(proof.get('assertions') and all(row.get('passed') is True for row in proof['assertions']),
            'Shader proof contains failed assertions')
    width,height=proof['dimensions'];half=geometry['_taper']['t_in'];cx=geometry['_cx']
    outlet=height-(2*geometry['_neck_y']-geometry['_taper']['y_bot'])
    length=max(2.001,1.5*half)
    roi=[max(0,int(np.floor(cx-half-2))),max(0,int(np.floor(outlet))-1),
         min(width,int(np.ceil(cx+half+2))),min(height,int(np.ceil(outlet+length))+1)]
    neck_roi=[int(np.ceil(cx-.5*half)),int(np.ceil(height-geometry['_taper']['y_bot']))+1,
              int(np.floor(cx+.5*half)),int(np.floor(outlet))]
    require(proof.get('allowed_diff_roi')==roi and proof.get('neck_comparison_roi')==neck_roi,
            'Shader proof expanded or moved its permitted comparison region')

    def local_change(left,right):
        changed=np.any(images[left]!=images[right],axis=2)
        outside=changed.copy();x0,y0,x1,y1=roi;outside[y0:y1,x0:x1]=False
        require(changed.any() and not outside.any(),'Shader ablation must change only actual outlet pixels')
        return int(changed.sum())

    changed=local_change('current','original-shader')
    negative=local_change('no-flow-current','no-flow-missing-guard-negative')
    require(np.array_equal(images['no-flow-current'],images['no-flow-original-shader']),
            'Inactive flow path differs from the original shader')
    x0,y0,x1,y1=neck_roi
    require(x1>x0 and y1>y0 and np.array_equal(images['current'][y0:y1,x0:x1],
            images['helper-neck'][y0:y1,x0:x1]),'Shared helper differs from actual neck pixels')
    return dict(local_changed_pixels=changed,negative_changed_pixels=negative,
                no_flow_changed_pixels=0,neck_helper_changed_pixels=0)


def validate(record=None):
    selected=check_current()
    path=Path(record).resolve() if record else selected.resolve()
    require(path==selected.resolve(),'Record is not the current approved release decision')
    r=read(path);manifest=runtime_manifest();digest=source_digest()
    require(current_version()==VERSION and r.get('schema')==1 and r.get('version')==VERSION
            and r.get('status')=='approved' and r.get('source_sha256')==digest
            and r.get('source_manifest')==manifest,'Release record does not describe current 3.12 source')
    require(r.get('baseline_commit')==BASELINE_COMMIT,'Wrong published baseline')
    require(all(r.get('review',{}).get(k)=='passed' for k in GOALS),'Missing scoped visual review')
    require(all(r.get(k) is False for k in ('android_tested','audio_tested','performance_tested')),
            'This visual release must explicitly leave device, audio and performance unverified')
    baseline=baseline_sources()
    sources={name:(ROOT/name).read_bytes() for name in manifest}
    require_source_scope(sources,baseline)
    require(set(r.get('validation_sources',{}))==TOOLS,'Validation tool inventory differs')
    for name,digest_value in r['validation_sources'].items():
        require(hashlib.sha256(relative_file(ROOT,name).read_bytes().replace(b'\r\n',b'\n')).hexdigest()==digest_value,
                'Validation source changed: '+name)
    require(isinstance(r.get('artifacts'),dict) and bool(r['artifacts']),'No review-page artifacts')
    for name,digest_value in r['artifacts'].items():
        require(sha(relative_file(path.parent,name))==digest_value,'Review artifact changed: '+name)
    archive=path.parent/'evidence.tar.xz'
    require(sha(archive)==r.get('archive_sha256'),'Evidence archive changed')
    evidence=r.get('evidence',{})
    require(set(evidence)==EVIDENCE_KEYS,'Missing outlet evidence categories')
    stats={}
    with tempfile.TemporaryDirectory(prefix='outlet-release-') as temp:
        folder=Path(temp);safe_extract(archive,folder);check_members(folder,r.get('archive_members'))
        raw={};old_raw={}
        for name,value in sources.items():
            file=relative_file(folder,'measured-source/'+name)
            require(file.read_bytes().replace(b'\r\n',b'\n')==value.replace(b'\r\n',b'\n'),
                    'Archived measured source differs: '+name)
            raw[name]=sha(file)
        for name,value in baseline.items():
            file=relative_file(folder,'baseline-source/'+name)
            require(file.read_bytes().replace(b'\r\n',b'\n')==value.replace(b'\r\n',b'\n'),
                    'Archived baseline is not published 3.11: '+name)
            old_raw[name]=sha(file)
        matrix_path=relative_file(folder,evidence['matrix'])
        stats['matrix']=require_matrix(matrix_path,manifest,raw)
        old_manifest={k:hashlib.sha256(v.replace(b'\r\n',b'\n')).hexdigest() for k,v in baseline.items()}
        motions={}
        movies=set()
        for period in (5,50):
            for prefix,version,m,rm in (('',VERSION,manifest,raw),('baseline_',BASELINE_VERSION,old_manifest,old_raw)):
                key=prefix+'motion_p'+str(period);file=relative_file(folder,evidence[key])
                motions[key]=require_motion(file,period,m,rm,version)
        stats['width']=require_width(folder,evidence,motions)
        fine_path=relative_file(folder,evidence['motion_fine_black'])
        fine=require_motion(fine_path,50,manifest,raw,VERSION,grain=1,color=5,isolated=True)
        stats['motion_fine_black']=dict(frames=len(fine['frames']),grain=1,color=5)
        from check_flow_release import require_video
        for key,period,grain,color in (('video_p5',5,3,0),('video_p50',50,3,0),
                                       ('video_fine_black',50,1,5)):
            file=relative_file(folder,evidence[key])
            capture=read(file.parent/'result.json');require_capture(capture,1,'particle_sprite_pool')
            require_identity(capture,manifest,raw,VERSION)
            video=read(file)
            require(video.get('version')==VERSION and video.get('period')==period
                    and video.get('grain')==grain and video.get('color')==color,
                    'Video version, period or actual grain/colour configuration differs')
            require(sha(relative_file(file.parent,video['movie']))==video.get('sha256'),'Real-time video changed')
            stats[key]=require_video(video)
            movies.add((file.parent/video['movie']).relative_to(folder).as_posix())
        stats['tests']=require_tests(relative_file(folder,evidence['tests']),digest)
        audits=r.get('audits',{})
        require(set(audits)=={'source','visual'},'Missing independent visual/source audits')
        audit_reports={k:read(relative_file(folder,audits[k])) for k in ('source','visual')}
        reviewers=[require_audit(audit_reports[k],k,digest,folder) for k in ('source','visual')]
        require(isinstance(r.get('implementation_owner'),str) and bool(r['implementation_owner'])
                and len(set(reviewers))==2 and r['implementation_owner'] not in reviewers,
                'Implementation and independent review owners must differ')
        matrix_frames={(matrix_path.parent/f['file']).relative_to(folder).as_posix()
                       for f in read(matrix_path)['frames']}
        fine_frames={(fine_path.parent/f['file']).relative_to(folder).as_posix() for f in fine['frames']}
        require_visual_coverage(audit_reports['visual'],movies,matrix_frames,fine_frames)
        # Shader proof is re-evaluated against original pixels and actual matrix
        # geometry; its own "passed" or generously expanded masks are insufficient.
        geometry=next(f['geometry'] for f in read(matrix_path)['frames'] if f['name']=='p50-t8-f000')
        stats['shader']=require_shader_proof(relative_file(folder,evidence['shader']),manifest,raw,digest,
                                             geometry,sources['src/app/short_grain_stream.py'])
        stats['independent_reviewers']=reviewers
    return stats


if __name__=='__main__':
    print('OUTLET_RELEASE_OK',json.dumps(validate(),ensure_ascii=False))
