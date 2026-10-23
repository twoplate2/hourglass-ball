"""Temporal negative controls for actual-pixel pause and receiving evidence."""
import copy
import hashlib
import unittest
import numpy as np

from check_flow_release import require_video,require_receiving_arrays


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


if __name__=='__main__':unittest.main()
