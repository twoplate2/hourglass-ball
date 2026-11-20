"""The airborne emitter must retain the user's accepted 3.3 impulse and RNG."""
import ast
import copy
from functools import lru_cache
import math
from pathlib import Path
import random
import subprocess
import sys
from types import SimpleNamespace
from types import MethodType
import unittest
import numpy as np


ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src/app'))
REFERENCE='ff3b721918a3c6b41428fbc4d26ddb14e430490e'
WEAKENED='c869e24ea54732cddaed22b1ead18989b288e33b'
PARAMETERS=('SPLASH_SPEED_LO','SPLASH_SPEED_HI','SPLASH_GAIN',
    'SPLASH_ANGLE_MIN','SPLASH_ANGLE_MAX','SPLASH_HORIZONTAL_ANGLE_LIMIT',
    'SPLASH_PX_BASE','SPLASH_SIZE_MIX','SPLASH_GRAV_LO','SPLASH_GRAV_HI',
    'SPLASH_LIFT_PX','SPLASH_G_SCALE')


def historical(ref):
    return subprocess.check_output(['git','show',ref+':main.py'],cwd=ROOT).decode('utf-8')


@lru_cache(maxsize=32)
def method(source,name):
    cls=next(n for n in ast.parse(source).body if isinstance(n,ast.ClassDef)
             and n.name=='HourglassWidget')
    return next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name==name)


@lru_cache(maxsize=8)
def parameter_nodes(source):
    return {target.id:node for node in ast.parse(source).body if isinstance(node,ast.Assign)
            for target in node.targets if isinstance(target,ast.Name) and target.id in PARAMETERS}


@lru_cache(maxsize=8)
def emitter(source):
    import os
    nodes=list(parameter_nodes(source).values())+[copy.deepcopy(method(source,'_eject_splash'))]
    ns={'math':math,'os':os}
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),
                 'main.py:3.3-emitter-contract','exec'),ns)
    return ns['_eject_splash']


def representative_motion(source,period,geometry_scale=1.):
    # Fixed PC geometry fixture; use the application's actual period properties.
    names=('_neck_fill_time','_natural_flight_time','_b13_transit_at','_b13_transit','_fall_delay','_particle_motion_scale')
    cls=ast.parse('class Motion: pass').body[0]
    cls.body=[copy.deepcopy(method(source,name)) for name in names]
    constants=[node for node in ast.parse(source).body if isinstance(node,ast.Assign)
               and any(isinstance(t,ast.Name) and t.id in ('NECK_FILL','FALL_DELAY')
                       for t in node.targets)]
    ns={'math':math, 'FLOW_RENDERER':'b13'}
    exec(compile(ast.fix_missing_locations(ast.Module(body=constants+[cls],type_ignores=[])),
                 'main.py:period-scale-contract','exec'),ns)
    fixture=ns['Motion']()
    fixture.duration=period;fixture._geom_ready=True
    fixture._R_inner=209.*geometry_scale
    fixture._neck_y=500.*geometry_scale
    from neck_geometry import inner_width
    half_ratio = inner_width(period, 1.)
    fixture._taper={'y_bot':520.*geometry_scale, 't_in': fixture._R_inner * half_ratio}
    fixture._lower_sand_bot=25.*geometry_scale
    return fixture


def opening_capacity_samples(source):
    """Real widget travel time and full-cylinder upper bound on the reference glass."""
    for duration in (1., 5., 20., 50., 6000.):
        glass = representative_motion(source, duration, 1040. / 455.)
        arrival = glass._fall_delay
        depth = 2. * glass._neck_y - glass._taper["y_bot"] - glass._lower_sand_bot
        capacity = glass._taper["t_in"]**2 * depth / (4. * glass._R_inner**3 / 3.)
        yield glass, arrival, arrival / duration, capacity


def launch(fn,*,seed=297,scale=1.,x=270.,radius=208.8,reference=None,
           impact=600.,clearance=1000.,capped=False):
    rng=random.Random(seed)
    rows=[]
    surface=100.
    profile=SimpleNamespace(column=lambda offset,apex:(surface,True,50.),
                            bounds=lambda offset:(0.,surface+clearance))
    w=SimpleNamespace(_R_inner=radius,_cx=270.,_sn=1 if capped else 0,
        _splash_cap=1 if capped else 0,_splash_stats={'pool_full':0,'born_air':0},
        _mound_profile=profile,_lower_sand_bot=0.,_splash_reference_speed=reference,
        _splash_speed_scale=.8,_particle_motion_scale=scale,_splash_origin_blend=.6,
        _splash_random=lambda:rng,_mound_apex=lambda:120.,_contact_flow_diameter=lambda:40.)
    def append(*values):
        rows.append(values);w._sn+=1
        return w._sn-1
    w._s_append=append
    result=fn(w,x,surface,impact)
    return result,rows,rng.getstate(),dict(w._splash_stats)


