"""Temporal negative controls for actual-pixel pause and receiving evidence."""
import copy
import hashlib
import unittest
import numpy as np

from check_flow_release import require_video,require_receiving_arrays,require_material_scope,require_flare_scope


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
    def test_only_renderer_and_version_may_change(self):
        baseline={'main.py':'a','buildozer.spec':'b','src/app/flow_numpy.py':'c',
                  'src/app/short_grain_stream.py':'d','src/app/app_version.py':'e'}
        candidate=dict(baseline)
        candidate.update({'src/app/short_grain_stream.py':'new','src/app/app_version.py':'3.9'})
        require_material_scope(candidate,baseline)
        for name in ('main.py','buildozer.spec','src/app/flow_numpy.py'):
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

if __name__=='__main__':unittest.main()
