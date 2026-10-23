"""Current scoped PC release evidence; historical green reviews cannot override it."""
import ast
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import sys
import subprocess
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))

from PIL import Image

from benchmark_particle_renderer import summarize,_meets_budget
from check_motion_shape_release import safe_extract,require_hidden,sha
from check_short_grain_release import require_capture,compare_preserved
from neck_geometry import inner_width
from release_decision import ROOT,check_current,current_version,runtime_manifest,source_digest
from stream_review_metrics import inspect_motion,inspect_frame
from pixel_flow_review import inspect as inspect_pixel_motion


BASELINE_VERSION='3.10'
BASELINE_COMMIT='c869e24ea54732cddaed22b1ead18989b288e33b'
MATERIAL_FILES={'src/app/app_version.py'}
# Exact syntax boundaries, set only after independent review of the new delta.
# These hashes protect scope; they are not appearance or motion acceptance.
APPROVED_NODE_AST={
    "main._draw_splashes": "59327e35d57357894130b860fd1cb0ae5a7207c6bb03cdd61519d6d7a43aa1bf",
    "stream.splash_colours": "3d423c8091d4da3e88d258733a1224434305a75c6d2ab3e974f8874ab5014fdf",
    "batch.TINT_VERTEX_SHADER": "44c541bd603855a304fdfa1a030d8853ae136c9a78632d4fd2948f46390fd692",
    "batch._tint_bytes": "af91ad6f6bf2546599905b13015b75ef9cb8444413378d23ee3974ae0e2bc09e",
    "batch._tint_context": "977a8094b6135a11cb355613c9d8474bdf9bbac8605423cfb50e722b03e72710",
    "batch.ColoredSplashBatch": "eb7ddc78d26b7e32f596e404a4011292f468d305ced965edb4b32ea4b4addc28",
    "batch.install": "ad668cbaa3dd85389877a9535f0df425667d149fc67a83162ab499838ab75d31"
}
TRACES={'final-normal':(50,3,241),'final-short':(5,3,61),
        'final-fine':(50,1,31),'final-coarse':(50,6,31),
        'final-long':(36000,3,61),'final-tail':(50,3,37)}
TOOLS=['src/tools/check_flow_release.py','src/tools/release_decision.py',
       'src/tools/stream_review_metrics.py','src/tools/record_flow_review.py',
       'src/tools/capture_stream_motion.py','src/tools/verify_short_grain_runtime.py',
       'src/tools/test_particle_continuity.py','src/tools/benchmark_particle_renderer.py',
       'src/tools/test_stream_review_metrics.py','src/tools/test_flow_release.py',
       'src/tools/test_neck_geometry.py','src/tools/test_release_decision.py',
       'src/tools/test_receiving_surface.py','src/tools/test_stream_aperture.py']
TOOLS+=['src/tools/pixel_flow_review.py','src/tools/test_pixel_flow_review.py']
TOOLS+=['src/tools/verify_receiving_pixels.py']
TOOLS+=['src/tools/test_particle_material.py']
TOOLS+=['src/tools/test_flare_occlusion.py']
TOOLS+=['src/tools/test_splash_occlusion.py']
TOOLS+=['src/tools/check_short_grain_release.py']
TOOLS+=['src/tools/test_landing_spread.py']
TOOLS+=['src/tools/test_splash_tint_batch.py']
TOOLS+=['src/tools/test_splash_colours.py']
TOOLS+=['src/tools/test_splash33_impulse.py']
TOOLS+=['src/tools/verify_splash_tint_paths.py']


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))


def _flare_assignment(tree):
    classes=[n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='HourglassWidget']
    methods=[n for c in classes for n in c.body
             if isinstance(n,ast.FunctionDef) and n.name=='update_particles']
    rows=[n for fn in methods for n in ast.walk(fn) if isinstance(n,ast.Assign)
          and any(isinstance(t,ast.Attribute) and isinstance(t.value,ast.Name)
                  and t.value.id=='self' and t.attr=='flares' for t in n.targets)]
    if len(classes)!=1 or len(methods)!=1 or len(rows)!=1 or len(rows[0].targets)!=1:
        raise ValueError('Expected one unique flare retention assignment')
    return rows[0]


