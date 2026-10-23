"""Temporal negative controls for actual-pixel pause and receiving evidence."""
import copy
import ast
import hashlib
import unittest
from unittest.mock import patch
import numpy as np

from check_flow_release import require_video,require_receiving_arrays,require_material_scope,require_flare_scope,require_landing_physics
from check_short_grain_release import compare_preserved
from pathlib import Path
import json
import tempfile
from PIL import Image


def fixture():
    hidden=dict(IsWindow=True,IsWindowVisible=False,IsIconic=False,window_state_config='hidden')
    frames=[]
    for i in range(701):
        t=i/100;e=min(5,t if t<4.7 else 4.7 if t<5.2 else t-.5)
        paused=4.7<=t<5.2
        frames.append(dict(time=t,elapsed=e,running=e<5 and not paused,particles=int(.25<e<5),
            splashes=int(1<e<5),hidden=hidden,image_sha256=hashlib.sha256(str(e).encode()).hexdigest(),
            contact_sha256=str(e),center_free=e<4.75,receiving_active=True,
            transfer=dict(upper=max(0,1-e/5),neck=0,flight=0,landed=min(1,e/5))))
    return dict(period=5,completed=True,ended_settled=True,pause_enabled=True,pause_length=.5,duration=7,frames=frames)


class VideoControls(unittest.TestCase):
    def test_complete_flow(self):require_video(fixture())
    def test_inflight_sand_is_still_supply(self):
        r=fixture()
        for f in r['frames']:
            if 4.75<f['elapsed']<5:
                f['transfer']=dict(upper=0,neck=0,flight=.01)
        require_video(r)
    def test_empty_reservoirs_without_particles_are_not_supply(self):
        r=fixture()
        for f in r['frames']:
            if f['elapsed']>4.75:
                f['particles']=0
                f['transfer']=dict(upper=0,neck=0,flight=0)
        with self.assertRaisesRegex(ValueError,'receiving'):require_video(r)
    def test_early_pause_fade_is_not_ignored(self):
        r=fixture();r['frames'][471]['image_sha256']='different'
        with self.assertRaisesRegex(ValueError,'Pause'):require_video(r)
    def test_near_full_freeze_fails(self):
        r=fixture()
        for f in r['frames']:f['contact_sha256']='frozen'
        with self.assertRaisesRegex(ValueError,'frozen'):require_video(r)
    def test_missing_receiving_layer_fails(self):
        r=fixture()
        for f in r['frames']:f['receiving_active']=False
        with self.assertRaisesRegex(ValueError,'receiving'):require_video(r)
    def test_incomplete_video_fails(self):
        r=copy.deepcopy(fixture());r['completed']=False
        with self.assertRaises(ValueError):require_video(r)

    def test_empty_particle_lists_do_not_prove_full_sand_transfer(self):
        r=fixture();r['frames'][-1]['transfer']['landed']=.9
        with self.assertRaisesRegex(ValueError,'transfer'):require_video(r)

    def test_isolated_frozen_texture_fails_despite_active_mesh(self):
        images={k:np.zeros((20,20,3),int) for k in ('on-a','on-b','off-a','off-b')}
        with self.assertRaisesRegex(ValueError,'texture'):require_receiving_arrays(images)

    def test_isolated_texture_and_static_negative_pass(self):
        images={k:np.zeros((20,20,3),int) for k in ('on-a','on-b','off-a','off-b')}
        images['on-b'][:5,:5]=6
        require_receiving_arrays(images)
        images['off-b'][0,0]=1
        with self.assertRaisesRegex(ValueError,'negative'):require_receiving_arrays(images)


class MaterialScopeControls(unittest.TestCase):
    def test_only_version_can_change_without_explicit_scope_sources(self):
        baseline={'main.py':'a','buildozer.spec':'b','src/app/flow_numpy.py':'c',
                  'src/app/short_grain_stream.py':'d','src/app/app_version.py':'e',
                  'src/app/splash_motion.py':'f'}
        candidate=dict(baseline)
        candidate.update({'src/app/app_version.py':'3.11'})
        require_material_scope(candidate,baseline)
        for name in ('main.py','buildozer.spec','src/app/flow_numpy.py',
                     'src/app/short_grain_stream.py','src/app/splash_motion.py'):
            with self.subTest(name=name):
                changed=dict(candidate);changed[name]='changed'
                with self.assertRaisesRegex(ValueError,'protected'):
                    require_material_scope(changed,baseline)

    def test_added_or_missing_runtime_file_is_not_appearance_only(self):
        baseline={'main.py':'a','src/app/grain_style.py':'b'}
        for candidate in ({'main.py':'a'},dict(baseline,extra='x')):
            with self.assertRaisesRegex(ValueError,'inventory'):
                require_material_scope(candidate,baseline)


