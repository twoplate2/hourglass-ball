"""Material sampling must measure actual sprite pixels, including large grains."""
from pathlib import Path
import math
import sys
import unittest

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from short_grain_stream import particle_vertices,sprite_radius


def unpack_appearance(codes,pitch):
    large=(codes>=.75).astype(float)
    return 2*(codes-large),sprite_radius(pitch,1+large)


class ParticleMaterialTests(unittest.TestCase):
    def test_source_seed_and_size_survive_the_vertex_payload(self):
        phases=np.asarray([0,.01,1.2,math.pi,6.2,math.tau]*2)
        sizes=np.asarray([1]*6+[2]*6)
        v=particle_vertices(np.arange(12),np.arange(12)*2,phases,sizes,1.85)
        seed,radius=unpack_appearance(v[:,0,4],1.85)
        np.testing.assert_allclose(seed,(phases/math.tau)%1,atol=2e-7,rtol=0)
        np.testing.assert_allclose(radius,sprite_radius(1.85,sizes),atol=1e-7,rtol=0)

    def test_texture_pixel_offsets_match_actual_geometry_at_all_sizes(self):
        for pitch in (.8,1.85,4.0):
            with self.subTest(pitch=pitch):
                v=particle_vertices([20,80],[90,150],[.4,2.4],[1,2],pitch)
                _,radius=unpack_appearance(v[:,0,4],pitch)
                material_pixels=v[:,:,2:4]*radius[:,None,None]*np.asarray([1,1.08])
                actual_pixels=v[:,:,:2]-np.asarray([[20,90],[80,150]])[:,None,:]
                np.testing.assert_allclose(material_pixels,actual_pixels,atol=2e-5,rtol=0)


if __name__=='__main__':unittest.main()