def require_flare_scope(candidate,baseline):
    current,old=ast.parse(candidate),ast.parse(baseline)
    allowed,original=_flare_assignment(current),_flare_assignment(old)
    class RestoreRetention(ast.NodeTransformer):
        def visit_Assign(self,node):
            return original if node is allowed else self.generic_visit(node)
    restored=RestoreRetention().visit(current)
    _restore_splash_extraction(restored,old)
    if ast.dump(restored)!=ast.dump(old):
        raise ValueError('Appearance iteration changed protected main outside declared occlusion changes')


def _restore_splash_extraction(current,baseline):
    cls=next(n for n in current.body if isinstance(n,ast.ClassDef) and n.name=='HourglassWidget')
    old_cls=next(n for n in baseline.body if isinstance(n,ast.ClassDef) and n.name=='HourglassWidget')
    helpers=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_draw_splashes']
    if not helpers:return
    redraws=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='redraw']
    old_redraws=[n for n in old_cls.body if isinstance(n,ast.FunctionDef) and n.name=='redraw']
    if len(helpers)!=1 or len(redraws)!=1 or len(old_redraws)!=1 or any(
            isinstance(n,ast.FunctionDef) and n.name=='_draw_splashes' for n in old_cls.body):
        raise ValueError('Expected one unique splash drawing extraction')
    old_redraw,redraw=old_redraws[0],redraws[0]
    assignment=ast.parse('_batch=getattr(self,"_splash_batch",None)').body[0]
    starts=[i for i,n in enumerate(old_redraw.body) if ast.dump(n)==ast.dump(assignment)]
    if len(starts)!=1 or starts[0]+1>=len(old_redraw.body) or not isinstance(old_redraw.body[starts[0]+1],ast.If):
        raise ValueError('Baseline splash drawing block is not unique')
    original=old_redraw.body[starts[0]:starts[0]+2]
    call=ast.parse('self._draw_splashes()').body[0]
    calls=[i for i,n in enumerate(redraw.body) if ast.dump(n)==ast.dump(call)]
    if len(calls)!=1:raise ValueError('Expected one unique splash drawing call')
    expected=ast.parse('''def _draw_splashes(self):
    from short_grain_stream import visible_splash_sizes
    hws,hhs=self.srw,self.srh
    if self._sn and self._mound_carve is not None:
        hws,hhs=visible_splash_sizes(self._sn,self.sx,self.sy,self.srw,self.srh,self._mound_carve._v,self._lower_sand_bot)
''').body[0]
    class DrawingSizes(ast.NodeTransformer):
        def visit_Attribute(self,node):
            if isinstance(node.value,ast.Name) and node.value.id=='self' and node.attr in ('srw','srh'):
                return ast.Name(id='hws' if node.attr=='srw' else 'hhs',ctx=node.ctx)
            return self.generic_visit(node)
    expected.body.extend(DrawingSizes().visit(n) for n in copy.deepcopy(original))
    if ast.dump(helpers[0])!=ast.dump(expected):
        raise ValueError('New splash drawing helper changes more than visible drawing sizes')
    redraw.body[calls[0]:calls[0]+1]=copy.deepcopy(original)
    cls.body.remove(helpers[0])


def _ast_sha(node):
    return hashlib.sha256(ast.dump(node).encode('utf-8')).hexdigest()


def _unique_method(tree,name):
    classes=[n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='HourglassWidget']
    methods=[n for c in classes for n in c.body if isinstance(n,ast.FunctionDef) and n.name==name]
    if len(classes)!=1 or len(methods)!=1:
        raise ValueError('Changed protected main: expected one unique '+name)
    return classes[0],methods[0]


def _require_pinned(node,key):
    if _ast_sha(node)!=APPROVED_NODE_AST.get(key):
        raise ValueError('Changed protected reviewed node: '+key)


