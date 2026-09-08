from __future__ import unicode_literals

import unittest

from resources.lib.planning import (
    FilePlanner,
    PlanningError,
    normalize_path,
    join_path,
    path_is_within,
    summarize_file_groups,
    tmdb_helper_cache_exclusions,
)


class FakeVfs:
    def __init__(self, directories, sizes):
        self.directories = {
            normalize_path(path): value for path, value in directories.items()
        }
        self.sizes = {
            normalize_path(path): value for path, value in sizes.items()
        }

    def exists(self, path):
        return normalize_path(path) in self.directories

    def listdir(self, path):
        return self.directories[normalize_path(path)]

    def fileSize(self, path):
        return self.sizes[normalize_path(path)]


class PathTests(unittest.TestCase):
    def test_normalize_preserves_scheme_and_collapses_separators(self):
        self.assertEqual(
            'special://profile/addon_data/example',
            normalize_path('special://profile//addon_data\\example/'))

    def test_parent_traversal_is_rejected(self):
        with self.assertRaises(PlanningError):
            normalize_path('/profile/addon_data/../Database')

    def test_join_preserves_empty_scheme_root(self):
        self.assertEqual('special://profile', join_path('special://', 'profile'))

    def test_component_match_does_not_match_similar_sibling(self):
        root = '/profile/addon_data/plugin.video.themoviedb.helper/blur_v3'
        self.assertTrue(path_is_within(root, root))
        self.assertTrue(path_is_within(root + '/image.png', root))
        self.assertFalse(path_is_within(root + '0/image.png', root))

    def test_matching_is_exact_case_by_default_to_fail_safe(self):
        self.assertFalse(path_is_within('/Profile/Blur_V3/a.png',
                                       '/profile/blur_v3'))
        self.assertTrue(path_is_within('/Profile/Blur_V3/a.png',
                                      '/profile/blur_v3',
                                      case_sensitive=False))


class TmdbHelperAdapterTests(unittest.TestCase):
    def test_adapter_has_only_approved_generated_directories(self):
        rules = tmdb_helper_cache_exclusions(
            'special://profile/addon_data/plugin.video.themoviedb.helper')
        self.assertEqual(
            ['blur_v3', 'crop_v2', 'desaturate_v2', 'colors_v2'],
            [rule['path'].rsplit('/', 1)[-1] for rule in rules])
        self.assertTrue(all(rule['adapter'] ==
                            'plugin.video.themoviedb.helper' for rule in rules))

    def test_custom_image_location_is_respected(self):
        rules = tmdb_helper_cache_exclusions('/media/TMDb Images/')
        self.assertEqual('/media/TMDb Images/blur_v3', rules[0]['path'])


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.root = '/profile/addon_data'
        self.tmdb = self.root + '/plugin.video.themoviedb.helper'
        self.vfs = FakeVfs({
            self.root: (['plugin.video.themoviedb.helper'], []),
            self.tmdb: (
                ['blur_v3', 'blur_v30', 'crop_v2', 'database_07'],
                ['settings.xml']),
            self.tmdb + '/blur_v3': ([], ['one.png', 'two.png']),
            self.tmdb + '/blur_v30': ([], ['keep.png']),
            self.tmdb + '/crop_v2': (['nested'], ['crop.png']),
            self.tmdb + '/crop_v2/nested': ([], ['nested.png']),
            self.tmdb + '/database_07': ([], ['cache.db']),
        }, {
            self.tmdb + '/settings.xml': 1,
            self.tmdb + '/blur_v3/one.png': 10,
            self.tmdb + '/blur_v3/two.png': 20,
            self.tmdb + '/blur_v30/keep.png': 30,
            self.tmdb + '/crop_v2/crop.png': 40,
            self.tmdb + '/crop_v2/nested/nested.png': 50,
            self.tmdb + '/database_07/cache.db': 60,
        })

    def test_planner_excludes_and_measures_only_exact_cache_trees(self):
        planner = FilePlanner(self.vfs)
        planner.addDir({'type': 'include', 'path': self.root, 'recurse': True})
        for rule in tmdb_helper_cache_exclusions(self.tmdb):
            planner.addDir(rule)
        planner.walk()

        summary = planner.summary()
        files = [item['file'] for item in planner.fileArray
                 if not item['is_dir']]
        self.assertEqual(91, summary['included_kib'])
        self.assertEqual(3, summary['included_files'])
        self.assertEqual(120, summary['excluded_kib'])
        self.assertEqual(4, summary['excluded_files'])
        self.assertIn(self.tmdb + '/blur_v30/keep.png', files)
        self.assertIn(self.tmdb + '/database_07/cache.db', files)
        self.assertNotIn(self.tmdb + '/blur_v3/one.png', files)
        self.assertEqual(
            [self.tmdb + '/blur_v3', self.tmdb + '/crop_v2'],
            [item['path'] for item in summary['exclusions']])

    def test_summary_reports_groups_and_largest_top_level_directory(self):
        planner = FilePlanner(self.vfs)
        planner.addDir({'type': 'include', 'path': self.root, 'recurse': True})
        for rule in tmdb_helper_cache_exclusions(self.tmdb):
            planner.addDir(rule)
        planner.walk()
        summary = planner.summary()
        group = {
            'name': 'addon_data',
            'source': 'special://home/userdata/addon_data',
            'plan_root': self.root,
            'files': list(planner.fileArray),
            'summary': summary,
        }

        plan = summarize_file_groups([group])
        self.assertEqual(91, plan['total_kib'])
        self.assertEqual(120, plan['excluded_kib'])
        self.assertEqual(self.tmdb, plan['largest_directories'][0]['path'])
        self.assertEqual(3, plan['largest_directories'][0]['file_count'])

    def test_get_files_resets_planner_for_restore_compatibility(self):
        planner = FilePlanner(self.vfs)
        planner.addFile(self.tmdb + '/settings.xml')
        self.assertEqual(1, len(planner.getFiles()))
        self.assertEqual(0, planner.totalFiles())
        self.assertEqual(0, planner.fileSize())


if __name__ == '__main__':
    unittest.main()
