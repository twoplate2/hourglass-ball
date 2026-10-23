"""Exercise the app's actual flare-retention expression without starting Kivy."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT=Path(__file__).resolve().parents[2]


def retain(flares,clock,surface):
    tree=ast.parse((ROOT/'main.py').read_text(encoding='utf-8'))
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='HourglassWidget')
    fn=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='update_particles')
    rows=[n for n in ast.walk(fn) if isinstance(n,ast.Assign) and any(
        isinstance(t,ast.Attribute) and isinstance(t.value,ast.Name) and
        t.value.id=='self' and t.attr=='flares' for t in n.targets)]
    if len(rows)!=1:raise AssertionError('Flare retention must have one actual assignment')
    widget=SimpleNamespace(flares=flares,_mound_flow_clock=clock,_mound_top_at=surface)
    return eval(compile(ast.Expression(rows[0].value),'main.py:flare_retention','eval'),dict(self=widget))


class FlareOcclusionTests(unittest.TestCase):
    def test_old_live_flare_is_hidden_by_new_sand(self):
        flash=dict(x=2,y=100,end=1.08)
        self.assertEqual(retain([flash],1.01,lambda x:110),[])

    def test_surface_flash_remains_at_its_real_contact(self):
        flash=dict(x=2,y=100,end=1.08)
        self.assertEqual(retain([flash],1.01,lambda x:100),[flash])
        self.assertEqual(flash,dict(x=2,y=100,end=1.08))

    def test_partial_visible_tip_is_not_removed_early(self):
        flash=dict(x=2,y=100,end=1.08)
        self.assertEqual(retain([flash],1.04,lambda x:100.45),[flash])
        self.assertEqual(retain([flash],1.04,lambda x:100.55),[])

    def test_expired_flashes_are_still_removed(self):
        flash=dict(x=2,y=100,end=1.08)
        self.assertEqual(retain([flash],1.08,lambda x:99),[])

    def test_visible_downhill_corner_survives_center_burial(self):
        flash=dict(x=0,y=100,end=1.08)
        self.assertEqual(retain([flash],1.04,lambda x:101-x),[flash])


if __name__=='__main__':unittest.main()