class Splash33ImpulseTests(unittest.TestCase):
    def test_bulk_front_fits_the_charge_while_splash_speed_stays_comfortable(self):
        samples = list(opening_capacity_samples(self.current))
        arrivals = [arrival for _, arrival, _, _ in samples]
        self.assertLess(max(arrivals) - min(arrivals), .045)
        self.assertLess(max(arrivals[1:]) - min(arrivals[1:]), .002)
        self.assertLess(arrivals[1], .175)
        splash_scale = samples[0][0]._particle_motion_scale
        for glass, arrival, released, capacity in samples:
            with self.subTest(duration=glass.duration):
                self.assertAlmostEqual(glass._particle_motion_scale, splash_scale)
                self.assertGreater(arrival, 0.)
                self.assertLessEqual(released, capacity)

    @classmethod
    def setUpClass(cls):
        cls.old=historical(REFERENCE)
        cls.current=(ROOT/'main.py').read_text(encoding='utf-8')

    def test_five_second_flight_is_stable_when_the_same_glass_doubles_in_pixels(self):
        def flight(size):
            w = representative_motion(self.current, 5., size)
            return w._fall_delay - w._neck_fill_time
        self.assertAlmostEqual(flight(1.), flight(2.), places=8)

    def test_actual_emitter_ast_matches_published_33(self):
        self.assertEqual(ast.dump(method(self.current,'_eject_splash')),
                         ast.dump(method(self.old,'_eject_splash')))

    def test_airborne_size_angle_gravity_and_speed_parameters_match_33(self):
        a,b=parameter_nodes(self.old),parameter_nodes(self.current)
        self.assertEqual(set(a),set(PARAMETERS));self.assertEqual(set(b),set(PARAMETERS))
        for name in PARAMETERS:
            with self.subTest(parameter=name):self.assertEqual(ast.dump(a[name]),ast.dump(b[name]))

    def test_seeded_emission_matches_33_at_short_and_normal_period_scales(self):
        old,new=emitter(self.old),emitter(self.current)
        for period in (1.,5.,50.):
            scale=representative_motion(self.current,period)._particle_motion_scale
            for seed in (0,7,297):
                for x in (250.,270.,290.):
                    for reference in (None,540.):
                        with self.subTest(period=period,seed=seed,x=x,reference=reference):
                            args=dict(seed=seed,scale=scale,x=x,reference=reference)
                            self.assertEqual(launch(old,**args),launch(new,**args))

    def test_old_weakened_impulse_is_a_real_negative_control(self):
        current=launch(emitter(self.current))[1][0]
        old=launch(emitter(historical(WEAKENED)))[1][0]
        self.assertNotEqual(current,old)
        self.assertAlmostEqual(old[2],current[2]*.55)
        self.assertAlmostEqual(old[3],current[3]*.35)

    def test_pool_limit_and_low_roof_preserve_33_random_consumption(self):
        old,new=emitter(self.old),emitter(self.current)
        for settings in ({'capped':True},{'clearance':1.2},{'clearance':0.}):
            with self.subTest(settings=settings):self.assertEqual(launch(old,**settings),launch(new,**settings))


