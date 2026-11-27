"""Recorded hourglass regressions in reference-glass pixels."""
import ast
from functools import lru_cache
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


@lru_cache(maxsize=2)
def recorded_opening_geometry(period=50.):
    # Reconstruct the captured 540 × 924 throat from its quadratic wall.
    # These settings share an outlet but have different shoulder geometry.
    top_width, top_y, control_y, pole_y, bottom, credited = {
        20.: (89.29063421052629, 507.9251479844329, 489.4006074173106, 487.8747559685502,
              905.5641243896027, 88),
        50.: (79.9724462039688, 508.1002156835395, 488.0903127707635, 492.18170898956424,
              909.8710774106169, 93),
        600.: (55.37374710901672, 502.6910759947, 489.98053742984723, 495.21632562262215,
               912.9056940436748, 94),
    }[period]
    radius = 208.84468421052628
    scale = radius / R
    half = inner_width(period, 2. * radius) / (2. * scale)
    u = np.linspace(0., 1., 25)
    widths = half * scale + (top_width - half * scale) * (1. - u)**2
    heights = top_y * (1. - u)**2 + 2. * control_y * u * (1. - u) + 462. * u**2
    neck = SandNeck(radius, pole_y, list(zip(widths, heights)),
                    hourglass_transit(period, half), scale)
    return half, neck, scale, bottom, credited


def recorded_opening_frame(frame, surface=None, *, period=50., seed=97):
    half, neck, scale, bottom, credited = recorded_opening_geometry(period)
    floor = (bottom - 462.) / scale
    gap = floor if surface is None else (surface - 462.) / scale
    time = frame / 60.
    grains = sample_grains(
        time, period, half, gap, max_depth=floor, inlet_lift=neck.opening(time)[2] / scale,
        opening_seed=seed, first_exit_lift=neck.first_exit_lift / scale, floor_depth=floor,
        neck=neck, landed=frame >= credited,
    )
    return grains, half, gap


def cohort_depths(grains, cohort):
    ids, _, indices = np.intersect1d(cohort, grains.serial, return_indices=True)
    np.testing.assert_array_equal(ids, cohort)
    return grains.depth[indices]