def require_splash_main_scope(candidate,baseline,require_tint=False):
    current,old=ast.parse(candidate),ast.parse(baseline)
    cls,emitter=_unique_method(current,'_eject_splash')
    _,original=_unique_method(old,'_eject_splash')
    expected=copy.deepcopy(original)
    assignments=[n for n in ast.walk(expected) if isinstance(n,ast.Assign)
                 and len(n.targets)==1 and isinstance(n.targets[0],ast.Tuple)
                 and [getattr(t,'id',None) for t in n.targets[0].elts]==['vx','vy']]
    if len(assignments)!=1 or not isinstance(assignments[0].value,ast.Tuple):
        raise ValueError('Baseline splash launch assignment is not unique')
    values=assignments[0].value.elts
    if len(values)!=2:raise ValueError('Baseline splash launch components changed')
    for i,factor in enumerate((.55,.35)):
        value=values[i]
        if (not isinstance(value,ast.BinOp) or not isinstance(value.op,ast.Mult)
                or not isinstance(value.right,ast.Constant) or value.right.value!=factor):
            raise ValueError('Baseline splash launch factors changed')
        values[i]=value.left
    if ast.dump(emitter)!=ast.dump(expected):
        raise ValueError('Changed protected main outside the exact 3.3 splash launch restoration')
    cls.body[cls.body.index(emitter)]=copy.deepcopy(original)
    _,draw=_unique_method(current,'_draw_splashes')
    _,old_draw=_unique_method(old,'_draw_splashes')
    if require_tint or ast.dump(draw)!=ast.dump(old_draw):
        _require_pinned(draw,'main._draw_splashes')
        cls.body[cls.body.index(draw)]=copy.deepcopy(old_draw)
    if ast.dump(current)!=ast.dump(old):
        raise ValueError('Changed protected main outside declared splash drawing and launch')


def _new_top_node(tree,name,kind):
    if kind is ast.Assign:
        rows=[n for n in tree.body if isinstance(n,ast.Assign) and len(n.targets)==1
              and isinstance(n.targets[0],ast.Name) and n.targets[0].id==name]
    else:rows=[n for n in tree.body if isinstance(n,kind) and n.name==name]
    if len(rows)!=1:raise ValueError('Expected one unique new splash node: '+name)
    return rows[0]


def require_splash_colours_scope(candidate,baseline):
    current,old=ast.parse(candidate),ast.parse(baseline)
    node=_new_top_node(current,'splash_colours',ast.FunctionDef)
    _require_pinned(node,'stream.splash_colours')
    current.body.remove(node)
    if ast.dump(current)!=ast.dump(old):
        raise ValueError('Changed protected stream outside splash colour helper')


def require_coloured_batch_scope(candidate,baseline):
    current,old=ast.parse(candidate),ast.parse(baseline)
    for name,kind in [('TINT_VERTEX_SHADER',ast.Assign),('_tint_bytes',ast.FunctionDef),
                      ('_tint_context',ast.FunctionDef),('ColoredSplashBatch',ast.ClassDef)]:
        node=_new_top_node(current,name,kind)
        _require_pinned(node,'batch.'+name)
        current.body.remove(node)
    install=_new_top_node(current,'install',ast.FunctionDef)
    old_install=_new_top_node(old,'install',ast.FunctionDef)
    _require_pinned(install,'batch.install')
    current.body[current.body.index(install)]=copy.deepcopy(old_install)
    if ast.dump(current)!=ast.dump(old):
        raise ValueError('Changed protected batch outside coloured splash addition')


def require_material_scope(manifest,baseline,main_sources=None,source_pairs=None):
    if set(manifest)!=set(baseline):raise ValueError('Appearance runtime inventory changed')
    source_pairs=dict(source_pairs or {})
    if main_sources is not None:source_pairs['main.py']=main_sources
    validators={'main.py':require_splash_main_scope,
                'src/app/short_grain_stream.py':require_splash_colours_scope,
                'src/app/flow_splash_experiment.py':require_coloured_batch_scope}
    for name,digest in manifest.items():
        if name not in MATERIAL_FILES and digest!=baseline[name]:
            if name in validators and name in source_pairs:
                validators[name](*source_pairs[name])
                continue
            raise ValueError('Appearance iteration changed protected runtime: '+name)


def require_landing_physics(before,after):
    """Permit declared splash motion only; keep every other recorded state."""
    states=[]
    for frame in (before,after):
        state=copy.deepcopy(frame['physics'])
        if type(state['_sn']) is not int or state['_sn']<0 or state['_sn']!=frame['splashes']:
            raise ValueError('Splash count differs from the recorded live state')
        state.pop('sha256')
        state.pop('_sn')
        state['arrays'].pop('splash')
        state['effects'].pop('_splash_stats')
        states.append(state)
    if states[0]!=states[1]:
        raise ValueError('Physics or random state changed outside declared landing motion: '+before['name'])