class FlareScopeControls(unittest.TestCase):
    baseline='''class HourglassWidget:
    def update_particles(self,dt):
        marker=1
        self.flares=[f for f in self.flares if f['end']>self._mound_flow_clock]
        return marker
    def redraw(self):
        return 4
'''

    def changed(self):
        return self.baseline.replace("f['end']>self._mound_flow_clock",
            "f['end']>self._mound_flow_clock and f['y']>=self._mound_top_at(f['x'])")

    def test_only_unique_retention_assignment_may_change(self):
        require_flare_scope(self.changed(),self.baseline)
        # The historical 3.8->3.9 helper no longer exempts the current main.
        with self.assertRaisesRegex(ValueError,'protected'):
            require_material_scope({'main.py':'new'},{'main.py':'old'},
                                   main_sources=(self.changed(),self.baseline))

    def test_other_main_nodes_still_fail(self):
        for candidate in (self.changed().replace('marker=1','marker=2'),
                          self.changed().replace('return 4','return 5')):
            with self.assertRaisesRegex(ValueError,'protected main'):
                require_flare_scope(candidate,self.baseline)

    def test_duplicate_or_chained_assignment_cannot_hide_other_changes(self):
        for candidate in (self.changed().replace('return marker','self.flares=[]\n        return marker'),
                          self.changed().replace('self.flares=[','self.flares=self.dusts=[')):
            with self.assertRaisesRegex(ValueError,'unique flare'):
                require_flare_scope(candidate,self.baseline)


class SplashScopeControls(unittest.TestCase):
    old_block='''        _batch=getattr(self,"_splash_batch",None)
        if _batch is None:
            self._sync_rects_arrays(self._splash_group,self._splash_rects,self._sn,self.sx,self.sy,self.srw,self.srh)
        else:
            _batch.update_arrays(self._sn,self.sx,self.sy,self.srw,self.srh)
'''
    helper='''    def _draw_splashes(self):
        from short_grain_stream import visible_splash_sizes
        hws,hhs=self.srw,self.srh
        if self._sn and self._mound_carve is not None:
            hws,hhs=visible_splash_sizes(self._sn,self.sx,self.sy,self.srw,self.srh,self._mound_carve._v,self._lower_sand_bot)
'''+old_block.replace('self.srw,self.srh)','hws,hhs)')

    def baseline(self):
        return FlareScopeControls.baseline.replace('        return 4\n',self.old_block+'        return 4\n')

    def changed(self):
        return self.baseline().replace(self.old_block,'        self._draw_splashes()\n')+self.helper

    def test_only_exact_splash_extraction_is_allowed(self):
        require_flare_scope(self.changed(),self.baseline())

    def test_other_redraw_nodes_remain_protected(self):
        candidate=self.changed().replace('        return 4','        return 5')
        with self.assertRaisesRegex(ValueError,'protected main'):
            require_flare_scope(candidate,self.baseline())

    def test_duplicate_helper_or_nonexact_call_is_rejected(self):
        candidates=(self.changed()+self.helper,
                    self.changed().replace('self._draw_splashes()','self._draw_splashes(1)'),
                    self.changed().replace('self._draw_splashes()','self._draw_splashes()\n        self._draw_splashes()'))
        for candidate in candidates:
            with self.assertRaisesRegex(ValueError,'splash'):
                require_flare_scope(candidate,self.baseline())

    def test_new_helper_cannot_hide_physics_or_array_writes(self):
        candidate=self.changed().replace('        hws,hhs=self.srw,self.srh',
            '        self._sn=0\n        hws,hhs=self.srw,self.srh')
        with self.assertRaisesRegex(ValueError,'splash'):
            require_flare_scope(candidate,self.baseline())


