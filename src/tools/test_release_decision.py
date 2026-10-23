"""A withdrawn/pending review must block publishing despite old green evidence."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parent))
from release_decision import require_decision,require_committed_bytes


class DecisionTests(unittest.TestCase):
    def good(self):
        return dict(schema=1,version='3.8',status='approved',blockers=[],
                    source_sha256='a'*64,record='design/checks/records/v3.8/review.json')

    def test_approved_exact_source(self):
        require_decision(self.good(),'3.8','a'*64)

    def test_withdrawal_overrides_old_pass(self):
        for status in ('revoked','pending','failed'):
            value=self.good();value.update(status=status,old_appearance='passed')
            with self.assertRaises(ValueError):require_decision(value,'3.8','a'*64)

    def test_open_issue_blocks(self):
        value=self.good();value['blockers']=[dict(id='solid-rod',status='open')]
        with self.assertRaises(ValueError):require_decision(value,'3.8','a'*64)

    def test_different_source_or_version_blocks(self):
        for version,digest in [('3.7','a'*64),('3.8','b'*64)]:
            with self.assertRaises(ValueError):require_decision(self.good(),version,digest)

    def test_untracked_approval_blocks_commit_identity(self):
        def absent(name):raise KeyError(name)
        with self.assertRaisesRegex(ValueError,'committed'):
            require_committed_bytes({'decision.json':b'approved'},absent)

    def test_uncommitted_approval_blocks_commit_identity(self):
        with self.assertRaisesRegex(ValueError,'committed'):
            require_committed_bytes({'decision.json':b'approved'},lambda name:b'pending')

    def test_committed_approval_and_evidence_match(self):
        value={'decision.json':b'approved\n','evidence':b'123'}
        require_committed_bytes(value,value.__getitem__)


if __name__=='__main__':unittest.main()
