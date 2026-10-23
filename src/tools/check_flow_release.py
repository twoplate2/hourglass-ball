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


BASELINE_VERSION='3.9'
BASELINE_COMMIT='27a8279fbbc003c6fd99a0f312c0d24ac3004b11'
MATERIAL_FILES={'src/app/short_grain_stream.py','src/app/app_version.py',
                'src/app/splash_motion.py'}
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


def require_material_scope(manifest,baseline,main_sources=None):
    if set(manifest)!=set(baseline):raise ValueError('Appearance runtime inventory changed')
    for name,digest in manifest.items():
        if name not in MATERIAL_FILES and digest!=baseline[name]:
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


def validate(record=None):
    path=Path(record) if record else check_current()
    r=read(path);version=current_version();manifest=runtime_manifest()
    if r.get('version')!=version or r.get('source_sha256')!=source_digest():
        raise ValueError('Release record does not describe current runtime')
    if r.get('source_manifest')!=manifest:raise ValueError('Runtime manifest changed')
    baseline=baseline_sources()
    require_material_scope(manifest,{name:hashlib.sha256(value.replace(b'\r\n',b'\n')).hexdigest()
                                    for name,value in baseline.items()})
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