class LandingPreservationControls(unittest.TestCase):
    def frame(self):
        physics=dict(pn=1,_sn=1,ledger={'landed':.5},
            arrays={'main':{'px':'unchanged'},'splash':{'sx':'old'}},
            scalars={'_mound_flow_clock':8.,'_splash_density':1.},
            effects={'flares':[{'x':50,'y':25,'end':8.1}], 'dusts':[],
                     '_contact_hits':[[8.,50.,100.]],'_splash_stats':{'settled':0}},
            random_sha256='main-rng',effect_random_sha256='effect-rng',sha256='state')
        return dict(name='sample',file='frame.png',splashes=1,physics=physics,
            transfer={'landed':.5},dimensions=[100,100],half_width=5.,outlet_y=70.,
            effect_rects=[[49.,19.,51.,21.]],
            geometry={'_cx':50.,'_R_inner':40.,'_lower_y_c':40.,'_lower_sand_bot':0.,
                      'contact_diameter':10.,'mound_cols':[[10.,20.,True,20.],[90.,20.,True,20.]]})

    def changed(self):
        frame=self.frame();p=frame['physics']
        p['_sn']=2;p['arrays']['splash']={'sx':'new'}
        p['effects']['_splash_stats']={'settled':1};p['sha256']='new-state'
        frame['splashes']=2;frame['effect_rects']=[[48.,8.,50.,10.],[50.,8.,52.,10.]]
        return frame

    def compare(self,b,*,pixel=None,landing=False,**flags):
        if landing:require_landing_physics(self.frame(),b)
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);a=root/'before';a.mkdir();d=root/'after';d.mkdir()
            image=Image.new('RGB',(100,100),(210,160,85));image.save(a/'frame.png')
            if pixel is not None:image.putpixel(pixel,(0,0,0))
            image.save(d/'frame.png')
            (a/'result.json').write_text(json.dumps({'frames':[self.frame()]}),encoding='utf-8')
            (d/'result.json').write_text(json.dumps({'frames':[b]}),encoding='utf-8')
            return compare_preserved(a/'result.json',d/'result.json',**flags)

    def test_explicit_landing_scope_allows_only_splash_state(self):
        self.assertEqual(self.compare(self.changed(),landing=True),1)
        with self.assertRaisesRegex(ValueError,'Physics'):
            self.compare(self.changed(),allow_buried_flares=True)

    def test_main_mass_and_geometry_remain_protected(self):
        changes=(lambda f:f['physics']['arrays']['main'].update(px='moved'),
                 lambda f:f['transfer'].update(landed=.6),
                 lambda f:f['geometry'].update(_cx=51.))
        for change in changes:
            f=self.changed();change(f)
            with self.assertRaisesRegex(ValueError,'Main particles|Physics'):
                self.compare(f,landing=True)

    def test_random_other_effects_and_clock_remain_protected(self):
        changes=(lambda p:p.update(random_sha256='different'),
                 lambda p:p.update(effect_random_sha256='different'),
                 lambda p:p['effects']['flares'].clear(),
                 lambda p:p['effects']['dusts'].append({'x':1}),
                 lambda p:p['effects']['_contact_hits'].clear(),
                 lambda p:p['scalars'].update(_mound_flow_clock=9.))
        for change in changes:
            f=self.changed();change(f['physics'])
            with self.assertRaisesRegex(ValueError,'Physics'):
                self.compare(f,landing=True)

    def test_count_must_match_recorded_splash_count(self):
        f=self.changed();f['splashes']=1
        with self.assertRaisesRegex(ValueError,'count'):
            self.compare(f,landing=True)

    def test_deep_mound_pixels_are_not_exempted_by_landing_scope(self):
        with self.assertRaisesRegex(ValueError,'Pixels changed outside'):
            self.compare(self.changed(),pixel=(50,95),landing=True)

    def test_only_actual_small_splash_rectangles_extend_pixel_mask(self):
        self.assertEqual(self.compare(self.changed(),pixel=(49,91),landing=True),1)
        f=self.changed();f['effect_rects'][0]=[10.,1.,80.,20.]
        with self.assertRaisesRegex(ValueError,'Invalid effect rectangle'):
            self.compare(f,landing=True)

