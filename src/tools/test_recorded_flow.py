"""Recorded hourglass regressions in reference-glass pixels."""
import ast
from pathlib import Path
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/app"))
from b13_stream import sample_grains
from grain_style import B13_REFERENCE_RADIUS as R, hourglass_transit
from neck_geometry import SandNeck, inner_width


def glass(period):
    half = inner_width(period, 2 * R) / 2
    return half, hourglass_transit(period, half)


class RecordedFlowTests(unittest.TestCase):
    def test_opening_gradient_checks_the_back_of_the_head_and_each_shoulder(self):
        from check_recorded_flow import check_head_transition

        holes = np.tile([.72, .55, .34, .15, .07, .03], (6, 3, 1))
        check_head_transition(holes, 60)
        dense = holes.copy()
        dense[:, 0, 3:] = .02
        with self.assertRaisesRegex(ValueError, "中央过早变实"):
            check_head_transition(dense, 60)
        for side in (1, 2):
            reversed_edge = holes.copy()
            reversed_edge[1:4, side, 4] = .25
            with self.assertRaisesRegex(ValueError, "持续由密返稀"):
                check_head_transition(reversed_edge, 60)

    def test_first_deposit_follows_visible_contact_and_spreads_the_initial_rise(self):
        from check_recorded_flow import first_deposit

        def frames(heights, landed_at=2):
            return [dict(time=i/60., geometry=dict(surface_y=900.-height, half_width=80.),
                         transfer=dict(landed=max(0., (i-landed_at+1)/1000.)))
                    for i, height in enumerate(heights)]

        contact = 2/60.
        gradual = [0, 0, 8, 29, 50, 63, 70, 75, 80]
        first_deposit(frames(gradual), contact, 1., 60)
        for abrupt in ([0, 0, 50, 69], [0, 0, 2, 52]):
            with self.assertRaisesRegex(ValueError, "首堆突然升高"):
                first_deposit(frames(abrupt + gradual[4:]), contact, 1., 60)
        with self.assertRaisesRegex(ValueError, "相差超过一帧"):
            first_deposit(frames(gradual, landed_at=5), contact, 1., 60)

    def test_free_head_is_porous_across_its_width_and_has_no_persistent_round_cap(self):
        from check_recorded_flow import airborne_head_section, check_airborne_head
        y, x = np.ogrid[:160, -55:56]
        geo = dict(cx=55, outlet_y=10, surface_y=158, half_width=40, background_rgb=[234,243,248])
        bg = np.array(geo["background_rgb"])
        sand = np.array([216,162,96])

        def section(length, curvature):
            edge = 115 - curvature*(x/40.)**2
            opacity = np.clip((edge-y)/length, 0., 1.)
            opacity[132,55] = 1.   # An extra grain in front cannot open up the head.
            return airborne_head_section(np.uint8(bg + opacity[:,:,None]*(sand-bg)), geo)

        loose = section(30, 4)
        for cap in (section(4, 45), section(32, 54)):
            with self.assertRaisesRegex(ValueError, "实心圆帽"):
                check_airborne_head([cap]*5 + [loose], 40)
        check_airborne_head([loose]*5 + [section(4, 45)], 40)
        with self.assertRaisesRegex(ValueError, "散成雾"):
            check_airborne_head([section(68, 0)]*5, 40)

    def test_width_behind_the_loose_grains_is_compared_to_the_same_jet(self):
        from check_recorded_flow import head_width_ratio
        y, x = np.ogrid[:220, -65:66]
        geo = dict(cx=65, outlet_y=8, half_width=40, background_rgb=[234,243,248])
        normal = (abs(x) < 30) & (y < 190)
        bulge = (abs(x) < 38) & (y > 121) & (y < 158)
        for mask, expected in ((normal, 1.), (normal | bulge, 75/59)):
            rgb = np.uint8(np.where(mask[:,:,None], [216,162,96], geo["background_rgb"]))
            self.assertAlmostEqual(head_width_ratio(rgb, geo, 190), expected)

    def test_first_gaps_open_at_the_lower_face_on_both_sides(self):
        from check_recorded_flow import head_shoulder_density, check_head_release_order
        geo = dict(cx=85., half_width=40., outlet_y=8., surface_y=260.)
        y, x = np.ogrid[:270, -85:86]
        forward, reversed_right, single_frame, narrow_only = [], [], [], []
        for tip in (155, 161, 168, 175, 182, 189):
            behind = (tip-y)/40.
            normal = (abs(x) < 36) & (behind >= 0)
            shoulder = abs(x) >= 22
            pores = (abs(x)*7 + (tip-y)*3) % 11 < 5
            loose_face = normal & ~(pores & (behind < 1.))
            bad = np.where(x < 0, loose_face,
                           normal & ~(shoulder & pores & ((behind < .30) | ((1 < behind) & (behind < 1.55)))))
            forward.append(head_shoulder_density(loose_face, geo))
            reversed_right.append(head_shoulder_density(bad, geo))
            single_frame.append(reversed_right[-1] if tip == 175 else forward[-1])
            narrow_only.append(head_shoulder_density(normal & ~(shoulder & (behind < 1.)), geo))
        self.assertGreater(check_head_release_order(forward, 40.), 25.)
        check_head_release_order(single_frame, 40.)
        with self.assertRaisesRegex(ValueError, "上方两侧先露出空隙"):
            check_head_release_order(reversed_right, 40.)
        with self.assertRaisesRegex(ValueError, "前端内部仍挤在一起"):
            check_head_release_order(narrow_only, 40.)

    def test_timer_draws_one_seed_per_new_run_including_after_completion(self):
        from types import MethodType
        from unittest.mock import Mock
        from test_ui_polish import method

        entropy = Mock()
        entropy.urandom.side_effect = [n.to_bytes(8, "little") for n in (17, 94, 271)]
        clock = Mock()
        clock.perf_counter.return_value = 3.
        toggle = method("HourglassWidget", "toggle", {"os": entropy, "time": clock})
        reset = method("HourglassWidget", "reset")
        state_reset = method("HourglassWidget", "_reset_run_state", {"gc": Mock()})
        w = Mock(duration=15., elapsed=0., running=False, _geom_ready=False, _opening_seed=None,
                 _splash_stats={}, _completion_token=0)
        w._reset_run_state = MethodType(state_reset, w)
        toggle(w)
        self.assertEqual(w._opening_seed, 17)
        toggle(w)  # Pause and resume the same round.
        toggle(w)
        self.assertEqual(w._opening_seed, 17)
        reset(w)
        toggle(w)
        self.assertEqual(w._opening_seed, 94)
        w.elapsed, w.running = w.duration, False
        toggle(w)
        self.assertEqual(w._opening_seed, 271)
        self.assertEqual(entropy.urandom.call_count, 3)

    def test_opening_seed_varies_the_first_packet_and_replays_it_exactly(self):
        half, flow = glass(15.)
        t = flow.age_at(305.)
        def grains(time, seed):
            return sample_grains(time, 15., half, 433., opening_seed=seed)

        first = grains(t, 17)
        second = grains(t, 94)
        self.assertFalse(np.array_equal(first.x, second.x) and np.array_equal(first.depth, second.depth))
        for a, b in zip(first, grains(t, 17)):
            np.testing.assert_array_equal(a, b)
        for a, b in zip(grains(4., 17), grains(4., 94)):
            np.testing.assert_array_equal(a, b)

    def test_a_narrow_leading_tuft_must_give_way_during_the_drop(self):
        from check_recorded_flow import persistent_leading_spike
        tips = np.arange(165., 310., 4.)
        shapes = np.tile(np.zeros(13), (len(tips), 1))
        shapes[:, 5:7] = 19.
        with self.assertRaisesRegex(ValueError, "持续成尖"):
            persistent_leading_spike(tips, shapes+tips[:, None], 50., 205.)
        shapes[6:] = 0.
        persistent_leading_spike(tips, shapes+tips[:, None], 50., 205.)

    def test_nearby_leading_grains_slip_past_one_another_during_the_fall(self):
        half, flow = glass(15.)
        for reach in (240., 290.):
            a, b = (sample_grains(flow.age_at(z), 15., half, 455.) for z in (reach, reach+75.))
            _, i, j = np.intersect1d(a.serial, b.serial, return_indices=True)
            leading = (a.depth.max()-a.depth[i] < .72*half) & (abs(a.x[i]) < .60*half)
            motion = b.depth[j[leading]] - a.depth[i[leading]]
            with self.subTest(reach=reach):
                self.assertGreater(len(motion), 100)
                self.assertGreater(motion.min(), 0.)
                self.assertGreater(np.ptp(np.percentile(motion, (10, 90))), .105*half)

    def test_fifty_second_free_face_changes_inside_the_leading_tuft(self):
        half, flow = glass(50.)
        for seed in (54, 97, 271):
            for reach in (190., 240., 290.):
                with self.subTest(seed=seed, reach=reach):
                    a, b = (sample_grains(flow.age_at(z), 50., half, 455., opening_seed=seed)
                            for z in (reach, reach+75.))
                    _, i, j = np.intersect1d(a.serial, b.serial, return_indices=True)
                    near = (a.depth.max()-a.depth[i] < .72*half) & (abs(a.x[i]) < .60*half)
                    movement = b.depth[j[near]] - a.depth[i[near]]
                    self.assertGreater(len(movement), 40)
                    # Several grain diameters of slip across the bulk of the tuft.
                    self.assertGreater(np.ptp(np.percentile(movement, (10, 90))), .16*half)

    def test_first_grain_stays_visible_after_crossing_the_lip(self):
        from b13_stream import HEAD_ID_START
        half, _ = glass(15.)
        heights = []
        for tick in (11, 12, 13):
            grains = sample_grains(tick/120., 15., half, 450., opening_seed=33)
            first = grains.depth[grains.serial == HEAD_ID_START]
            self.assertEqual(len(first), 1, f"首粒在第 {tick} 步消失")
            heights.append(first[0])
        self.assertTrue(np.all(np.diff(heights) > 0))

    def test_opening_gaps_preserve_downward_motion_in_the_head_and_following_grains(self):
        for period in (1., 5., 15.):
            half, flow = glass(period)
            for depth in (100., 165., 240., 330.):
                t = flow.age_at(depth)
                a, b = (sample_grains(t+dt, period, half, 450., opening_seed=33)
                        for dt in (0., 1/120.))
                _, i, j = np.intersect1d(a.serial, b.serial, return_indices=True)
                with self.subTest(period=period, depth=depth):
                    self.assertGreater(len(i), 100)
                    self.assertGreaterEqual((b.depth[j] - a.depth[i]).min(), -1.e-8)

    def test_first_packet_advances_through_the_fifty_second_jet(self):
        half, _ = glass(50.)
        for seed in (54, 97, 271):
            with self.subTest(seed=seed):
                previous = sample_grains(.8, 50., half, 440., opening_seed=seed)
                for tick in range(97, 187):
                    current = sample_grains(tick/120., 50., half, 440., opening_seed=seed)
                    _, i, j = np.intersect1d(previous.serial, current.serial, return_indices=True)
                    airborne = previous.depth[i] > 4.
                    self.assertGreater(airborne.sum(), 100)
                    step = current.depth[j[airborne]] - previous.depth[i[airborne]]
                    self.assertGreater(step.min(), 0., f"seed={seed}, frame={tick}")
                    previous = current

    def test_tip_profile_rejects_both_a_rigid_cap_and_frame_to_frame_noise(self):
        from check_recorded_flow import leading_deformation
        x = np.linspace(-1., 1., 13)
        travel = np.arange(0., 240., 3.)
        rigid = travel[:, None] + 8.*np.cos(3.*x)
        with self.assertRaisesRegex(ValueError, "整块平移"):
            leading_deformation(travel, rigid, 200.)
        noise = np.random.default_rng(17).normal(0., 7., rigid.shape)
        with self.assertRaisesRegex(ValueError, "短间隔"):
            leading_deformation(travel, rigid + noise, 200.)
        moving = rigid + 8.*np.sin(6.*x + travel[:, None]/45.)
        self.assertGreater(leading_deformation(travel, moving, 200.), 3.)

    def test_landing_height_cannot_change_the_stream_far_above_the_pile(self):
        half, flow = glass(50.)
        t = flow.age_at(435.)
        low_pile = sample_grains(t, 50., half, 500.)
        high_pile = sample_grains(t, 50., half, 330.)
        _, i, j = np.intersect1d(low_pile.serial, high_pile.serial, return_indices=True)
        middle = (low_pile.depth[i] > 100.) & (low_pile.depth[i] < 280.)
        self.assertGreater(middle.sum(), 100)
        np.testing.assert_array_equal(low_pile.x[i[middle]], high_pile.x[j[middle]])

    def test_first_grains_thin_out_towards_the_leading_edge(self):
        for period in (5., 15., 50.):
            half, flow = glass(period)
            for reach in (180., 275., 355.):
                grains = sample_grains(flow.age_at(reach), period, half, 455.)
                core = grains.depth[abs(grains.x) < .40*half]
                behind = (core.max()-core)/half
                # Compare equal-length bands, following the moving front.
                edge = np.count_nonzero((behind >= .10) & (behind < .35))
                body = np.count_nonzero((behind >= .85) & (behind < 1.35)) / 2.
                with self.subTest(period=period, reach=reach):
                    self.assertGreater(edge, 0)
                    self.assertLess(edge, .85*body)

    def test_leading_grains_travel_with_the_first_visible_front(self):
        for period in (1., 5., 15., 50., 6000.):
            half, flow = glass(period)
            t0, t1 = (flow.age_at(z) for z in (155., 225.))
            a = sample_grains(t0, period, half, 460.)
            b = sample_grains(t1, period, half, 460.)
            _, i, j = np.intersect1d(a.serial, b.serial, return_indices=True)
            behind = a.depth.max() - a.depth[i]
            band = (behind < max(7., .30*half)) & (abs(a.x[i]) < .7*half)
            with self.subTest(period=period):
                self.assertGreater(band.sum(), 8)
                movement = np.median(b.depth[j[band]] - a.depth[i[band]])
                self.assertAlmostEqual(movement / (b.depth.max() - a.depth.max()), 1., delta=.24)

    def test_stable_acceleration_remains_visible_in_the_lower_half(self):
        half, _ = glass(15.)
        a = sample_grains(3., 15., half, 433.)
        b = sample_grains(3.04, 15., half, 433.)
        _, i, j = np.intersect1d(a.serial, b.serial, return_indices=True)
        speeds = (b.depth[j] - a.depth[i]) / .04
        values = [np.median(speeds[(abs(a.depth[i]-z) < 4) & (abs(a.x[i]) < .4*half)])
                  for z in (10., 73., 136., 199.)]
        increments = np.diff(values)
        self.assertGreater(increments.min(), .55 * increments.max(), values)
        # Check the fall distance the viewer actually has before the pile rises.
        # 3.38 kept its increments even, but only gained 21% across these rows.
        self.assertGreater(values[1] / values[0], 1.17, values)
        self.assertGreater(values[3] / values[0], 1.55, values)

    def test_flat_last_layer_and_contact_follow_the_inner_wall(self):
        from check_recorded_flow import closing_surface, shoulder_gap
        mask = np.zeros((100,100), dtype=bool)
        mask[51:] = True
        mask[38:51,40:61] = True
        with self.assertRaisesRegex(ValueError, "台阶"):
            closing_surface(mask, 50, 50, 24)
        mask[38:51] = False
        mask[50, 48:58] = True
        closing_surface(mask, 50, 50, 24)
        with_slot = mask.copy()
        with_slot[52:69, 69:79] = False
        with self.assertRaisesRegex(ValueError, "空槽"):
            shoulder_gap(with_slot, mask, 50, 50, 24, 100)
        shoulder_gap(mask, mask, 50, 50, 24, 100)

    def test_resting_bulbs_are_mirrored_circles_with_an_empty_neck(self):
        from check_recorded_flow import sphere_endpoint
        y, x = np.ogrid[:240, :120]
        geometry = dict(cx=60., radius=50., crown_y=5., surface_y=235., outlet_y=120.)
        for upper, cy in ((True, 55.), (False, 185.)):
            mask = (x+.5-60.)**2 + (y+.5-cy)**2 <= 50.**2
            def image():
                return np.where(mask[:,:,None], [217,166,96], [234,243,248]).astype(np.uint8)
            self.assertLessEqual(sphere_endpoint(image(), geometry, upper), 1.)
            if upper:
                mask[97:117, 52:69] = True
            else:
                mask[123:143, 52:69] = True
            with self.assertRaisesRegex(ValueError, "偏离圆弧"):
                sphere_endpoint(image(), geometry, upper)

    def test_initial_sand_reaches_the_crown_and_discharges_from_the_first_step(self):
        from test_splash33_impulse import method
        source = (ROOT / 'main.py').read_text(encoding='utf-8')
        names = ('_transfer_timing', '_released_fraction_at', '_upper_sand_fraction',
                 '_upper_sand_height_px', '_neck_fill_time', '_supply_elapsed')
        widget = ast.ClassDef(name='Glass', bases=[], keywords=[], decorator_list=[],
                              body=[method(source, name) for name in names])
        ns = {'FLOW_RENDERER': 'b13'}
        exec(compile(ast.fix_missing_locations(ast.Module([widget], [])), 'main.py', 'exec'), ns)
        w = ns['Glass']()
        w._geom_ready, w._R_inner, w._raw_height_ratio = True, R, lambda volume: volume
        for period in (1., 5., 15., 20., 50., 6000.):
            w.duration, w._sand_timing, w.elapsed = period, None, 0.
            half, transit = glass(period)
            shoulder = half + 30.
            join_y = 99. + R - np.sqrt(R**2-shoulder**2)
            w._sand_neck = SandNeck(R, 99., [(shoulder, join_y), (half, 89.), (half, 80.)],
                                    transit, 1.)
            self.assertAlmostEqual(w._upper_sand_height_px(), 2 * R)
            self.assertLess(w._upper_sand_fraction(1/120), w._upper_sand_fraction(0))
            w.elapsed = period
            self.assertEqual(w._upper_sand_height_px(), 0.)

    def test_first_contact_has_visible_travel_time_and_fits_the_opening(self):
        for period, earliest, latest in ((1, .145, .25), (5, .32, .60),
                                        (15, .80, 1.55), (20, 1.20, 1.60),
                                        (50, 1.20, 1.60), (6000, 1.20, 1.60)):
            with self.subTest(period=period):
                half, flow = glass(period)
                hit = flow.age_at(433.) / flow.lane_factor(0., 1.)
                self.assertGreaterEqual(hit, earliest)
                self.assertLessEqual(hit, latest)
                self.assertLessEqual(hit / period, half**2 * 433. / (4 * R**3 / 3))
                for depth in (20., 70., 180., 320.):
                    self.assertLess(flow.age_at(depth), hit)
                    self.assertAlmostEqual(flow.depth_at(flow.age_at(depth)), depth, delta=.05)

    def test_pigment_has_a_common_entry_and_bounded_downstream_speed(self):
        for period in (1., 5., 15., 50., 6000.):
            half, _ = glass(period)
            t = min(.7 * period, 3.)
            a = sample_grains(t, period, half, 433.)
            b = sample_grains(t+.02, period, half, 433.)
            _, i, j = np.intersect1d(a.serial, b.serial, return_indices=True)
            speeds = (b.depth[j] - a.depth[i]) / .02
            medians = []
            for z in (3., 100., 365.):
                band = (abs(a.depth[i]-z) < 3) & (abs(a.x[i]) < .65*half)
                self.assertGreater(np.count_nonzero(band), 5)
                medians.append(np.median(speeds[band]))
            top, middle, bottom = medians
            self.assertTrue(76. <= top <= 83., medians)
            self.assertTrue(1.08 * top < middle < bottom, medians)
            low, high = (1.20, 1.47) if period == 1. else (1.75, 2.20)
            self.assertTrue(low * top < bottom < high * top, (period, medians))

    def test_first_ten_pixels_keep_the_previous_release_motion(self):
        from b13_stream import _pigment_depth
        age = np.arange(8) / 60.
        expected = 80.*age + .5*9.25*age**2
        for period in (1., 2., 5., 15., 50., 6000.):
            np.testing.assert_allclose(_pigment_depth(age, period), expected, rtol=0., atol=1.e-10)

    def test_final_flow_stays_connected_while_the_mouth_is_still_supplied(self):
        half, _ = glass(15.)
        grains = sample_grains(15.-1/60., 15., half, 32.)
        central = abs(grains.x) < .55*half

        def coverage(lo, hi):
            band = central & (grains.depth >= lo) & (grains.depth < hi)
            return np.sum(grains.radius[band]**2) / (hi-lo)

        # Both bands are still airborne. The 3.38 breakup left a pale stripe
        # above a second dense patch in the frame at 14.983 s.
        self.assertGreater(coverage(4., 13.), .85*coverage(17., 24.))

    def test_last_airborne_grains_survive_the_first_frame_after_zero(self):
        half, _ = glass(15.)
        at_zero = sample_grains(15., 15., half, 32.)
        next_frame = sample_grains(15. + 1/60., 15., half, 32.)
        _, i, j = np.intersect1d(at_zero.serial, next_frame.serial, return_indices=True)
        near_lip = (at_zero.depth < 6.) & (abs(at_zero.x) < .65*half)
        self.assertGreater(near_lip.sum(), 100)
        self.assertEqual(near_lip[i].sum(), near_lip.sum())
        self.assertTrue(np.isin(next_frame.serial, at_zero.serial).all())
        np.testing.assert_array_equal(at_zero.radius[i], next_frame.radius[j])
        advance = next_frame.depth[j[near_lip[i]]] - at_zero.depth[i[near_lip[i]]]
        self.assertTrue(np.all(advance > 0.))
        self.assertLess(np.median(advance), 9.)
        previous = at_zero
        for elapsed in np.arange(1, 13)/120.:
            frame = sample_grains(15.+elapsed, 15., half, max(25., 32.-75.*elapsed))
            _, old, new = np.intersect1d(previous.serial, frame.serial, return_indices=True)
            self.assertEqual(len(new), len(frame.serial))
            self.assertTrue(np.all(frame.depth[new] > previous.depth[old]))
            np.testing.assert_array_equal(frame.radius[new], previous.radius[old])
            previous = frame
        self.assertFalse(len(previous.serial))

    def test_cutoff_check_detects_a_jump_on_either_of_the_first_two_frames(self):
        from check_recorded_flow import closing_step_ratio
        times = 15. + np.arange(-6, 6)/60.
        packed = (times[-1]-times)*240.
        self.assertAlmostEqual(closing_step_ratio(list(zip(times, packed)), 15.), 1.)
        for start in (1/60., 2/60.):
            jumped = packed - np.where(times >= 15.+start, 11., 0.)
            with self.assertRaisesRegex(ValueError, "相邻帧"):
                closing_step_ratio(list(zip(times, jumped)), 15.)

    def test_dense_flow_reaches_both_edges_immediately_below_the_lip(self):
        for period in (1., 5., 15., 50.):
            half, _ = glass(period)
            positions = []
            for t in np.arange(.35, .70, .025):
                grains = sample_grains(t, period, half, 220.)
                near = (grains.depth < 3.) & (grains.depth > .2)
                positions.extend(grains.x[near] / half)
            left, right = np.percentile(positions, [5, 95])
            self.assertLess(left, -.84, (period, left))
            self.assertGreater(right, .84, (period, right))

    def test_rising_impact_carries_only_exposed_outward_grains_and_respects_glass(self):
        from types import SimpleNamespace
        from splash_motion import follow_rising_impact

        w = SimpleNamespace(_R_inner=100., _lower_y_c=100., _lower_sand_bot=0.,
                            _taper={'t_in': 30.}, _cx=0., _sn=6)
        w.sx = np.array([-24., 24., 26., 24., 1., 98.])
        w.sy = np.array([103., 103., 103., 79., 103., 103.])
        w.svx = np.array([-20., 20., -20., 20., 20., 20.])
        w.srest = np.array([0., 0., 1., 0., 0., 0.])
        w.sgd = np.ones(6)
        w.shas = np.zeros(6)
        w.shw = w.shh = np.ones(6)
        old_x, old_y = w.sx.copy(), w.sy.copy()
        table = np.full(129, 105.)
        follow_rising_impact(w, .01, 1/60, table, .64)
        np.testing.assert_array_equal(w.sx, old_x)
        follow_rising_impact(w, 4., 1/60, table, .64)
        self.assertLess(w.sx[0], old_x[0])
        self.assertGreater(w.sx[1], old_x[1])
        self.assertTrue(np.all(w.sy[:2] > old_y[:2]))
        np.testing.assert_array_equal(w.sy[2:5], old_y[2:5])
        np.testing.assert_array_equal(w.sx[2:5], old_x[2:5])
        self.assertLess(np.hypot(w.sx[-1], w.sy[-1]-100), 100.)

    def test_rebinding_a_new_grain_material_also_changes_the_crest_and_surface_colours(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        source = (ROOT / 'main.py').read_text(encoding='utf-8')
        widget = next(node for node in ast.parse(source).body if getattr(node, 'name', '') == 'HourglassWidget')
        methods = [node for node in widget.body if getattr(node, 'name', '') in
                   ('rebind_sand_material', '_bind_surface_colours')]
        ns = {}
        exec(compile(ast.Module(methods, []), 'main.py', 'exec'), ns)
        w = SimpleNamespace(_sand_chords=[], _neck_quads=Mock(), _contact_grains=Mock(),
                            _neck_solid_rect=Mock(), _neck_fade_rect=Mock(), _neck_color=Mock(),
                            _neck_solid_color=Mock(), _neck_fade_color=Mock(), _render_colors=None,
                            _stream_pools={}, _upper_band_color=Mock(), _mound_band_color=Mock(),
                            _splash_color=Mock(), _dust_color=Mock(), redraw=Mock(),
                            sand_base=(.2,.35,.6), sand_dark=(.1,.2,.3), sand_light=(.4,.5,.7))
        w._bind_surface_colours = ns['_bind_surface_colours'].__get__(w)
        material = Mock()
        ns['rebind_sand_material'](w, material)
        w._contact_grains.set_texture.assert_called_once_with(material.texture)
        self.assertEqual(w._upper_band_color.rgb, w.sand_light)
        self.assertEqual(w._mound_band_color.rgb, w.sand_light)
        self.assertEqual(w._splash_color.rgb, w.sand_light)
        self.assertEqual(w._dust_color.rgb, w.sand_light)

    def test_picture_measurements_detect_a_missing_cap_and_follow_actual_motion(self):
        from check_recorded_flow import crown_gap, motion_speed, splash_pixels

        rng = np.random.default_rng(336)
        rgb = np.full((50, 50, 3), (226, 239, 247), dtype=np.uint8)
        rgb[15:] = (215, 172, 107)
        self.assertEqual(crown_gap(rgb, dict(cx=25, crown_y=4)), 11)
        rgb[4:15] = (215, 172, 107)
        self.assertEqual(crown_gap(rgb, dict(cx=25, crown_y=4)), 0)
        a = rng.integers(90, 170, (120,90), dtype=np.uint8)
        for distance in (3, 5, 8):
            speed = motion_speed(a, np.roll(a, distance, axis=0), .1, 45, 30, 10, 100)
            self.assertAlmostEqual(speed, distance/.1, delta=.8)
        flat = np.ones_like(a)
        self.assertIsNone(motion_speed(flat, flat, .1, 45, 30, 10, 100))
        mask = np.zeros((100,100), dtype=bool)
        mask[80:] = True
        self.assertEqual(splash_pixels(mask, 10, 50, 10, 40), 0)
        mask[75, 30] = True
        self.assertEqual(splash_pixels(mask, 10, 50, 10, 40), 1)


if __name__ == "__main__":
    unittest.main()
