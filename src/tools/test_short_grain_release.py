"""Negative controls for new visual goals and incomplete/frozen evidence."""
from copy import deepcopy
import unittest

from check_short_grain_release import require_capture, require_draining
from check_short_grain_visual import meets_short_grain_goal
from record_short_grain import validate_recording


class ReleaseTests(unittest.TestCase):
    def capture(self):
        hidden = dict(IsWindow=True, IsWindowVisible=False, IsIconic=False)
        return dict(completed=True, passed=True, source_unchanged=True,
                    hidden_at_creation=hidden, hidden_at_finish=hidden,
                    cases=[dict(completed=True,passed=True)], normalized_cases=[dict(frame_offsets=[0])],
                    frames=[dict(completed=True, passed=True, renderer="short_grain_field", native_hidden=hidden)])

    def test_good_capture(self):
        require_capture(self.capture(), 1, "short_grain_field")

    def test_timer_zero_still_animates_in_flight_grains(self):
        rows=[dict(case="draining",particles=100,stream_draw=dict(phase=88.0)) for _ in range(3)]
        with self.assertRaises(ValueError):require_draining(rows)
        rows[1]["stream_draw"]["phase"]=88.2
        require_draining(rows)

    def test_wrong_renderer(self):
        with self.assertRaises(ValueError):
            require_capture(self.capture(), 1, "line_pool")

    def test_cancelled_or_modified_capture(self):
        for k in ("completed", "passed", "source_unchanged"):
            r=self.capture(); r[k]=False
            with self.assertRaises(ValueError): require_capture(r, 1)

    def test_missing_or_failed_frame(self):
        with self.assertRaises(ValueError): require_capture(self.capture(), 2)
        r=self.capture(); r["frames"][0]["passed"]=False
        with self.assertRaises(ValueError): require_capture(r, 1)

    def test_visible_minimized_or_nonwindow(self):
        for k,v in (("IsWindow",False),("IsWindowVisible",True),("IsIconic",True)):
            r=deepcopy(self.capture());r["frames"][0]["native_hidden"][k]=v
            with self.assertRaises(ValueError): require_capture(r, 1)

    def test_long_lane_is_not_hidden_by_good_average_coverage(self):
        r=dict(applicable=True,coverage=.98,worst_row_coverage=.9,long_sparse_band=0,
               longest_vertical_hole=48,max_contact_hole=0,pitch=4)
        self.assertFalse(meets_short_grain_goal(r))
        r["longest_vertical_hole"]=4
        self.assertTrue(meets_short_grain_goal(r))

    def test_one_thread_cannot_hide_a_sparse_band(self):
        r=dict(applicable=True,coverage=.98,worst_row_coverage=.10,long_sparse_band=3,
               longest_vertical_hole=4,max_contact_hole=0,pitch=4)
        self.assertFalse(meets_short_grain_goal(r))

    def recording(self):
        rows=[dict(time=i/10,elapsed=min(i/10,50),running=i<500,sha256=str(i)) for i in range(601)]
        for row in rows:
            if 12.2<row["time"]<14.8: row.update(running=False,sha256="paused")
        return dict(frames=rows,duration=60,ended_settled=True)

    def test_completed_moving_recording(self):
        validate_recording(self.recording())

    def test_frozen_recorder_is_rejected(self):
        r=self.recording()
        for row in r["frames"]:row["sha256"]="same-frame"
        with self.assertRaises(ValueError):validate_recording(r)

    def test_missing_pause_or_unsettled_recording(self):
        for mode in ("pause","settle"):
            r=self.recording()
            if mode=="pause":
                for row in r["frames"]:row["running"]=True
            else:r["ended_settled"]=False
            with self.assertRaises(ValueError):validate_recording(r)

    def test_cancelled_or_reversed_recording(self):
        r=self.recording();r["frames"]=r["frames"][:200]
        with self.assertRaises(ValueError):validate_recording(r)
        r=self.recording();r["frames"][5]["time"]=0
        with self.assertRaises(ValueError):validate_recording(r)


if __name__=="__main__":unittest.main()
