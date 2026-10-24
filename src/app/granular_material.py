"""Cached periodic dry-grain pigment, independent of animation and global RNG.

The result replaces a noise field, not a palette or an opacity mask. Existing
grain strength remains responsible for the final amount of visible contrast.
"""
import math
from functools import lru_cache
from pathlib import Path
import struct
import zlib

import numpy as np


_PACKED_COARSE=(1.,1.4,1.8,2.2,2.6,3.)
_PACKED_DIR=Path(__file__).resolve().parents[2]/'ui'/'grains'


def _read_packed_tile(path):
    """Read our fixed L8/filter-0 assets using only the standard library.

    This is deliberately not a general image decoder. The six build assets
    have one documented encoding, so APK startup needs neither Pillow nor a
    graphics context. Unsupported/damaged resources fall back to generation.
    """
    data=path.read_bytes()
    if data[:8]!=b'\x89PNG\r\n\x1a\n':raise ValueError('invalid packed PNG signature')
    offset=8;header=None;parts=[];ended=False
    while offset+12<=len(data):
        length=struct.unpack_from('>I',data,offset)[0]
        end=offset+12+length
        if end>len(data):raise ValueError('truncated packed PNG chunk')
        kind=data[offset+4:offset+8];payload=data[offset+8:end-4]
        if zlib.crc32(kind+payload)&0xffffffff!=struct.unpack_from('>I',data,end-4)[0]:
            raise ValueError('damaged packed PNG chunk')
        if kind==b'IHDR':header=payload
        elif kind==b'IDAT':parts.append(payload)
        elif kind==b'IEND':ended=True;break
        offset=end
    if not ended or header!=struct.pack('>IIBBBBB',512,512,8,0,0,0,0):
        raise ValueError('packed PNG must be non-interlaced 512x512 L8')
    raw=zlib.decompress(b''.join(parts))
    if len(raw)!=512*513:raise ValueError('invalid packed PNG scanlines')
    rows=np.frombuffer(raw,dtype=np.uint8).reshape(512,513)
    if np.any(rows[:,0]):raise ValueError('packed PNG must use filter zero')
    result=(rows[:,1:].astype(np.float32)+.5)/256.
    result.setflags(write=False)
    return result


@lru_cache(maxsize=12)
def material_noise(size=512,coarse=1.8,seed=721):
    """Load an exact default pigment bin, or generate nonstandard parameters.

    Assets quantize the pure field with floor(value*256). Midpoint decoding
    differs by at most 1/512, without changing the material's grain scale.
    The independent read-only cache is shared across palettes and strengths.
    """
    if isinstance(size,(int,np.integer)) and size==512 and seed==721 and coarse in _PACKED_COARSE:
        path=_PACKED_DIR/('packed-grain-%d.png' % (_PACKED_COARSE.index(coarse)+1))
        try:return _read_packed_tile(path)
        except (OSError,ValueError,zlib.error):pass
    return granular_noise(size,coarse,seed)


def stable_grain_order(phases,trails):
    """Global painter order derived only from immutable particle identity.

    A separate uint32 hash namespace assigns depth, not material or shape.
    Identity bits break hash collisions without consulting the current array
    index, so growth, compaction and later Mesh chunking cannot flip survivors.
    Exactly duplicate identities keep their input order. No RNG is consumed.
    """
    phases=np.asarray(phases,dtype=np.float64)
    trails=np.asarray(trails,dtype=np.float64)
    if phases.ndim!=1 or trails.ndim!=1 or phases.shape!=trails.shape:
        raise ValueError('phases and trails must be equal-length one-dimensional arrays')
    if not np.isfinite(phases).all() or not np.isfinite(trails).all():
        raise ValueError('particle identities must be finite')
    phase_bits=phases.view(np.uint64)
    trail_bits=trails.view(np.uint64)
    depth=(phase_bits^(phase_bits>>np.uint64(32))).astype(np.uint32)
    trail_word=(trail_bits^(trail_bits>>np.uint64(32))).astype(np.uint32)
    depth^=trail_word*np.uint32(0x9E3779B9)
    depth^=np.uint32(0xD1B54A35)  # Painter depth salt, independent of grain shape.
    depth^=depth>>np.uint32(16)
    depth*=np.uint32(0x7FEB352D)
    depth^=depth>>np.uint32(15)
    depth*=np.uint32(0x846CA68B)
    depth^=depth>>np.uint32(16)
    return np.lexsort((trail_bits,phase_bits,depth)).astype(np.intp,copy=False)