def baseline_sources():
    names=subprocess.check_output(['git','ls-tree','-r','--name-only',BASELINE_COMMIT,
        '--','src/app'],cwd=ROOT,text=True).splitlines()
    names=['main.py','buildozer.spec']+[n for n in names if n.endswith('.py') and n.count('/')==2]
    return {name:subprocess.check_output(['git','show',BASELINE_COMMIT+':'+name],cwd=ROOT)
            for name in names}


def require_receiving_arrays(images):
    moving=np.max(np.abs(images['on-a']-images['on-b']),axis=2)
    static=np.max(np.abs(images['off-a']-images['off-b']),axis=2)
    if static.max()!=0 or np.count_nonzero(moving)<20 or moving.max()<3:
        raise ValueError('Isolated receiving texture is frozen or the negative control changes')
    ys,xs=np.where(moving>0)
    return dict(moving_pixels=int(np.count_nonzero(moving)),max_channel_change=int(moving.max()),
        negative_changed_pixels=int(np.count_nonzero(static)),
        bounds=[int(xs.min()),int(ys.min()),int(xs.max()),int(ys.max())])


def require_video(report):
    period=report.get('period',0)
    if (period not in (5,50) or report.get('completed') is not True
            or report.get('ended_settled') is not True or not report.get('pause_enabled')):
        raise ValueError('Incomplete real-time review video')
    rows=report.get('frames',[])
    if len(rows)<period*15 or report.get('duration',0)<period+report.get('pause_length',0)+.9:
        raise ValueError('Video too short or missing frames')
    if any(b['time']<=a['time'] or b['elapsed']<a['elapsed'] for a,b in zip(rows,rows[1:])):
        raise ValueError('Invalid recording timestamps')
    for row in rows:require_hidden(row['hidden'])
    paused=[f for f in rows if not f['running'] and 0<f['elapsed']<period]
    if len(paused)<5 or len({r['image_sha256'] for r in paused})!=1:
        raise ValueError('Pause changes actual rendered pixels')
    last=[r for r in rows if .99*period<=r['elapsed']<period and r['running']]
    if len(last)<2:raise ValueError('Final one percent was not sampled')
    # Reservoir exhaustion precedes the last arrivals. Count actual in-flight
    # supply as well, and require surviving particles rather than upper volume.
    receiving=[r for r in rows if .95*period<r['elapsed']<period and r['running']
               and sum(r['transfer'].get(k,0) for k in ('upper','neck','flight'))>0
               and r['particles']>0 and not r['center_free']]
    if len(receiving)<2 or not all(r['receiving_active'] for r in receiving):
        raise ValueError('Near-full receiving layer was not checked')
    if len({r['contact_sha256'] for r in receiving})<2:
        raise ValueError('Near-full receiving picture is frozen while supplying')
    if rows[-1]['particles'] or rows[-1]['splashes'] or rows[-1]['running']:
        raise ValueError('Video ends before settling')
    last_transfer=rows[-1]['transfer']
    if abs(last_transfer.get('landed',0)-1)>1e-8 or any(abs(last_transfer.get(k,0))>1e-8 for k in ('upper','neck','flight')):
        raise ValueError('Final sand transfer is incomplete despite empty particle lists')
    return dict(frames=len(rows),pause_frames=len(paused),last_percent_frames=len(last),
                receiving_frames=len(receiving))


def manifest_matches(report,manifest):
    files=report.get('source_files',{})
    expected={k:v for k,v in manifest.items() if k!='buildozer.spec'}
    if set(files)!=set(expected) or files!=report.get('source_files_end'):
        raise ValueError('Captured source inventory changed')
    # Captures retain exact original bytes; archived source normalizes for Git.
    return files