class SplashSourceScopeControls(unittest.TestCase):
    baseline='''class HourglassWidget:
    def _eject_splash(self,x,y,speed):
        marker=1
        vx,vy=side*math.sin(angle)*speed*.55,abs(math.cos(angle))*speed*.35
        return vx,vy,marker
    def _draw_splashes(self):
        return 0
    def redraw(self):
        return 4
'''

    def restored(self):
        return self.baseline.replace('speed*.55','speed').replace('speed*.35','speed')

    def require(self,source):
        from check_flow_release import require_splash_main_scope
        return require_splash_main_scope(source,self.baseline)

    def test_only_the_two_historical_launch_multipliers_are_restored(self):
        self.require(self.restored())

    def test_partial_or_wrong_launch_restoration_is_rejected(self):
        for source in (self.baseline,self.restored().replace('speed,abs','speed*.9,abs')):
            with self.assertRaisesRegex(ValueError,'launch|splash'):
                self.require(source)

    def test_other_emitter_or_main_changes_are_rejected(self):
        for source in (self.restored().replace('marker=1','marker=2'),
                       self.restored().replace('return 4','return 5')):
            with self.assertRaisesRegex(ValueError,'protected main|launch'):
                self.require(source)

    def test_duplicate_emitter_cannot_hide_a_second_implementation(self):
        source=self.restored()+'''    def _eject_splash(self,x,y,speed):
        return 0
'''
        with self.assertRaisesRegex(ValueError,'unique|protected main'):
            self.require(source)

    def test_approved_drawing_does_not_exempt_later_physical_writes(self):
        from check_flow_release import APPROVED_NODE_AST,_ast_sha
        source=self.restored().replace('return 0','self._sync_tinted()')
        node=next(n for n in ast.parse(source).body[0].body if n.name=='_draw_splashes')
        with patch.dict(APPROVED_NODE_AST,{'main._draw_splashes':_ast_sha(node)}):
            self.require(source)
            bad=source.replace('self._sync_tinted()','self.pn=0\n        self._sync_tinted()')
            with self.assertRaisesRegex(ValueError,'reviewed node'):
                self.require(bad)

    def test_required_colour_path_cannot_revert_to_the_old_draw_body(self):
        from check_flow_release import APPROVED_NODE_AST,_ast_sha,require_splash_main_scope
        source=self.restored().replace('return 0','self._sync_tinted()')
        node=next(n for n in ast.parse(source).body[0].body if n.name=='_draw_splashes')
        with patch.dict(APPROVED_NODE_AST,{'main._draw_splashes':_ast_sha(node)}):
            require_splash_main_scope(source,self.baseline,require_tint=True)
            with self.assertRaisesRegex(ValueError,'reviewed node'):
                require_splash_main_scope(self.restored(),self.baseline,require_tint=True)


class SplashModuleScopeControls(unittest.TestCase):
    stream='''import math
VERTEX_SHADER='unchanged stream'
def existing():
    return 1
'''
    colour='''def splash_colours(count):
    return []
'''
    batch='''VERTEX_SHADER='unchanged bounds'
class SplashBatch:
    def update_arrays(self):
        return 1
def install(widget_class):
    return 1
def install_neck(widget_class):
    return 2
'''
    additions='''TINT_VERTEX_SHADER='new tint'
def _tint_bytes(count,rgba8):
    return bytes(rgba8)
def _tint_context():
    return None
class ColoredSplashBatch(SplashBatch):
    pass
'''

    def source_and_pins(self):
        from check_flow_release import _ast_sha
        source=self.batch.replace('def install(widget_class):\n    return 1',
                                  'def install(widget_class):\n    return 3')+self.additions
        pins={}
        for node in ast.parse(source).body:
            name=(node.targets[0].id if isinstance(node,ast.Assign) else getattr(node,'name',None))
            if name in ['TINT_VERTEX_SHADER','_tint_bytes','_tint_context','ColoredSplashBatch','install']:
                pins['batch.'+name]=_ast_sha(node)
        return source,pins

    def test_colour_helper_addition_keeps_existing_stream_shader_and_functions(self):
        from check_flow_release import require_splash_colours_scope,APPROVED_NODE_AST,_ast_sha
        source=self.stream+self.colour
        pin={'stream.splash_colours':_ast_sha(ast.parse(self.colour).body[0])}
        with patch.dict(APPROVED_NODE_AST,pin):
            require_splash_colours_scope(source,self.stream)
            for changed in (source.replace("'unchanged stream'","'different'"),
                            source.replace('return 1','return 9')):
                with self.assertRaisesRegex(ValueError,'protected stream'):
                    require_splash_colours_scope(changed,self.stream)

    def test_colour_helper_body_and_duplicate_node_are_not_a_blanket_exemption(self):
        from check_flow_release import require_splash_colours_scope,APPROVED_NODE_AST,_ast_sha
        source=self.stream+self.colour
        pin={'stream.splash_colours':_ast_sha(ast.parse(self.colour).body[0])}
        with patch.dict(APPROVED_NODE_AST,pin):
            with self.assertRaisesRegex(ValueError,'reviewed node'):
                require_splash_colours_scope(source.replace('return []','count.pn=0\n    return []'),self.stream)
            with self.assertRaisesRegex(ValueError,'unique'):
                require_splash_colours_scope(source+self.colour,self.stream)

    def test_reviewed_colour_batch_additions_preserve_the_original_module(self):
        from check_flow_release import require_coloured_batch_scope,APPROVED_NODE_AST
        source,pins=self.source_and_pins()
        with patch.dict(APPROVED_NODE_AST,pins):
            require_coloured_batch_scope(source,self.batch)

    def test_original_shared_batch_neck_and_shader_still_cannot_change(self):
        from check_flow_release import require_coloured_batch_scope,APPROVED_NODE_AST
        source,pins=self.source_and_pins()
        with patch.dict(APPROVED_NODE_AST,pins):
            for bad in (source.replace("'unchanged bounds'","'different'"),
                        source.replace('        return 1','        return 9'),
                        source.replace('    return 2','    return 9')):
                with self.assertRaisesRegex(ValueError,'protected batch'):
                    require_coloured_batch_scope(bad,self.batch)

    def test_reviewed_install_and_new_helper_internal_writes_are_rejected(self):
        from check_flow_release import require_coloured_batch_scope,APPROVED_NODE_AST
        source,pins=self.source_and_pins()
        with patch.dict(APPROVED_NODE_AST,pins):
            for bad in (source.replace('    return 3','    widget_class.pn=0\n    return 3'),
                        source.replace('    return bytes(rgba8)','    count.pn=0\n    return bytes(rgba8)')):
                with self.assertRaisesRegex(ValueError,'reviewed node'):
                    require_coloured_batch_scope(bad,self.batch)

    def test_unknown_new_batch_nodes_are_not_allowed(self):
        from check_flow_release import require_coloured_batch_scope,APPROVED_NODE_AST
        source,pins=self.source_and_pins()
        with patch.dict(APPROVED_NODE_AST,pins):
            with self.assertRaisesRegex(ValueError,'protected batch'):
                require_coloured_batch_scope(source+'\nUNDECLARED=1\n',self.batch)


