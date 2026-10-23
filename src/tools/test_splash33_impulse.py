"""The airborne emitter must retain the user's accepted 3.3 impulse and RNG."""
import ast
import copy
import math
from pathlib import Path
import random
import subprocess
from types import SimpleNamespace
import unittest


ROOT=Path(__file__).resolve().parents[2]
REFERENCE='ff3b721918a3c6b41428fbc4d26ddb14e430490e'
WEAKENED='c869e24ea54732cddaed22b1ead18989b288e33b'
PARAMETERS=('SPLASH_SPEED_LO','SPLASH_SPEED_HI','SPLASH_GAIN',
    'SPLASH_ANGLE_MIN','SPLASH_ANGLE_MAX','SPLASH_HORIZONTAL_ANGLE_LIMIT',
    'SPLASH_PX_BASE','SPLASH_SIZE_MIX','SPLASH_GRAV_LO','SPLASH_GRAV_HI',
    'SPLASH_LIFT_PX','SPLASH_G_SCALE')


def historical(ref):
    return subprocess.check_output(['git','show',ref+':main.py'],cwd=ROOT).decode('utf-8')


def method(source,name):
    cls=next(n for n in ast.parse(source).body if isinstance(n,ast.ClassDef)
             and n.name=='HourglassWidget')
    return next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name==name)


def parameter_nodes(source):
    return {target.id:node for node in ast.parse(source).body if isinstance(node,ast.Assign)
            for target in node.targets if isinstance(target,ast.Name) and target.id in PARAMETERS}


def emitter(source):
    import os
    nodes=list(parameter_nodes(source).values())+[copy.deepcopy(method(source,'_eject_splash'))]
    ns={'math':math,'os':os}
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),
                 'main.py:3.3-emitter-contract','exec'),ns)
    return ns['_eject_splash']


def representative_scale(source,period):
    # Fixed PC geometry fixture; use the application's actual period properties.
    names=('_neck_fill_time','_natural_flight_time','_fall_delay','_particle_motion_scale')
    cls=ast.parse('class Motion: pass').body[0]
    cls.body=[copy.deepcopy(method(source,name)) for name in names]
    constants=[node for node in ast.parse(source).body if isinstance(node,ast.Assign)
               and any(isinstance(t,ast.Name) and t.id in ('NECK_FILL','FALL_DELAY')
                       for t in node.targets)]
    ns={'math':math}
    exec(compile(ast.fix_missing_locations(ast.Module(body=constants+[cls],type_ignores=[])),
                 'main.py:period-scale-contract','exec'),ns)
    fixture=ns['Motion']()
    fixture.duration=period;fixture._geom_ready=True
    fixture._neck_y=500.;fixture._taper={'y_bot':520.};fixture._lower_sand_bot=25.
    return fixture._particle_motion_scale


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
    @classmethod
    def setUpClass(cls):
        cls.old=historical(REFERENCE)
        cls.current=(ROOT/'main.py').read_text(encoding='utf-8')

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
            scale=representative_scale(self.current,period)
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


if __name__=='__main__':unittest.main()