class RecordedFlowTests(unittest.TestCase):
    def test_contact_speed_is_the_reference_through_steady_flow_and_cutoff(self):
        # Normal 540x924 PC glass. Measure the same IDs over each step at
        # fixed heights. The independent targets come from d867a16 at 1.517s.
        half, neck, scale, bottom, _ = recorded_opening_geometry()
        floor = (bottom - 462.) / scale
        def frame(t):
            return sample_grains(t, 50., half, floor, floor_depth=floor, neck=neck,
                                 inlet_lift=neck.opening(t)[2]/scale,
                                 first_exit_lift=neck.first_exit_lift/scale, opening_seed=97)
        for t in (1.517, 1.55, 1.8, 4.6, 8., 11., 49.98, 50.02):
            a, b = frame(t), frame(t+1/120)
            _, i, j = np.intersect1d(a.serial, b.serial, return_indices=True)
            y = scale*a.depth[i]
            speed = (b.depth[j] - a.depth[i])*scale*120.
            for depth, target in ((95., 206.), (255., 422.), (295., 485.)):
                band = (abs(y-depth) <= 5.) & (abs(a.x[i]*scale) < 18.)
                with self.subTest(t=t, depth=depth):
                    self.assertGreater(band.sum(), 10)
                    self.assertAlmostEqual(np.median(speed[band]), target, delta=.08*target)

    def test_filled_outlet_starts_flowing_on_the_first_tick(self):
        initial = recorded_opening_frame(0)[0]
        first = recorded_opening_frame(1)[0]
        self.assertEqual(len(initial.serial), 0)
        self.assertGreater(np.count_nonzero(first.depth > 0.), 0)
        self.assertLess(max(first.depth), 4.)

    def test_first_pass_uses_the_contact_speed_at_each_height(self):
        targets = ((95., 206.), (255., 422.), (295., 485.))
        unseen = dict(targets)
        previous = recorded_opening_frame(0)[0]
        for frame in range(1, 94):
            current = recorded_opening_frame(frame)[0]
            _, i, j = np.intersect1d(previous.serial, current.serial, return_indices=True)
            for height, target in list(unseen.items()):
                band = (abs(previous.depth[i]-height) <= 5.) & (abs(previous.x[i]) < 18.)
                if band.sum() >= 20:
                    speed = (current.depth[j[band]]-previous.depth[i[band]])*60.
                    self.assertAlmostEqual(np.median(speed), target, delta=.08*target)
                    del unseen[height]
            previous = current
            if not unseen:
                break
        self.assertEqual(unseen, {})

    def test_continuous_feed_has_no_travelling_low_flux_layer(self):
        # In the seed 97 recording at 2.433 s, the sparse layer is 164 px
        # below the outlet. Compare disk area passing each short section
        # per unit time; increasing speed alone cannot produce a flux hole.
        for frame, center in ((138, 128.), (146, 164.), (154, 207.)):
            grains = recorded_opening_frame(frame)[0]
            following = recorded_opening_frame(frame+1)[0]
            _, i, j = np.intersect1d(grains.serial, following.serial, return_indices=True)
            depth = grains.depth[i]
            speed = (following.depth[j]-depth)*60.
            flux = [np.sum((grains.radius[i]**2 * speed)[abs(depth-z) < 3.5])
                    for z in (center-15., center, center+15.)]
            self.assertGreater(flux[1], .8*min(flux[0],flux[2]), (frame,flux))

    def test_preimpact_waist_does_not_recur_in_disk_area(self):
        # The six recordings showed a denser band below a thinner middle.
        # Sum projected disk area per unit length independent of lateral spread.
        for seed in (97, 54, 271):
            differences = []
            for frame in range(81, 93):
                grains, half, _ = recorded_opening_frame(frame, seed=seed)
                bounds = grains.depth.max() * np.array([.60, .65, .75, .80])
                area = np.histogram(grains.depth, bounds, weights=grains.radius**2)[0]
                differences.append((area[2] - area[0]) / (bounds[1] - bounds[0]))
            with self.subTest(seed=seed):
                self.assertLessEqual(np.median(differences), 0.)

    def test_wide_throat_releases_a_connected_first_layer(self):
        fractions = []
        for frame in range(1, 90):
            grains, half, _ = recorded_opening_frame(frame, period=20.)
            central = abs(grains.x) < .48 * half
            tip = grains.depth[central].max(initial=0.)
            if tip > half:
                break
            if .65*half <= tip <= half:
                near = central & (grains.depth > tip-9.) & (grains.depth < tip-3.)
                fractions.append(np.pi * np.sum(grains.radius[near]**2) / (6. * .96 * half))
        self.assertGreaterEqual(len(fractions), 2)
        # Disk area must at least cover the footprint before projection and
        # occlusion. The paired native render verifies actual covered pixels.
        self.assertGreaterEqual(min(fractions), 1.)

    def test_opening_waist_cannot_move_to_another_airborne_section(self):
        # 82ab8b4 corrected the two measured sections but opened a much
        # larger void upstream. Follow all bands through impact and reception.
        for frame in (85, 92, 108):
            grains, half, floor = recorded_opening_frame(frame)
            bins = np.arange(0., min(floor, grains.depth.max()) - half, 20.)
            core = abs(grains.x) < .45 * half
            area = np.histogram(grains.depth[core], bins, weights=grains.radius[core]**2)[0]
            neighbours = np.minimum(area[:-4], area[4:])
            with self.subTest(frame=frame):
                self.assertTrue(np.all(area[2:-2] >= .65 * neighbours))

    def test_first_packet_does_not_keep_a_narrow_spike(self):
        for seed in (54, 271, 97):
            protrusions = []
            for frame in range(44, 57):
                grains, half, _ = recorded_opening_frame(frame, seed=seed)
                # Find the leading grain and both neighbouring bands, a quarter
                # aperture radius away. Either neighbour can support the front.
                y = grains.depth + grains.radius
                dx = grains.x - grains.x[y.argmax()]
                left = (dx > -.35 * half) & (dx < -.17 * half)
                right = (dx > .17 * half) & (dx < .35 * half)
                ahead = y.max() - max(y[left].max(initial=0.), y[right].max(initial=0.))
                protrusions.append(ahead)
            with self.subTest(seed=seed):
                self.assertLess(np.median(protrusions), .17 * half)

    def test_symmetric_round_nose_requires_both_visible_contours(self):
        from check_recorded_flow import symmetric_nose
        x = np.linspace(-.7, .7, 45)
        y = np.arange(120)[:, None]
        curve = 85. - 50.*x*x
        smooth = y <= curve
        self.assertTrue(symmetric_nose(x, smooth*255, 32., 209.))
        fringe = y <= curve + 8.*np.sin(18.*x)
        opacity = 255*smooth + 70*(fringe & ~smooth)
        self.assertFalse(symmetric_nose(x, opacity, 32., 209.))
        self.assertFalse(symmetric_nose(x, (y <= 60.+0.*x)*255, 32., 209.))
        self.assertFalse(symmetric_nose(x, smooth*0, 32., 209.))

    def test_flat_nose_requires_a_level_edge_across_both_shoulders(self):
        from check_recorded_flow import flat_nose

        x = np.linspace(-.7, .7, 45)
        y = np.arange(150)[:, None]
        for pitch in (1., 2.):
            with self.subTest(pitch=pitch):
                rough_cut = (y <= 65. + pitch*np.sin(31.*x))*255
                rough_cut[92, 22] = 255  # An outrider cannot conceal a flat body.
                self.assertTrue(flat_nose(x, rough_cut, pitch))
                irregular_edge = (y <= 65. + pitch*(5.*np.sin(7.*x) + 3.*np.cos(16.*x)))*255
                self.assertFalse(flat_nose(x, irregular_edge, pitch))
                narrow_cut = rough_cut*(abs(x) < .4)
                self.assertFalse(flat_nose(x, narrow_cut, pitch))

    def test_flat_opening_is_rejected_only_when_a_level_cut_persists(self):
        from check_recorded_flow import check_flat_opening, flat_nose

        x = np.linspace(-.7, .7, 45)
        y = np.arange(140)[:, None]
        times = np.arange(18)/60.
        frames = [(y <= 20. + i*4. + np.sin(17.*x))*255 for i in range(len(times))]
        samples = [(t, flat_nose(x, frame, 1.)) for t, frame in zip(times, frames)]
        with self.assertRaisesRegex(ValueError, "持续呈平切"):
            check_flat_opening(samples)
        samples[8] = (times[8], False)
        check_flat_opening(samples)

    @staticmethod
    def opening_fixture(depth, relief, time, hole=None, loose=0):
        from check_recorded_flow import opening_edge_samples

        background = (234, 243, 248)
        geometry = dict(cx=40, outlet_y=10, surface_y=96, half_width=16., background_rgb=background)
        rgb = np.full((100, 80, 3), background, dtype=np.uint8)
        for x, height in zip(range(25, 55), 10 + depth + relief):
            rgb[10:height+1, x] = (247, 193, 95)
            if loose:
                rgb[height-loose + x % 2:height:2, x] = background
        if hole is not None:
            rgb[hole] = background
        return dict(opening_edge_samples(rgb, geometry, 1.), time=time)

    def test_opening_reference_finds_a_shoulder_patch_inside_the_local_edge(self):
        from check_recorded_flow import opening_reference_failures

        x = np.arange(-15, 15)
        early_times = .55 + np.arange(5)/60.
        later_times = .83 + np.arange(7)/60.
        # An uneven edge has open air below some columns and sand below
        # others in the same row. It cannot create an interior patch.
        early_relief = x % 4
        late_relief = -np.rint(14. * (x / 15.)**2).astype(int)
        later = [self.opening_fixture(52, late_relief, t) for t in later_times]
        reference = [self.opening_fixture(16, early_relief, t) for t in early_times] + later
        self.assertEqual(reference[0]["coverage"], [0., 0., 0.])
        positive, failures = opening_reference_failures(reference, reference, 16., 1., 60)
        self.assertEqual(positive, {"early": 5, "fringe": 7})
        self.assertEqual(failures, [])
        hole = (slice(17, 21), slice(50, 54))
        candidate = [self.opening_fixture(16, early_relief, t, hole) for t in early_times] + later
        _, failures = opening_reference_failures(reference, candidate, 16., 1., 60)
        self.assertEqual(len(failures), 1)
        self.assertIn("右肩沙层内连片露底", failures[0])

    def test_opening_reference_uses_fall_distance_and_requires_sustained_fringe_flatness(self):
        from check_recorded_flow import opening_reference_failures

        x = np.arange(-15, 15)
        early = [self.opening_fixture(16, x % 4, .55+i/60.) for i in range(5)]
        curved = -np.rint(16.*(x / 15.)**2).astype(int)
        flat = x % 3 - 1
        reference = early + [self.opening_fixture(52+i, curved, .77+i/60.) for i in range(7)]
        # A delay alone must pass. The visual stages are matched by distance.
        delayed = early + [dict(frame, time=frame["time"]+.16) for frame in reference[5:]]
        self.assertEqual(opening_reference_failures(reference, delayed, 16., 1., 60)[1], [])
        first = delayed[5]["time"]
        candidate = early + [self.opening_fixture(48+i, flat, first+i/60.) for i in range(7)]
        _, failures = opening_reference_failures(reference, candidate, 16., 1., 60)
        self.assertEqual(len(failures), 1)
        self.assertIn("散粒外沿持续横齐", failures[0])
        candidate[8] = delayed[8]
        self.assertEqual(opening_reference_failures(reference, candidate, 16., 1., 60)[1], [])
        with self.assertRaisesRegex(ValueError, "未覆盖"):
            opening_reference_failures(reference, candidate[:-2], 16., 1., 60)

    def test_opening_reference_rejects_dense_tip_even_with_matching_outer_shape(self):
        from check_recorded_flow import opening_reference_failures

        x = np.arange(-15, 15)
        early = [self.opening_fixture(16, x % 4, .55+i/60.) for i in range(5)]
        relief = -np.rint(16.*(x / 15.)**2).astype(int)
        reference = early + [self.opening_fixture(52+i, relief, .83+i/60., loose=16) for i in range(7)]
        actual = early + [self.opening_fixture(52+i, relief, .83+i/60., loose=3) for i in range(7)]
        _, failures = opening_reference_failures(reference, actual, 16., 1., 60)
        self.assertEqual(len(failures), 1)
        self.assertIn("渐散带持续太短", failures[0])

    def test_initial_charge_disks_rest_on_real_contacts(self):
        from opening_packing import settled_section
        rng = np.random.default_rng(97)
        radii = np.array([.55, .69, .81, .93])
        width = 9.
        charge = settled_section(width, 18., radii, rng.integers(4, size=800), rng.random(800))
        self.assertGreater(len(charge), 100)
        for i, (x, y, r, key) in enumerate(charge):
            self.assertEqual(r, radii[key])
            self.assertLessEqual(abs(x) + r, width + 1.e-8)
            self.assertGreaterEqual(y, r - 1.e-8)
            left = abs(x + width - r) < 1.e-8
            right = abs(x - width + r) < 1.e-8
            for px, py, pr, _ in charge[:i]:
                gap = np.hypot(x-px, y-py) - (r+pr)
                self.assertGreaterEqual(gap, -1.e-8)
                if abs(gap) < 1.e-8:
                    left |= px <= x
                    right |= px >= x
            self.assertTrue(abs(y-r) < 1.e-8 or (left and right))

    def test_opening_face_does_not_repeat_the_same_outline_in_every_packing(self):
        # 540 x 924 native glass, 50 s. Compare at the same falling distance.
        half, flow = glass(50.)
        from b13_stream import _opening_material, _birth_material
        pole = 492.18170898956424
        heights = np.linspace(462., 508.1, 25)
        widths = half + 48.27 * ((heights - heights[0]) / np.ptp(heights))**2
        neck = SandNeck(R, pole, list(zip(widths[::-1], heights[::-1])), flow, 1.)
        floor = 447.87
        args = 50., half, floor, neck.first_exit_lift
        profiles = []
        for seed in (17, 54, 97, 271):
            opening = _opening_material(*args, 1., seed, neck)
            birth = opening.crossings
            material = _birth_material()
            lane = (material[1] + material[9])[opening.keys]
            profile = np.array([birth[abs(lane - centre) < .08].min()
                                for centre in np.linspace(-.8, .8, 11)])
            profiles.append(opening.entry * (profile - birth.min()))
        # The troublesome cone had the same shoulders and tip to within
        # about one grain diameter, even after repacking the entire charge.
        self.assertGreater(np.median(np.ptp(profiles, axis=0)), 1.6)

    def test_initial_gaps_are_measured_behind_the_exposed_surface(self):
        from check_recorded_flow import early_packed_coverage
        mask = np.zeros((100, 100), dtype=bool)
        mask[:28, 28:72] = True
        mask[28:31, 38:62:2] = True
        self.assertGreaterEqual(early_packed_coverage(mask, 50, 0, 100, 31., 1.), .95)
        mask[22:28, 35:65:2] = False
        self.assertLess(early_packed_coverage(mask, 50, 0, 100, 31., 1.), 2./3.)
        self.assertIsNone(early_packed_coverage(np.zeros_like(mask), 50, 0, 100, 31., 1.))

    def test_released_grains_keep_their_vertical_order_in_free_flight(self):
        half, _ = glass(50.)
        for seed in (54, 97, 271):
            first = sample_grains(.6, 50., half, 448., opening_seed=seed)
            near = first.depth >= first.depth.max()-12.
            cohort = first.serial[near][np.argsort(first.depth[near], kind="stable")]
            self.assertGreater(len(cohort), 20)
            for t in (.65, .70, .75, .8, 1.):
                now = sample_grains(t, 50., half, 448., opening_seed=seed)
                by_id = np.argsort(now.serial)
                indices = by_id[np.searchsorted(now.serial[by_id], cohort)]
                np.testing.assert_array_equal(now.serial[indices], cohort)
                with self.subTest(seed=seed, time=t):
                    self.assertGreaterEqual(np.diff(now.depth[indices]).min(), -1.e-8)

    def test_all_airborne_grains_keep_their_order_until_first_contact(self):
        # Follow survivors, including the previous rear, and all new arrivals.
        # A stationary leader alone cannot detect exchanges inside the stream.
        for period in (1., 5., 15., 50., 6000.):
            half, flow = glass(period)
            floor = 448.
            neck = SandNeck(R, 478., [(half+20., 488.), (half, 458.), (half, 448.)],
                            flow, 1.)
            previous = None
            comparisons = 0
            for time in np.arange(1, 241)/120.:
                grains = sample_grains(time, period, half, floor, opening_seed=97,
                                       floor_depth=floor, first_exit_lift=neck.first_exit_lift,
                                       inlet_lift=neck.opening(time)[2], neck=neck)
                if not len(grains.serial):
                    continue
                if max(grains.depth) >= floor-2.:
                    break
                if previous is not None:
                    with self.subTest(period=period, time=time):
                        ids, a, b = np.intersect1d(previous.serial, grains.serial, return_indices=True)
                        self.assertEqual(len(ids), len(previous.serial))
                        order = np.argsort(previous.depth[a], kind="stable")
                        self.assertTrue(np.all(np.diff(grains.depth[b[order]]) >= -1.e-9))
                        new = ~np.isin(grains.serial, ids)
                        if new.any():
                            self.assertLessEqual(grains.depth[new].max(), grains.depth[b].min()+1.e-9)
                        comparisons += len(ids)
                previous = grains
            self.assertGreater(comparisons, 0, period)

    def test_narrow_neck_preserves_release_order_at_the_end_of_opening(self):
        # The 600-second PC replay first hit the bed at frame 93. Frame
        # 92 is still airborne, including particles less than 2 px down.
        before, _, _ = recorded_opening_frame(91, period=600.)
        after, _, _ = recorded_opening_frame(92, period=600.)
        earlier = before.depth > 0.
        later = after.depth > 0.
        ids, a, b = np.intersect1d(before.serial[earlier], after.serial[later], return_indices=True)
        self.assertGreater(len(ids), 100)
        self.assertEqual(len(ids), int(earlier.sum()))
        prior, ongoing = before.depth[earlier][a], after.depth[later][b]
        order = np.argsort(prior, kind="stable")
        strict_order = np.diff(prior[order]) > 1.e-8
        new = later & ~np.isin(after.serial, before.serial[earlier], assume_unique=True)
        self.assertTrue(new.any())
        with self.subTest("存续颗粒"):
            self.assertGreaterEqual(
                np.diff(ongoing[order])[strict_order].min(), -1.e-8,
                "600 秒档种子 97 在 1.517～1.533 秒出颈颗粒发生纵向换位",
            )
        with self.subTest("新出颈颗粒"):
            self.assertLessEqual(
                after.depth[new].max(), ongoing.min()+1.e-8,
                "600 秒档种子 97 在 1.533 秒新出颈颗粒越过在途旧粒",
            )

    def test_wide_mouth_can_carry_the_selected_volume_at_its_measured_speed(self):
        from b13_stream import _discharge
        for duration in (1., 5.):
            half, _ = glass(duration)
            flow = _discharge(duration, half, 433., 0., 1., 97, None)
            for depth in (25., 95., 255.):
                in_flight = np.interp(depth, flow.depth, flow.clock)/duration
                # Upper bound even if this entire length were densely filled.
                capacity = depth * half**2 / (4.*R**3/3.)
                with self.subTest(duration=duration, depth=depth):
                    self.assertLessEqual(in_flight, capacity)

    def test_recording_a_deposit_does_not_switch_airborne_motion(self):
        half, neck, scale, bottom, _ = recorded_opening_geometry()
        floor = (bottom-462.)/scale
        for time in (1.53, 1.55, 1.60, 4.8, 50.1):
            for stride in (1, 3):
                kw = dict(floor_depth=floor, opening_seed=97, neck=neck,
                          first_exit_lift=neck.first_exit_lift/scale, stride=stride)
                before = sample_grains(time, 50., half, floor, landed=False, **kw)
                after = sample_grains(time, 50., half, floor, landed=True, **kw)
                for x, y in zip(before, after):
                    np.testing.assert_array_equal(x, y)

    def test_same_airborne_layer_does_not_split_into_two_speeds_at_first_deposit(self):
        bottom = 909.8710774106169
        surface = {
            91: bottom, 92: bottom, 93: 906.8279599923823, 97: 896.4578399303778,
            98: 895.3311344280246, 99: 893.7996168335685,
        }

        def sample_frame(frame):
            return recorded_opening_frame(frame, surface[frame])[0]

        initial = sample_frame(91)
        layer = (initial.depth >= 290.) & (initial.depth < 295.)
        cohort = np.sort(initial.serial[layer])
        self.assertGreaterEqual(len(cohort), 40)

        def velocities(start, end):
            positions = []
            for frame in (start, end):
                grains, _, gap = recorded_opening_frame(frame, surface[frame])
                z = cohort_depths(grains, cohort)
                self.assertLess(float(z.max()), gap-20.)
                positions.append(z)
            # Both old source types have been replaced by one feed. Compare
            # the fastest and slowest fixed IDs, including every former lane.
            speed = (positions[1]-positions[0])*60./(end-start)
            return speed.min(), speed.max()

        # Include the frame that first credits the pile. Clipping that frame
        # out of the opening audit misses the upstream grains' handover.
        before, impact = sample_frame(92), sample_frame(93)
        survivors, prev, now = np.intersect1d(before.serial, impact.serial, return_indices=True)
        self.assertGreater(len(survivors), 1000)
        order = np.argsort(before.depth[prev], kind="stable")
        self.assertGreaterEqual(np.diff(impact.depth[now][order]).min(), -1.e-8)
        for start, end in ((91, 92), (97, 98), (98, 99)):
            with self.subTest(start=start, end=end):
                speed = velocities(start, end)
                self.assertGreater(min(speed), 0.)
                self.assertLessEqual(
                    max(speed) / min(speed), 1.5,
                    f"{start/60.:.3f}～{end/60.:.3f} 秒同一空中沙层分成两种速度 {speed[0]:.1f}、{speed[1]:.1f} 像素/秒",
                )

    def test_dense_front_keeps_moving_as_the_first_fringe_opens(self):
        from b13_raster import GrainImage
        from check_recorded_flow import gold, opening_edges

        def edges(frame):
            grains, half, _ = recorded_opening_frame(frame)
            # Use the application's CPU grain renderer for a deterministic
            # optical check. Native GPU footage separately supplies the verdict.
            width, height, outlet = 84, 131, 126
            image = GrainImage((-width//2, 0), (width, height), (0., outlet, 1., half))
            image.paint(grains, np.zeros(width+1), np.array([215., 161., 95.])/255., outlet)
            rgba = image.rgba[::-1].astype(float)
            alpha = rgba[:, :, 3:4] / 255.
            background = np.array([234., 243., 248.])
            rgb = np.rint(rgba[:, :, :3]*alpha + background*(1.-alpha)).astype(np.uint8)
            return np.array(opening_edges(gold(rgb), width//2, height-outlet, height, half))

        half = recorded_opening_geometry()[0]
        samples = {k: edges(k) for k in range(30, 58)}
        observed = 0
        stalls = []
        for frame in range(30, 55):
            start, end = samples[frame], samples[frame+3]
            if not .7*half <= start[1] <= 3.*half:
                continue
            observed += 1
            dense, fringe = end - start
            if dense <= 2. and fringe >= 8.:
                stalls.append((round(frame/60., 3), float(dense), float(fringe)))
        self.assertGreaterEqual(observed, 3, "未覆盖散粒离口后的连续三个 0.05 秒窗")
        self.assertFalse(stalls, f"密实边停滞而散粒继续下落，各窗起点（秒）、下移量（像素）：{stalls}")

    def test_middle_of_the_first_packet_does_not_brake_then_catch_up(self):
        first, _, _ = recorded_opening_frame(36)
        lo, hi = np.quantile(first.depth, [.45, .55])
        cohort = np.sort(first.serial[(first.depth >= lo) & (first.depth <= hi)])
        self.assertGreater(len(cohort), 100)
        samples = [cohort_depths(recorded_opening_frame(frame)[0], cohort) for frame in range(36, 61, 3)]
        speeds = np.median(np.diff(samples, axis=0), axis=1) / .05
        for prior, valley, recovery in zip(speeds, speeds[2:], speeds[4:]):
            self.assertFalse(
                valley < .80 * prior and recovery > 1.25 * valley,
                f"空中中排固定编号颗粒减速再追赶，三窗中位速度 {prior:.1f}、{valley:.1f}、{recovery:.1f} 像素/秒",
            )

    def test_first_contact_does_not_brake_an_airborne_layer_far_from_the_bed(self):
        surface = {
            90: 909.8710774106169, 91: 909.8710774106169,
            92: 909.8710774106169, 93: 906.8279599923823, 95: 900.1558835557064,
            97: 896.4578399303778, 99: 893.7996168335685, 101: 891.7761942512401,
            103: 889.6913344681395, 105: 887.9754013370507,
        }
        samples = {k: recorded_opening_frame(k, height) for k, height in surface.items()}
        initial = samples[91][0]
        compared = 0
        for depth in (240., 290.):
            cohort = np.sort(initial.serial[(initial.depth >= depth) & (initial.depth < depth+5.)])
            self.assertGreaterEqual(len(cohort), 20)
            positions = {}
            for frame, (grains, half, gap) in samples.items():
                ids, _, indices = np.intersect1d(cohort, grains.serial, return_indices=True)
                if len(ids) < len(cohort) or (grains.depth[indices] + grains.radius[indices]).max() >= gap-half:
                    break
                positions[frame] = grains.depth[indices]
            windows = [(a, b) for a, b in ((90, 92), (93, 95), (95, 97), (97, 99),
                                          (99, 101), (101, 103), (103, 105))
                       if a in positions and b in positions]
            if len(windows) < 3 or windows[0] != (90, 92):
                continue
            compared += 1
            speeds = [
                float(np.median(positions[b]-positions[a])) * 60. / (b-a)
                for a, b in windows
            ]
            self.assertGreater(speeds[0], 0.)
            self.assertGreaterEqual(
                min(speeds[1:]), .75 * max(speeds[:2]),
                f"距口 {depth:g} 像素层随首批触底减速，仍距堆至少一倍颈口半宽，"
                f"各窗中位速度 {np.round(speeds, 1)} 像素/秒",
            )
        self.assertGreater(compared, 0, "未覆盖触底前及触底后两个远离堆面的速度窗")

    def test_neighbour_spacing_follows_the_release_interval_and_common_gravity(self):
        from b13_stream import _opening_material
        half, flow = glass(50.)
        floor = 448.
        neck = SandNeck(R, 478., [(half+20., 488.), (half, 458.), (half, 448.)], flow, 1.)
        lift = neck.first_exit_lift
        opening = _opening_material(50., half, floor, lift, 1., 97, neck)
        entry, gravity = opening.entry, opening.gravity
        self.assertGreater(gravity, 0.)
        earlier = sample_grains(.60, 50., half, floor, first_exit_lift=lift,
                                opening_seed=97, floor_depth=floor, neck=neck)
        later = sample_grains(.75, 50., half, floor, first_exit_lift=lift,
                              opening_seed=97, floor_depth=floor, neck=neck)
        ids, a, b = np.intersect1d(earlier.serial, later.serial, return_indices=True)
        self.assertGreater(len(ids), 100)
        matched, i, _ = np.intersect1d(opening.material.serial, ids, return_indices=True)
        np.testing.assert_array_equal(matched, ids)
        birth = opening.birth[i]
        age = .60 - birth
        np.testing.assert_allclose(earlier.depth[a], entry*age+.5*gravity*age**2)
        gains = np.diff(later.depth[b])-np.diff(earlier.depth[a])
        np.testing.assert_allclose(gains, -.15*gravity*np.diff(birth), atol=1e-10)

    def test_dense_opening_face_cannot_hang_while_loose_grains_keep_falling(self):
        from check_recorded_flow import check_opening_braking, opening_edges
        for fps in (30, 60, 120):
            times = np.arange(fps + 1) / fps
            smooth, hanging = [], []
            for t in times:
                body = round(14 + 48*t + 36*t*t)
                fringe = body + round(5 + 6*t)
                for rows, dense in ((smooth, body), (hanging, min(35, body))):
                    mask = np.zeros((128, 32), dtype=bool)
                    mask[:dense, :] = True
                    mask[dense:fringe, ::3] = True
                    rows.append((t, *opening_edges(mask, 16, 0, 128, 16)))
            check_opening_braking(smooth, 209.)
            with self.assertRaisesRegex(ValueError, "沙团停滞"):
                check_opening_braking(hanging, 209.)
            stopped = [(t, 20., 28.) for t in times]
            with self.assertRaisesRegex(ValueError, "沙团停滞"):
                check_opening_braking(stopped, 209.)
            # Both edges remain strictly increasing, but both lose half their speed.
            braked = [(t, 12.+80*min(t,.4)+40*max(0.,t-.4),
                        18.+90*min(t,.4)+45*max(0.,t-.4)) for t in times]
            with self.assertRaisesRegex(ValueError, "明显减速"):
                check_opening_braking(braked, 209.)
        with self.assertRaisesRegex(ValueError, "不足"):
            check_opening_braking(smooth[::30], 209.)

    def test_same_opening_grains_do_not_brake_after_leaving_the_neck(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from b13_stream import install

        class Widget:
            def _build_dynamic_canvas(self):
                pass

            def _draw_stream(self):
                raise AssertionError("未走首落颗粒绘制入口")

        install(Widget)
        w = Widget()
        half, flow = glass(50.)
        w._cx, w._R_inner, w._neck_y, w._lower_sand_bot = 0., R, 448., 0.
        w._taper = {"t_in": half, "y_bot": 448.}
        w._sand_neck = SandNeck(R, 478., [(half+20., 488.), (half, 458.), (half, 448.)], flow, 1.)
        w.get_mound_top_y = lambda: 0.
        w._b13_mesh = SimpleNamespace(surface_x=np.array([0.]), bowl=np.array([0.]), update=Mock())
        w._mound_carve = SimpleNamespace(_v=[])
        w.duration, w._stream_settle_age, w._sand_active, w.pn = 50., 0., True, 1
        w.sand_base = (.84, .64, .38)
        with patch.object(sys.modules[__name__], "SAND_MATERIAL_COARSE", 1.8, create=True):
            for seed in (54, 97, 271):
                with self.subTest(seed=seed):
                    w._opening_seed = seed
                    positions = []
                    for tick in (33, 36, 39, 42, 45, 48):
                        w.elapsed = tick / 60.
                        w._draw_stream()
                        grains = w._b13_mesh.update.call_args.args[0]
                        if not positions:
                            near = (grains.depth >= grains.depth.max()-.38*half) & (abs(grains.x) < .5*half)
                            cohort = np.sort(grains.serial[near])
                            self.assertGreater(len(cohort), 20)
                        same, _, indices = np.intersect1d(cohort, grains.serial, return_indices=True)
                        np.testing.assert_array_equal(same, cohort)
                        positions.append(grains.depth[indices])
                    early, middle, late = [np.median(positions[i+1]-positions[i]) for i in (0, 2, 4)]
                    self.assertFalse(max(middle, late) < .70*early and early-max(middle, late) > 2.,
                                     f"同一批 {len(cohort)} 粒沙每 0.05 秒的中位下移量"
                                     f"从 {early:.1f} 降至 {middle:.1f}、{late:.1f} 像素")

    def test_early_leaders_stay_with_the_next_layers(self):
        half, flow = glass(50.)
        # Test both an already full neck and the initially empty reference glass.
        neck = SandNeck(R, 478., [(half+20., 488.), (half, 458.), (half, 448.)], flow, 1.)
        for lift in (0., neck.first_exit_lift):
            for seed in (54, 97, 271):
                early, later = [sample_grains(flow.age_at(distance), 50., half, 450.,
                                             opening_seed=seed, first_exit_lift=lift) for distance in (2*half, 4*half)]
                _, i, j = np.intersect1d(early.serial, later.serial, return_indices=True)
                behind = early.depth.max() - early.depth[i]
                central = np.abs(early.x[i]) < .65*half
                leaders = central & (behind < .25*half)
                following = central & (behind >= .75*half) & (behind < half)
                self.assertGreater(min(leaders.sum(), following.sum()), 10)
                advance = later.depth[j] - early.depth[i]
                # Over one outlet diameter of fall, the front quarter of this layer
                # must not gain another half-radius on the back quarter.
                with self.subTest(seed=seed, first_exit_lift=lift):
                    self.assertLess(np.median(advance[leaders]) - np.median(advance[following]), .5*half)

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

    def test_opening_spacing_keeps_evolving_in_the_second_half_of_the_drop(self):
        # Follow the same grains. Compare spacing gains over equal fall distances.
        for period in (20., 50.):
            half, flow = glass(period)
            for seed in (54, 97, 271):
                widths = []
                for reach in (65., 140., 290., 365.):
                    grains = sample_grains(flow.age_at(reach), period, half, 460., opening_seed=seed)
                    if not widths:
                        near = (grains.depth > grains.depth.max() - .60*half) & (abs(grains.x) < .60*half)
                        tracked = grains.serial[near]
                    depths = grains.depth[np.isin(grains.serial, tracked)]
                    self.assertEqual(len(depths), len(tracked))
                    widths.append(np.ptp(np.percentile(depths, [10, 90])))
                early, late = widths[1] - widths[0], widths[3] - widths[2]
                with self.subTest(period=period, seed=seed):
                    self.assertGreater(early, 0.)
                    self.assertGreater(late, early / 3., widths)

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

    def test_packed_lip_cannot_have_a_seam_after_release(self):
        from check_recorded_flow import check_filled_lip_seam
        g = dict(cx=20., outlet_y=12., half_width=8.)
        mask = np.ones((24,40), dtype=bool)
        check_filled_lip_seam(mask, g)
        mask[10,:] = False
        with self.assertRaisesRegex(ValueError, "亮线"):
            check_filled_lip_seam(mask, g)
        # Open air at the leading front does not form a sand/sand seam.
        mask[11:] = False
        check_filled_lip_seam(mask, g)

    def test_primed_inventory_needs_a_full_throat_and_full_crown(self):
        from check_recorded_flow import primed_reservoir
        g = dict(cx=60., radius=50., crown_y=5., outlet_y=120.)
        y, x = np.ogrid[:140, :120]
        dx = abs(x+.5-60.)
        circle = (y+.5-55.)**2 + dx**2 <= 50.**2
        packed = circle | ((y >= 55) & (y < 120) & (dx <= 12.))
        extra = float(((packed & ~circle).sum(axis=0)*dx).sum())
        frac = extra/(4.*50.**3/3.)
        height = 100.*(.5-np.sin(np.arcsin(1.-2.*frac)/3.))
        rgb = np.full((140,120,3), (234,243,248), np.uint8)
        for row in range(55,120):
            cols = np.flatnonzero(packed[row])
            rgb[row, cols[0]-2:cols[0]] = rgb[row,cols[-1]+1:cols[-1]+3] = (90,103,105)
        rgb[packed & (y+.5 >= 5.+height)] = (219,163,90)
        with self.assertRaisesRegex(ValueError, "顶部空腔"):
            primed_reservoir(rgb,g)
        rgb[circle] = (219,163,90)
        primed_reservoir(rgb,g)
        empty = rgb.copy()
        empty[119,49:71] = (234,243,248)
        with self.assertRaisesRegex(ValueError, "填颈不连续"):
            primed_reservoir(empty,g)

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

    def test_initial_charge_fills_the_throat_from_the_reservoir_and_discharges_from_the_first_step(self):
        from test_splash33_impulse import method
        source = (ROOT / 'main.py').read_text(encoding='utf-8')
        names = ('_transfer_timing', '_released_fraction_at', '_upper_sand_fraction',
                 '_upper_sand_height_px', '_sphere_fill_fraction', '_neck_fill_time', '_supply_elapsed')
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
            self.assertAlmostEqual(w._upper_sand_height_px(), 2*R)
            initial_share = w._upper_sand_fraction()
            self.assertAlmostEqual(w._sand_neck.charged_volume_ratio * initial_share,
                                   1. - w._sand_neck.cap_fraction)
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

    def test_mass_carriers_and_visible_grains_use_the_contact_clock(self):
        from b13_stream import _discharge
        for period in (1., 5., 20., 50., 600., 6000.):
            half, initial = glass(period)
            model = _discharge(period, half, 433., 0., 1., 97, None)
            age = np.interp([5., 95., 255., 295.], model.depth, model.clock)
            cutoff = 2.
            time = cutoff + age.max() + .1
            birth = time-age
            y, vy = model.mass_motion(initial, cutoff, time, age, np.zeros(4), np.zeros(4), 17., 2., birth)
            np.testing.assert_allclose(y, 17.-2.*model.depth_at(age))
            np.testing.assert_allclose(vy, -2.*np.interp(age, model.clock, model.velocity))
            for k in range(4):
                scalar = model.mass_motion(initial, cutoff, time, age[k], .9, .4, 17., 2., birth[k])
                np.testing.assert_allclose(scalar, [y[k], vy[k]])

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
