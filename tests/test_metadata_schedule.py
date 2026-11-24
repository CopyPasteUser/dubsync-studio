"""Metadata prefetch contracts without touching user media."""
from __future__ import annotations

import unittest
from unittest.mock import Mock

from dubsync.core.metadata_schedule import prefetch_missing


class MetadataScheduleTests(unittest.TestCase):
    def test_preserves_existing_and_deduplicates_missing(self):
        known = {"a": 1}
        probe = Mock(return_value=[2, 3])
        policy = Mock(return_value=True)
        prefetch_missing(["a", "b", "b", "c"], known, permit_parallel=policy,
                         probe_many=probe, cancelled=lambda: False)
        policy.assert_called_once_with(["b", "c"])
        probe.assert_called_once_with(["b", "c"])
        self.assertEqual(known, {"a": 1, "b": 2, "c": 3})

    def test_policy_denial_and_single_missing_leave_sequential_fallback(self):
        probe = Mock()
        for paths, allowed in ((["a", "b"], False), (["a"], True)):
            known = {}
            prefetch_missing(paths, known, permit_parallel=lambda _: allowed,
                             probe_many=probe, cancelled=lambda: False)
            self.assertEqual(known, {})
        probe.assert_not_called()

    def test_failure_and_short_result_do_not_publish_partial_cache(self):
        for probe in (Mock(side_effect=RuntimeError("probe failed")), Mock(return_value=[1])):
            known = {}
            with self.assertRaises((RuntimeError, ValueError)):
                prefetch_missing(["a", "b"], known, permit_parallel=lambda _: True,
                                 probe_many=probe, cancelled=lambda: False)
            self.assertEqual(known, {})

    def test_cancellation_during_probe_does_not_publish(self):
        known = {}
        cancel = Mock(side_effect=[False, True])
        prefetch_missing(["a", "b"], known, permit_parallel=lambda _: True,
                         probe_many=lambda _: [1, 2], cancelled=cancel)
        self.assertEqual(known, {})