def require_tint_path_proof(capture,proof,path,digest):
    expected='ColoredSplashBatch' if path=='batch' else 'coloured_rectangles'
    frames=capture['frames']
    if (proof.get('completed') is not True or proof.get('all_draws_verified') is not True
            or proof.get('requested_path')!=path or proof.get('actual_path')!=expected
            or proof.get('observed_paths')!=[expected]
            or proof.get('force_build_failure') is not (path=='rect')
            or proof.get('install_was_batch') is not True):
        raise ValueError('Actual splash tint path was not verified: '+path)
    if (proof.get('source_sha256')!=digest
            or proof.get('source_files')!=capture['source_files']
            or proof.get('source_files_end')!=capture['source_files_end']):
        raise ValueError('Splash tint path source differs from capture: '+path)
    if (proof.get('frame_count')!=len(frames)
            or proof.get('frame_names')!=[f['name'] for f in frames]
            or proof.get('frame_sha256')!={f['file']:f['sha256'] for f in frames}):
        raise ValueError('Splash tint path proof does not describe the captured frames')
    keys=('draw_calls','nonempty_draw_calls','max_live_splashes','rect_pool_max','context_build_failures')
    if any(type(proof.get(k)) is not int or proof[k]<0 for k in keys):
        raise ValueError('Invalid splash tint path drawing counts')
    maximum=max(f['splashes'] for f in frames)
    if (not maximum or proof['max_live_splashes']<maximum
            or proof['draw_calls']<len(frames)
            or not 0<proof['nonempty_draw_calls']<=proof['draw_calls']):
        raise ValueError('No actual nonempty splash tint drawing was proven')
    if path=='rect':
        if proof['rect_pool_max']<proof['max_live_splashes'] or not proof['context_build_failures']:
            raise ValueError('Ordinary coloured Rectangle fallback was not exercised')
    elif proof['rect_pool_max'] or proof['context_build_failures']:
        raise ValueError('Coloured batch unexpectedly used ordinary fallback')
    adjacent=proof.get('adjacent_paths',{})
    for stem in ('neck','flare'):
        count=adjacent.get(stem+'_batch_draws');classes=adjacent.get(stem+'_classes')
        if (type(count) is not int or not 0<=count<=proof['draw_calls']
                or not isinstance(classes,list) or not classes
                or any(not isinstance(c,str) for c in classes)):
            raise ValueError('Missing adjacent splash path identity')


def require_tint_paths(folder,version,manifest,digest):
    results=[];proofs=[];manifests=[]
    for path in ('batch','rect'):
        arm=folder/('tint-'+path)
        capture=read(arm/'result.json');proof=read(arm/'splash-path.json')
        require_capture(capture,8,'particle_sprite_pool')
        if capture.get('version')!=version or capture.get('dimensions')!=[540,924]:
            raise ValueError('Wrong splash tint path version or dimensions')
        manifests.append(manifest_matches(capture,manifest))
        require_tint_path_proof(capture,proof,path,digest)
        results.append(capture);proofs.append(proof)
    a,b=results
    if (a['normalized_cases']!=b['normalized_cases']
            or [f['name'] for f in a['frames']]!=[f['name'] for f in b['frames']]
            or len({f['name'] for f in a['frames']})!=8
            or a['source_files']!=b['source_files']):
        raise ValueError('Splash tint paths did not capture the same cases and source')
    expected={
        'gold-eight':(50.,8.,'3',.35,0,[0.,1/30,2/30]),
        'black':(50.,8.,'3',.35,5,[0.]),
        'five':(5.,2.5,'3',.35,0,[0.]),
        'tail':(50.,49.,'3',.35,0,[0.]),
        'flat':(50.,25.,'3',0.,0,[0.]),
        'coarse':(50.,25.,'6',.35,0,[0.])}
    if {c['name'] for c in a['normalized_cases']}!=set(expected):
        raise ValueError('Missing splash tint path cases')
    for case in a['normalized_cases']:
        values=tuple(case[k] for k in ('period','elapsed','grain','sand_grain','color','frame_offsets'))
        if (values!=expected[case['name']] or case.get('rough')!='3'
                or case.get('state')!='running' or case.get('pause_seconds')!=0.):
            raise ValueError('Splash tint path cases differ from the reviewed contract')
    for stem in ('neck','flare'):
        key=stem+'_classes'
        if (proofs[0]['adjacent_paths'][key]!=proofs[1]['adjacent_paths'][key]
                or bool(proofs[0]['adjacent_paths'][stem+'_batch_draws'])
                !=bool(proofs[1]['adjacent_paths'][stem+'_batch_draws'])):
            raise ValueError('Adjacent drawing paths changed between splash tint arms')
    for left,right in zip(a['frames'],b['frames']):
        states=[]
        for frame,path in ((left,'batch'),(right,'rect')):
            state=copy.deepcopy(frame);state.pop('sha256')
            contexts=state['shaders']['contexts']
            splash=[c for c in contexts if c['path']=='_splash_context']
            if ((path=='batch' and (len(splash)!=1 or splash[0].get('success') is not True))
                    or (path=='rect' and splash)):
                raise ValueError('Captured splash shader identity disagrees with actual path')
            state['shaders']['contexts']=[c for c in contexts if c['path']!='_splash_context']
            states.append(state)
        if states[0]!=states[1]:
            raise ValueError('Splash tint paths changed physics, geometry or adjacent drawing: '+left['name'])
        arrays=[]
        for frame,path in ((left,'batch'),(right,'rect')):
            file=folder/('tint-'+path)/frame['file']
            if sha(file)!=frame['sha256']:raise ValueError('Splash tint path image changed')
            with Image.open(file) as im:
                if list(im.size)!=a['dimensions']:raise ValueError('Splash tint image dimensions changed')
                arrays.append(np.asarray(im.convert('RGB')))
        if not np.array_equal(*arrays):
            raise ValueError('Splash tint paths differ in actual RGB pixels: '+left['name'])
    return dict(frames=8,rgb_changed_pixels=0,nonempty_frames=sum(f['splashes']>0 for f in a['frames'])),manifests