class TintPathControls(unittest.TestCase):
    def fixture(self,root):
        hidden=dict(IsWindow=True,IsWindowVisible=False,IsIconic=False)
        specs=[('gold-eight',50.,8.,'3',.35,0,[0.,1/30,2/30]),
               ('black',50.,8.,'3',.35,5,[0.]),('five',5.,2.5,'3',.35,0,[0.]),
               ('tail',50.,49.,'3',.35,0,[0.]),('flat',50.,25.,'3',0.,0,[0.]),
               ('coarse',50.,25.,'6',.35,0,[0.])]
        normalized=[dict(name=n,period=p,elapsed=e,grain=g,sand_grain=s,color=c,frame_offsets=o,
                         rough='3',state='running',pause_seconds=0.) for n,p,e,g,s,c,o in specs]
        for path in ('batch','rect'):
            folder=root/('tint-'+path);folder.mkdir()
            frames=[]
            for case in normalized:
                for i,offset in enumerate(case['frame_offsets']):
                    name=case['name']+'-f%03d'%i;file=name+'.png'
                    Image.new('RGB',(540,924),(211,161,85)).save(folder/file)
                    contexts=[dict(path='_sand_flow_contexts[1]',success=True)]
                    if path=='batch':contexts.append(dict(path='_splash_context',success=True))
                    frames.append(dict(name=name,file=file,case=case['name'],splashes=3,
                        completed=True,passed=True,renderer='particle_sprite_pool',native_hidden=hidden,
                        sha256=hashlib.sha256((folder/file).read_bytes()).hexdigest(),
                        physics=dict(_sn=3,arrays={'main':'same','splash':'same'},random_sha256='same'),
                        transfer={'landed':.5},geometry={'cx':270.},
                        shaders=dict(contexts=contexts,reservoir_neck='gpu')))
            capture=dict(version='3.11',dimensions=[540,924],completed=True,passed=True,source_unchanged=True,
                hidden_at_creation=hidden,hidden_at_finish=hidden,
                cases=[dict(completed=True,passed=True) for _ in normalized],normalized_cases=normalized,
                source_files={'main.py':'raw-source'},source_files_end={'main.py':'raw-source'},frames=frames)
            proof=dict(completed=True,all_draws_verified=True,requested_path=path,
                actual_path='ColoredSplashBatch' if path=='batch' else 'coloured_rectangles',
                observed_paths=['ColoredSplashBatch' if path=='batch' else 'coloured_rectangles'],
                force_build_failure=path=='rect',install_was_batch=True,
                source_sha256='source',source_files=capture['source_files'],source_files_end=capture['source_files_end'],
                frame_count=8,frame_names=[f['name'] for f in frames],
                frame_sha256={f['file']:f['sha256'] for f in frames},
                draw_calls=8,nonempty_draw_calls=8,max_live_splashes=3,
                rect_pool_max=3 if path=='rect' else 0,context_build_failures=1 if path=='rect' else 0,
                adjacent_paths=dict(neck_batch_draws=0,flare_batch_draws=8,
                                    neck_classes=['None'],flare_classes=['FlareBatch']))
            (folder/'result.json').write_text(json.dumps(capture),encoding='utf-8')
            (folder/'splash-path.json').write_text(json.dumps(proof),encoding='utf-8')

    def run_fixture(self,change=None):
        from check_flow_release import require_tint_paths
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);self.fixture(root)
            if change:change(root)
            return require_tint_paths(root,'3.11',{'main.py':'normalized-source','buildozer.spec':'build'},'source')[0]

    def change_json(self,root,path,name,change):
        file=root/('tint-'+path)/name;d=json.loads(file.read_text(encoding='utf-8'));change(d)
        file.write_text(json.dumps(d),encoding='utf-8')

    def test_true_paths_preserve_all_rgb_pixels_and_material_neck(self):
        self.assertEqual(self.run_fixture(),dict(frames=8,rgb_changed_pixels=0,nonempty_frames=8))

    def test_same_images_with_unverified_or_wrong_actual_path_are_rejected(self):
        for change in (lambda d:d.update(all_draws_verified=False),
                       lambda d:d.update(observed_paths=['ColoredSplashBatch'])):
            with self.assertRaisesRegex(ValueError,'Actual splash tint path'):
                self.run_fixture(lambda r:self.change_json(r,'rect','splash-path.json',change))

    def test_empty_claimed_drawing_and_unexercised_fallback_are_rejected(self):
        for change in (lambda d:d.update(nonempty_draw_calls=0),
                       lambda d:d.update(context_build_failures=0),
                       lambda d:d.update(rect_pool_max=1)):
            with self.assertRaisesRegex(ValueError,'nonempty|fallback'):
                self.run_fixture(lambda r:self.change_json(r,'rect','splash-path.json',change))

    def test_path_proof_cannot_relabel_frames_or_source(self):
        for change in (lambda d:d.update(source_sha256='other'),
                       lambda d:d.update(frame_names=['unrelated']*8)):
            with self.assertRaisesRegex(ValueError,'source|captured frames'):
                self.run_fixture(lambda r:self.change_json(r,'rect','splash-path.json',change))

    def test_physics_and_adjacent_shader_changes_are_rejected(self):
        for change in (lambda d:d['frames'][0]['physics'].update(random_sha256='changed'),
                       lambda d:d['frames'][0]['shaders'].update(reservoir_neck='missing')):
            with self.assertRaisesRegex(ValueError,'physics, geometry or adjacent'):
                self.run_fixture(lambda r:self.change_json(r,'rect','result.json',change))

    def test_adjacent_route_identity_is_not_silently_disabled(self):
        with self.assertRaisesRegex(ValueError,'Adjacent drawing paths'):
            self.run_fixture(lambda r:self.change_json(r,'rect','splash-path.json',
                lambda d:d['adjacent_paths'].update(flare_batch_draws=0,flare_classes=['None'])))

    def test_a_single_rgb_channel_difference_is_rejected_even_with_correct_hashes(self):
        def change(root):
            path=root/'tint-rect/gold-eight-f000.png'
            with Image.open(path) as im:
                im.putpixel((270,600),(212,161,85));im.save(path)
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            self.change_json(root,'rect','result.json',lambda d:d['frames'][0].update(sha256=digest))
            self.change_json(root,'rect','splash-path.json',lambda d:d['frame_sha256'].update({'gold-eight-f000.png':digest}))
        with self.assertRaisesRegex(ValueError,'actual RGB pixels'):self.run_fixture(change)

    def test_missing_special_configuration_is_rejected(self):
        def change(root):
            for path in ('batch','rect'):
                self.change_json(root,path,'result.json',
                    lambda d:d['normalized_cases'][-1].update(grain='3'))
        with self.assertRaisesRegex(ValueError,'reviewed contract'):self.run_fixture(change)


if __name__=='__main__':unittest.main()
