import unittest
from sportscli.tools import train_ad_model as training


class TrainingSplitTests(unittest.TestCase):
    def test_all_excerpts_of_a_broadcast_stay_in_one_partition(self):
        rows = [{'broadcast': name} for name in ('a', 'a', 'b', 'c', 'c')]
        train, held = training.split(rows, ['a'])
        self.assertEqual(len(held), 2)
        self.assertFalse({r['broadcast'] for r in train} & {r['broadcast'] for r in held})

    def test_misspelled_holdout_cannot_silently_remove_evaluation(self):
        with self.assertRaisesRegex(ValueError, 'Unknown held-out'):
            training.split([{'broadcast': 'game'}], ['typo'])

    def test_studio_and_bench_train_as_coverage_but_report_separately(self):
        self.assertEqual({training.TARGETS[label] for label in ('studio', 'bench', 'broadcast')}, {1.0})
        rows = [{'positions': [0, 2, 4], 'labels': ['studio', 'bench', 'ad']}]
        counts = training.report(rows, lambda _: [1.0, 1.0, 0.0], 2)
        self.assertEqual((counts['studio']['samples'], counts['bench']['samples']), (1, 1))
        self.assertEqual(counts['studio']['muted'] + counts['bench']['muted'], 0)

    def test_arena_shows_train_as_mute_targets(self):
        self.assertEqual((training.TARGETS['arena'], training.TARGETS['ad']), (0.0, 0.0))
        rows = [{'positions': [0, 2, 4, 6], 'labels': ['broadcast', 'arena', 'arena', 'broadcast']}]
        counts = training.report(rows, lambda _: [1.0, 0.0, 0.0, 1.0], 2)
        self.assertEqual(counts['arena'], {'samples': 2, 'muted': 1})  # playback mutes after two low scores