@lru_cache(maxsize=12)
def granular_noise(size=512, coarse=1.8, seed=721, *, origin=(0,0)):
    """Return a float32 tile centred on .5 with uniform-noise RMS.

    Sites and pigment repeat on the tile's torus. Nearby grains have irregular
    boundaries and only weak surface relief; no bilinear enlargement, black
    mortar or large-scale lighting is baked into the field. ``origin`` shifts
    the same periodic material in texels, mainly useful for seam verification.
    The read-only result is cached independently of colour/contrast, so changing
    palette or grain strength does not regenerate it. Never generate per frame.
    """
    if isinstance(size,bool) or not isinstance(size,(int,np.integer)) or size<4:
        raise ValueError('size must be an integer of at least four texels')
    if not math.isfinite(coarse) or coarse<=0:
        raise ValueError('coarse must be finite and positive')
    if len(origin)!=2 or not all(math.isfinite(v) for v in origin):
        raise ValueError('origin must contain two finite texel offsets')
    cells=max(2,round(size/max(1.,float(coarse))))
    pitch=size/float(cells)
    rng=np.random.default_rng(seed)
    # Broad jitter avoids rows of identical little squares. A 5x5 search is
    # sufficient even with sites near opposite corners of adjacent cells.
    sites=rng.uniform(.05,.95,(cells,cells,2))
    pigment=rng.random((cells,cells))
    phase=rng.random(2)
    x=(((np.arange(size,dtype=float)+.5+origin[0])%size)/pitch+phase[0])%cells
    y=(((np.arange(size,dtype=float)+.5+origin[1])%size)/pitch+phase[1])%cells
    # A weak periodic bend prevents an entire texture edge from coinciding
    # with one lattice phase. Its wavelength is several grains, so individual
    # grains retain their scale instead of becoming ripples or stretched noise.
    cycles=max(1,round(cells/7.))
    warp_x=.45*np.sin(2.*math.pi*cycles*y/cells+phase[0]*math.tau)
    warp_y=.45*np.sin(2.*math.pi*cycles*x/cells+phase[1]*math.tau)
    x,y=(x[None,:]+warp_x[:,None])%cells,(y[:,None]+warp_y[None,:])%cells
    ix=np.floor(x).astype(np.intp);iy=np.floor(y).astype(np.intp)
    nearest=np.full((size,size),np.inf)
    second=np.full_like(nearest,np.inf)
    colour=np.empty_like(nearest)
    local_x=np.empty_like(nearest);local_y=np.empty_like(nearest)
    for oy in (-2,-1,0,1,2):
        row=iy+oy
        for ox in (-2,-1,0,1,2):
            column=ix+ox
            site=sites[row%cells,column%cells]
            dx=x-(column+site[:,:,0])
            dy=y-(row+site[:,:,1])
            distance=dx*dx+dy*dy
            closer=distance<nearest
            second=np.where(closer,nearest,np.minimum(second,distance))
            nearest=np.minimum(nearest,distance)
            colour=np.where(closer,pigment[row%cells,column%cells],colour)
            local_x=np.where(closer,dx,local_x)
            local_y=np.where(closer,dy,local_y)
    # Pigment dominates; these small cues keep each grain dry and slightly
    # uneven without turning the tessellation into outlined paving stones.
    relief=.025*(local_x-local_y)+.025*np.maximum(0.,1.-nearest)
    edge=.018*np.exp(-12.*np.maximum(0.,second-nearest))
    field=colour+relief-edge
    # A monotone histogram mapping keeps shape and pigment order, while every
    # coarse level has the same mean/RMS and [0,1] range as uniform fine noise.
    # Consequently changing coarse cannot quietly turn up overall strength.
    order=np.argsort(field,axis=None,kind='stable')
    result=np.empty(size*size,dtype=np.float32)
    result[order]=(np.arange(size*size,dtype=float)+.5)/(size*size)
    result=result.reshape(size,size)
    result.setflags(write=False)
    return result