class HitReplayTests(unittest.TestCase):
    reference='146f60e7116d228cde62c6d5565609643c9b3b64'

    def run_replay(self,source,n,cap=0,first=True,*,density=1.5,mode='normal',initial=0,renderer="line"):
        counter={'scalar_reads':0,'capped_emitter_calls':0,'emitter_calls':0}
        class ObservedArray(np.ndarray):
            def __getitem__(self,key):
                if isinstance(key,(int,np.integer)):counter['scalar_reads']+=1
                return super().__getitem__(key)
        rng=random.Random(713)
        effects=random.Random(912)
        nodes=[copy.deepcopy(method(source,name)) for name in
               ('_replay_hits','_record_contact','_first_contact_extra')]
        parameters={}
        import os
        parameters.update(math=math,os=os)
        exec(compile(ast.Module(body=list(parameter_nodes(source).values()),type_ignores=[]),
                     'emitter-parameters','exec'),parameters)
        ns={'_np':np,'random':rng,'SPLASH_HIT_CHANCE':.50,'FLOW_RENDERER':renderer,
            'SPLASH_SIZE_MIX':parameters['SPLASH_SIZE_MIX']}
        ns.update({name:parameters[name] for name in PARAMETERS})
        exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),
                     'hit-replay-test','exec'),ns)
        profile=SimpleNamespace(contact=lambda dx,apex:100.+.002*dx*dx,
                                column=lambda dx,apex:(100.+.002*dx*dx,mode!='blocked',60.),
                                bounds=lambda dx:(0.,101. if mode=='low-roof' else 900.))
        if mode=='no-profile':profile=None
        def observed(values):return np.asarray(values,dtype=float).view(ObservedArray)
        w=SimpleNamespace(px=observed(np.linspace(230.,310.,n+2)),
            pvy=observed(np.linspace(-400.,-700.,n+2)),pdt=observed(np.full(n+2,.03)),
            pmass=np.full(n+2,.00001),_sand_landed=.2,flares=[],_mound_profile=profile,
            _mound_apex=lambda:150.,_lower_sand_bot=20.,_cx=270.,_mound_flow_clock=.5,
            _splash_density=density,_splash_reference_speed=None if first else 500.,
            _contact_hits=[],_R_inner=220.,_sn=initial,_splash_cap=cap,
            _splash_stats={'pool_full':0,'born_air':0},_splash_speed_scale=.8,
            _particle_motion_scale=2.,_splash_origin_blend=.6,
            _splash_random=lambda:effects,_contact_flow_diameter=lambda:40.,
            sdt=np.zeros(4*n+20))
        w._mound_contact_h=lambda dx:profile.contact(dx,w._mound_apex()) if profile else 0.
        fields=('sx','sy','svx','svy','shw','shh','sgd','srest','sstill','shas','saway','srw','srh','stag','sdt')
        for name in fields:setattr(w,name,np.full(4*n+20,-999.))
        w._s_grow=lambda need:None
        def append(*values):
            i=w._sn
            for name,value in zip(fields[:7],values):getattr(w,name)[i]=value
            for name in ('srest','sstill','shas','saway','stag','sdt'):getattr(w,name)[i]=0.
            w.srw[i],w.srh[i]=w.shw[i],w.shh[i]
            w._sn+=1;return i
        w._s_append=append
        emit=MethodType(emitter(source),w)
        def counted_emit(*args,**kwargs):
            counter['emitter_calls']+=1
            if w._splash_cap and w._sn>=w._splash_cap:counter['capped_emitter_calls']+=1
            return emit(*args,**kwargs)
        w._eject_splash=counted_emit
        w._record_contact=MethodType(ns['_record_contact'],w)
        w._first_contact_extra=MethodType(ns['_first_contact_extra'],w)
        ns['_replay_hits'](w,np.arange(n,dtype=int),np.linspace(0,.04,n),100.,2.,0.)
        rows=[getattr(w,name)[:w._sn].tolist() for name in fields]
        result=(w._sand_landed,w.flares,w._contact_hits,rows,w.sdt[:w._sn].tolist(),
                w._splash_stats,rng.getstate(),effects.getstate())
        return result,counter

    def test_hit_batches_preserve_published_particles_mass_and_both_rngs(self):
        old=historical(self.reference);current=(ROOT/'main.py').read_text(encoding='utf-8')
        for n in (0,1,32,800):
            for cap in (0,5):
                for first in (True,False):
                    with self.subTest(n=n,cap=cap,first=first):
                        self.assertEqual(self.run_replay(old,n,cap,first)[0],
                                         self.run_replay(current,n,cap,first)[0])

    def test_b13_first_impact_limits_spray_without_losing_mass_or_random_draws(self):
        from grain_style import B13_LIP_SPEED, B13_REFERENCE_RADIUS, B13_SPEED_GAIN
        src=(ROOT/'main.py').read_text(encoding='utf-8')
        for n in (1, 63, 64, 65, 800):
            reference, _ = self.run_replay(src, n)
            actual, _ = self.run_replay(src, n, renderer="b13")
            self.assertEqual(actual[0], reference[0])
            self.assertEqual(actual[-2:], reference[-2:])
            self.assertAlmostEqual(actual[2][0][2], (B13_LIP_SPEED + B13_SPEED_GAIN) * 220. / B13_REFERENCE_RADIUS)
            self.assertLess(actual[2][0][2], reference[2][0][2])

    def test_hit_batch_avoids_per_particle_numpy_scalar_reads(self):
        current=(ROOT/'main.py').read_text(encoding='utf-8')
        _,counts=self.run_replay(current,800)
        self.assertEqual(counts['scalar_reads'],0)

    def test_full_pool_skips_invisible_emitter_work(self):
        current=(ROOT/'main.py').read_text(encoding='utf-8')
        _,counts=self.run_replay(current,800,cap=5)
        self.assertEqual(counts['capped_emitter_calls'],0)

    def test_dense_hit_batch_avoids_per_grain_emitter(self):
        current=(ROOT/'main.py').read_text(encoding='utf-8')
        _,counts=self.run_replay(current,800)
        self.assertEqual(counts['emitter_calls'],0)

    def test_batch_boundary_geometry_rejections_and_reused_slots_match(self):
        old=historical(self.reference);current=(ROOT/'main.py').read_text(encoding='utf-8')
        for n in (63,64,65):
            for density in (.15,.7,1.5):
                for mode in ('normal','low-roof','blocked','no-profile'):
                    with self.subTest(n=n,density=density,mode=mode):
                        args=dict(density=density,mode=mode)
                        self.assertEqual(self.run_replay(old,n,**args)[0],self.run_replay(current,n,**args)[0])
        for initial in (4,5):
            self.assertEqual(self.run_replay(old,100,cap=5,initial=initial)[0],
                             self.run_replay(current,100,cap=5,initial=initial)[0])


if __name__=='__main__':unittest.main()
