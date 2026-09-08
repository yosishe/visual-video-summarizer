"""Decision fixtures: provenance, one-stream selection, and incomplete downloads."""
import contextlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import candidates as c
from acquisition import AcquisitionError
import render
from safety import validate_generated_html


class MediaAcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.calls = []
        self.probe = mock.patch.object(c, 'probe_media', return_value={'start_time': 0, 'duration': 1, 'frame_duration': .04})
        self.probe.start()
        self.tools = mock.patch.object(c, 'require_tools'); self.tools.start()
        self.cache = mock.patch.object(c, 'MEDIA_CACHE_ROOT', None); self.cache.start()
    def tearDown(self):
        self.cache.stop(); self.tools.stop(); self.probe.stop(); self.tmp.cleanup()
    def download(self, command, **kwargs):
        self.calls.append(command)
        target = command[command.index('-o') + 1].replace('%(ext)s', 'mp4')
        Path(target).write_bytes(b'frame-media')
        return subprocess.CompletedProcess(command, 0, '', '')
    def test_duplicate_urls_and_valid_cache_make_one_download(self):
        with mock.patch.object(c, 'run_process', side_effect=self.download):
            first = c.resolve_parts('https://youtu.be/abcdefghijk', self.root)
            second = c.resolve_parts('https://www.youtube.com/watch?v=abcdefghijk&t=25', self.root)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(first, second)
        fmt = self.calls[0][self.calls[0].index('-f') + 1]
        self.assertNotIn('+ba', fmt)
        self.assertNotIn('-N', self.calls[0])
        self.assertIn('--abort-on-unavailable-fragments', self.calls[0])
    def test_corrupted_cache_is_reacquired_without_adopting_leftover(self):
        with mock.patch.object(c, 'run_process', side_effect=self.download):
            first = c.resolve_parts('https://youtu.be/abcdefghijk', self.root)
            Path(first[0]['path']).write_bytes(b'corrupted')
            c.resolve_parts('https://youtu.be/abcdefghijk', self.root)
        self.assertEqual(len(self.calls), 2)
    def test_shared_cache_across_work_directories(self):
        with mock.patch.object(c, 'MEDIA_CACHE_ROOT', self.root / 'shared'), \
             mock.patch.object(c, 'run_process', side_effect=self.download):
            first = c.resolve_parts('https://youtu.be/abcdefghijk', self.root / 'a')
            second = c.resolve_parts('https://youtu.be/abcdefghijk', self.root / 'b')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(first[0]['sha256'], second[0]['sha256'])
    def test_missing_fragment_never_becomes_complete_media(self):
        def incomplete(command, **kwargs):
            self.download(command, **kwargs)
            return subprocess.CompletedProcess(command, 1, '', 'fragment not found; aborting')
        with mock.patch.object(c, 'run_process', side_effect=incomplete):
            with self.assertRaises(AcquisitionError): c.resolve_parts('https://youtu.be/abcdefghijk', self.root)
        self.assertFalse((self.root / 'download' / 'parts.json').exists())
        self.assertEqual(len(self.calls), 1)
    def test_section_progress_survives_later_access_failure(self):
        def partial(command, **kwargs):
            if '--download-sections' in command and command[command.index('--download-sections') + 1].startswith('*1.'):
                return subprocess.CompletedProcess(command, 1, '', '403 Forbidden')
            return self.download(command, **kwargs)
        with mock.patch.object(c, 'run_process', side_effect=partial):
            with self.assertRaises(AcquisitionError): c.resolve_parts('https://youtu.be/abcdefghijk', self.root, [(0,1),(1,2)])
        self.assertFalse((self.root / 'download' / 'parts.json').exists())
        with mock.patch.object(c, 'run_process', side_effect=self.download):
            parts = c.resolve_parts('https://youtu.be/abcdefghijk', self.root, [(0,1),(1,2)])
        self.assertEqual(len(parts), 2)
        self.assertEqual(len(self.calls), 2)  # first section never downloaded twice
    def test_partial_html_is_visible_and_static_in_both_languages(self):
        transcript = {'status':'partial', 'video':{'title':'Fixture'},
                      'failed_chunks':[{'range':{'start_s':60,'end_s':120}}]}
        for lang, label in [('en','PARTIAL SUMMARY'), ('he','סיכום חלקי')]:
            with self.subTest(lang=lang):
                page = render._render_html(transcript, [], {}, [], '', lang)
                self.assertIn(label, page)
                self.assertIn('01:00–02:00', page)
                validate_generated_html(page)
