import unittest

from sportscli.tools import evaluate_ads

try:
    import numpy as np
    import sklearn  # noqa: F401
    from sportscli.tools import train_clip_ad_model as clip
except ImportError:  # the training extras are optional developer tools
    clip = None


class ClipLabelTests(unittest.TestCase):
    def test_every_trained_label_is_a_known_label(self):
        self.assertIn('arena', evaluate_ads.LABELS)
        if clip: self.assertLessEqual(set(clip.KEEP), set(evaluate_ads.LABELS))


@unittest.skipUnless(clip, 'numpy and scikit-learn are optional training extras')
class ClipHeadTests(unittest.TestCase):
    def test_heads_mute_ads_and_arena_shows_but_keep_coverage(self):
        rng = np.random.default_rng(0)
        centers = {label: rng.normal(size=16) for label in ('broadcast', 'studio', 'bench', 'overlay', 'ad', 'arena')}
        labels = [label for label in centers for _ in range(40)]
        features = np.stack([centers[l] + rng.normal(scale=.3, size=16) for l in labels]).astype('float32')
        heads = clip.fit([{'features': features, 'labels': labels}])
        scores = dict(zip(labels, clip.scores(heads, features)))
        for label in ('broadcast', 'studio', 'bench', 'overlay'): self.assertGreater(scores[label], .8, label)
        for label in ('ad', 'arena'): self.assertLess(scores[label], .2, label)

    def test_fitting_needs_both_ads_and_coverage(self):
        features = np.zeros((4, 8), 'float32')
        with self.assertRaises(SystemExit):
            clip.fit([{'features': features, 'labels': ['broadcast'] * 4}])


if __name__ == '__main__':
    unittest.main()