def validate(record=None):
    path=Path(record) if record else check_current()
    r=read(path);version=current_version();manifest=runtime_manifest()
    if r.get('version')!=version or r.get('source_sha256')!=source_digest():
        raise ValueError('Release record does not describe current runtime')
    if r.get('source_manifest')!=manifest:raise ValueError('Runtime manifest changed')
    baseline=baseline_sources()
    scoped=('main.py','src/app/short_grain_stream.py','src/app/flow_splash_experiment.py')
    pairs={name:((ROOT/name).read_bytes(),baseline[name]) for name in scoped}
    require_material_scope(manifest,{name:hashlib.sha256(value.replace(b'\r\n',b'\n')).hexdigest()
                                    for name,value in baseline.items()},source_pairs=pairs)
    # Every release must include the declared launch restoration and colour path,
    # rather than silently accepting a version-only push against the old image.
    require_splash_main_scope(*pairs['main.py'],require_tint=True)
    require_splash_colours_scope(*pairs['src/app/short_grain_stream.py'])
    require_coloured_batch_scope(*pairs['src/app/flow_splash_experiment.py'])
    goals=('geometry','upper','stream','edges','landing','joins','states','near_full')
    if r.get('status')!='approved' or not all(r.get('review',{}).get(k)=='passed' for k in goals):
        raise ValueError('Missing scoped visual review')
    reviews=r.get('adversarial_reviews',[])
    if len(reviews)<2 or any(x.get('status')!='passed' or not x.get('reviewer')
            or not x.get('evidence') or x.get('open_blockers')!=[] for x in reviews):
        raise ValueError('Adversarial blockers remain open')
    for name in TOOLS:
        expected=r.get('validation_sources',{}).get(name)
        actual=hashlib.sha256((ROOT/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest()
        if actual!=expected:raise ValueError('Validation source changed: '+name)
    for name,digest in r['artifacts'].items():
        if sha(path.parent/name)!=digest:raise ValueError('Reviewed artifact changed: '+name)
    archive=path.parent/'evidence.tar.xz'
    if sha(archive)!=r['archive_sha256']:raise ValueError('Evidence archive changed')
    stats={}
    with tempfile.TemporaryDirectory(prefix='flow-release-') as tmp:
        folder=Path(tmp);safe_extract(archive,folder)
        manifests=[]
        for arm,count,renderer in [('final-matrix-low',55,'particle_sprite_pool'),
                ('final-matrix-high',17,'particle_sprite_pool'),('final-fallback',4,'line_pool'),
                ('material-controls',4,'particle_sprite_pool')]:
            d=read(folder/arm/'result.json');require_capture(d,count,renderer)
            if d['version']!=version:raise ValueError('Wrong captured version')
            manifests.append(manifest_matches(d,manifest))
            for f in d['frames']:
                if sha(folder/arm/f['file'])!=f['sha256']:raise ValueError('Stage image changed')
                g=f['geometry'];period=next(c['period'] for c in d['normalized_cases'] if c['name']==f['case'])
                if abs(2*g['_taper']['t_in']-inner_width(period,2*g['_R_inner']))>1e-6:
                    raise ValueError('Measured aperture differs from the period contract')
                if abs(sum(f['transfer'].values())-1)>1e-8:raise ValueError('Sand mass is not conserved')
            if arm=='final-matrix-low':
                paused=[f for f in d['frames'] if f['case']=='paused']
                if len(paused)!=2 or paused[0]['sha256']!=paused[1]['sha256']:
                    raise ValueError('Stage pause does not freeze')
            if arm=='material-controls':
                controls={(c['color'],c['sand_grain']):c['name'] for c in d['normalized_cases']}
                if set(controls)!={(0,0.0),(0,.7),(5,0.0),(5,.7)}:
                    raise ValueError('Missing material control endpoints')
                frames={f['case']:f for f in d['frames']}
                for color in (0,5):
                    a,b=[frames[controls[color,strength]] for strength in (0.0,.7)]
                    if a['physics']['arrays']!=b['physics']['arrays'] or a['geometry']!=b['geometry']:
                        raise ValueError('Material strength changed particle state or geometry')
                    height=d['dimensions'][1];g=a['geometry'];half=g['_taper']['t_in']
                    top=height-a['outlet_y'];bottom=height-g['mound_top_y']
                    box=(int(g['_cx']-.6*half),int(top+.25*(bottom-top)),
                         int(g['_cx']+.6*half),int(top+.7*(bottom-top)))
                    with Image.open(folder/arm/a['file']) as ia,Image.open(folder/arm/b['file']) as ib:
                        if np.array_equal(np.asarray(ia.crop(box)),np.asarray(ib.crop(box))):
                            raise ValueError('Material strength does not affect actual stream pixels')
        stats['tint_paths'],tint_manifests=require_tint_paths(folder,version,manifest,source_digest())
        manifests.extend(tint_manifests)
        previous=read(folder/'baseline-matrix-low/result.json')
        require_capture(previous,55,'particle_sprite_pool')
        perf_baseline=read(folder/'perf-before/motion.json')
        if (previous['version']!=BASELINE_VERSION
                or previous['source_files']!=previous['source_files_end']
                or previous['source_files']!=perf_baseline['source_files']):
            raise ValueError('Preservation baseline is not the exact published runtime')
        for f in previous['frames']:
            if sha(folder/'baseline-matrix-low'/f['file'])!=f['sha256']:
                raise ValueError('Preservation baseline pixels changed')
        current={f['name']:f for f in read(folder/'final-matrix-low/result.json')['frames']}
        for f in previous['frames']:
            require_landing_physics(f,current[f['name']])
        stats['preserved_frames']=compare_preserved(folder/'baseline-matrix-low/result.json',
            folder/'final-matrix-low/result.json')
        for arm,(period,grain,count) in TRACES.items():
            d=read(folder/arm/'motion.json');manifests.append(manifest_matches(d,manifest))
            if (not d['completed'] or d['version']!=version or d['period']!=period
                    or d['grain']!=grain or len(d['frames'])!=count or not d.get('continuity',{}).get('passed')):
                raise ValueError('Incomplete actual particle trace: '+arm)
            if arm!='final-tail':stats[arm]=inspect_motion(folder/arm/'motion.json')
            for f in d['frames']:
                require_hidden(f['native_hidden'])
                if f['draw_state']['particles']!=f['draw_state']['rendered_particles']:
                    raise ValueError('Particle mesh is incomplete')
                if sha(folder/arm/f['file'])!=f['sha256']:raise ValueError('Motion image changed')
        for arm in ('video-p5','video-p50'):
            capture=read(folder/arm/'result.json');require_capture(capture,1,'particle_sprite_pool')
            manifests.append(manifest_matches(capture,manifest))
            d=read(folder/arm/'audit.json');stats[arm]=require_video(d)
            if d['version']!=version or sha(folder/arm/d['movie'])!=d['sha256']:
                raise ValueError('Video source or bytes changed')
        pixel_report=read(folder/'pixel-motion/motion.json')
        manifests.append(manifest_matches(pixel_report,manifest))
        stats['pixel_motion']=inspect_pixel_motion(folder/'pixel-motion/motion.json')
        arm='receiving-isolation'
        capture=read(folder/arm/'result.json');require_capture(capture,1,'particle_sprite_pool')
        manifests.append(manifest_matches(capture,manifest))
        d=read(folder/arm/'receiving.json');require_hidden(d['hidden'])
        if not d.get('completed') or d['center_free'] or abs(d['elapsed']-49)>1e-7:
            raise ValueError('Receiving isolation did not reach confined near-full state')
        images={}
        for name,digest in d['images'].items():
            p=folder/arm/(name+'.png')
            if sha(p)!=digest:raise ValueError('Receiving isolation image changed')
            with Image.open(p) as im:images[name]=np.asarray(im.convert('RGB')).astype(int)
        stats['receiving_isolation']=require_receiving_arrays(images)
        if any(d[k]!=v for k,v in stats['receiving_isolation'].items()):
            raise ValueError('Receiving isolation summary changed')
        for name,expected in manifests[0].items():
            saved=folder/'measured-source'/name
            if sha(saved)!=expected or hashlib.sha256(saved.read_bytes().replace(b'\r\n',b'\n')).hexdigest()!=manifest[name]:
                raise ValueError('Saved runtime differs from measured source')
        if any(m!=manifests[0] for m in manifests):raise ValueError('Captures ran different sources')
        old=read(folder/'negative-3.7/motion.json');f=old['frames'][0]
        if sha(folder/'negative-3.7'/f['file'])!=f['sha256']:raise ValueError('Old negative pixels changed')
        with Image.open(folder/'negative-3.7'/f['file']) as image:
            if 'exit_expands' not in inspect_frame(image.convert('RGB'),f['geometry'])['failures']:
                raise ValueError('Known 3.7 exit step was not rejected')
        try:inspect_motion(folder/'negative-coarse/motion.json')
        except ValueError as exc:
            if 'Coarse interior' not in str(exc):raise
        else:raise ValueError('Blocked coarse rod was not rejected')
        performance={}
        for arm in ('perf-before','perf-after'):
            capture=read(folder/arm/'motion.json')
            if not capture.get('completed') or capture['source_files']!=capture['source_files_end']:
                raise ValueError('Performance runtime identity is incomplete')
            if arm=='perf-after':
                if capture['source_files']!=manifests[0] or capture['version']!=version:
                    raise ValueError('Performance candidate differs from reviewed runtime')
            else:
                if capture['version']!=BASELINE_VERSION:raise ValueError('Wrong performance baseline')
                if set(capture['source_files'])!=set(baseline)-{'buildozer.spec'}:
                    raise ValueError('Performance baseline source inventory changed')
                for name,digest in capture['source_files'].items():
                    saved=folder/'baseline-source'/name
                    expected=baseline[name]
                    if sha(saved)!=digest or saved.read_bytes().replace(b'\r\n',b'\n')!=expected.replace(b'\r\n',b'\n'):
                        raise ValueError('Baseline is not the published '+BASELINE_VERSION+' runtime')
            d=read(folder/arm/'timing.json')
            if not d.get('completed') or len(d.get('runs',[]))!=3 or capture.get('benchmark')!=d:
                raise ValueError('Incomplete performance runs')
            raw=[]
            for run in d['runs']:
                if not run.get('completed') or len(run['frame_ms'])!=300 or summarize(run['frame_ms'])!=run['summary']:
                    raise ValueError('Performance raw samples do not match summary')
                if arm=='perf-after' and not _meets_budget(run['summary']):raise ValueError('PC round work budget failed')
                raw.extend(run['frame_ms'])
            summary=summarize(raw)
            if summary!=d['summary']:raise ValueError('Performance summary changed')
            if arm=='perf-after' and not _meets_budget(summary):raise ValueError('PC work budget failed')
            performance[arm]=summary
        before=read(folder/'perf-before/timing.json');after=read(folder/'perf-after/timing.json')
        if any(x['start_state']!=before['runs'][0]['start_state'] for x in before['runs']+after['runs']):
            raise ValueError('Performance rounds used different starting conditions')
        stats['performance']=performance
    return stats


if __name__=='__main__':print('FLOW_RELEASE_OK',json.dumps(validate(),ensure_ascii=False))
