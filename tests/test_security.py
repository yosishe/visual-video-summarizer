"""Security regressions with synthetic secrets/files; no real upload or key use."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock
from urllib.request import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import bundle
import doctor
import render
import safety
import transcript
import whisper


class UploadTests(unittest.TestCase):
    def test_key_presence_does_not_authorize_upload(self):
        for flags in ([], ['--no-whisper'], ['--whisper', 'groq', '--no-whisper']):
            with self.subTest(flags=flags), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = root / 'lecture.mp4'
                source.touch()
                with mock.patch.object(sys, 'argv', ['transcript', str(source), '--work', str(root), *flags]), \
                     mock.patch.object(transcript, 'probe', return_value={'duration': 20, 'has_audio': True}), \
                     mock.patch.dict(os.environ, {'GROQ_API_KEY': 'SYNTHETIC_SECRET'}), \
                     mock.patch.object(transcript, 'load_api_key') as keys, \
                     mock.patch.object(transcript, 'transcribe_video') as upload, \
                     contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(transcript.main(), 6)
                keys.assert_not_called()
                upload.assert_not_called()

    def test_explicit_provider_is_used(self):
        for provider in ('groq', 'openai'):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = root / 'lecture.mp4'
                source.touch()
                with mock.patch.object(sys, 'argv', ['transcript', str(source), '--work', str(root), '--whisper', provider]), \
                     mock.patch.object(transcript, 'probe', return_value={'duration': 20, 'has_audio': True}), \
                     mock.patch.object(transcript, 'load_api_key', return_value=(provider, 'SYNTHETIC_SECRET')) as keys, \
                     mock.patch.object(transcript, 'transcribe_video', return_value=([{'start': 0, 'end': 20, 'text': 'Lecture.'}], provider)) as upload, \
                     contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(transcript.main(), 0)
                keys.assert_called_once_with(provider)
                self.assertEqual(upload.call_args.kwargs['backend'], provider)
                self.assertNotIn('SYNTHETIC_SECRET', (root / 'transcript.json').read_text())

    def test_helper_requires_provider_before_reading_keys_or_audio(self):
        with mock.patch.object(whisper, 'load_api_key') as keys, mock.patch.object(whisper, 'extract_audio') as extract:
            with self.assertRaisesRegex(SystemExit, 'Explicit transcription provider'):
                whisper.transcribe_video('not-read.mp4', Path('not-written.mp3'))
            keys.assert_not_called()
            extract.assert_not_called()

    def test_no_legacy_or_project_credential_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            old = home / '.config/watch/.env'
            old.parent.mkdir(parents=True)
            old.write_text('GROQ_API_KEY=LEGACY_SECRET')
            own = home / '.config/summarize-video/.env'
            with mock.patch.object(Path, 'home', return_value=home), mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual(whisper.load_api_key('groq'), (None, None))
                own.parent.mkdir(parents=True)
                own.write_text('GROQ_API_KEY=OWN_SECRET\nOPENAI_API_KEY=SECOND_SECRET')
                self.assertEqual(whisper.load_api_key('groq'), ('groq', 'OWN_SECRET'))
                self.assertEqual(whisper.load_api_key('openai'), ('openai', 'SECOND_SECRET'))

    def test_all_upload_redirects_fail_closed(self):
        handler = whisper.NoUploadRedirects()
        request = Request(whisper.GROQ_ENDPOINT, data=b'audio', headers={'Authorization': 'Bearer SYNTHETIC_SECRET'})
        for code in (301, 302, 303, 307, 308):
            with self.subTest(code=code), self.assertRaises(urllib.error.HTTPError) as caught:
                handler.redirect_request(request, None, code, 'redirect', {}, 'https://other.invalid/upload')
            caught.exception.close()

    def test_errors_do_not_echo_provider_content_or_keys(self):
        secret = 'SYNTHETIC_SECRET'
        errors = [urllib.error.HTTPError(whisper.GROQ_ENDPOINT, 401, secret, {}, io.BytesIO(secret.encode())),
                  urllib.error.HTTPError(whisper.GROQ_ENDPOINT, 503, secret, {}, io.BytesIO(secret.encode())),
                  urllib.error.URLError(secret)]
        for error in errors:
            with self.subTest(kind=type(error).__name__), tempfile.TemporaryDirectory() as tmp:
                audio = Path(tmp) / 'audio.mp3'
                audio.write_bytes(b'audio')
                opener = mock.MagicMock()
                opener.open.side_effect = error
                stderr = io.StringIO()
                with mock.patch.object(whisper, 'build_opener', return_value=opener) as build, \
                     mock.patch.object(whisper.time, 'sleep'), contextlib.redirect_stderr(stderr):
                    with self.assertRaises(SystemExit) as caught:
                        whisper._post_whisper(whisper.GROQ_ENDPOINT, secret, 'test', audio)
                self.assertNotIn(secret, str(caught.exception) + stderr.getvalue())
                self.assertTrue(any(isinstance(x, whisper.NoUploadRedirects) for x in build.call_args.args))

    def test_non_json_response_is_not_echoed(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / 'audio.mp3'
            audio.write_bytes(b'audio')
            opener = mock.MagicMock()
            opener.open.return_value.__enter__.return_value.read.return_value = b'SYNTHETIC_SECRET'
            with mock.patch.object(whisper, 'build_opener', return_value=opener):
                with self.assertRaisesRegex(SystemExit, 'non-JSON') as caught:
                    whisper._post_whisper(whisper.GROQ_ENDPOINT, 'SYNTHETIC_SECRET', 'test', audio)
            self.assertNotIn('SYNTHETIC_SECRET', str(caught.exception))

    def test_unapproved_endpoint_rejected_before_reading_audio(self):
        with mock.patch.object(whisper, '_build_multipart') as multipart:
            with self.assertRaisesRegex(SystemExit, 'Unapproved'):
                whisper._post_whisper('https://other.invalid', 'secret', 'test', Path('not-read.mp3'))
            multipart.assert_not_called()

    def test_downloader_ignores_ambient_configuration(self):
        with mock.patch.object(transcript.shutil, 'which', return_value='/bin/yt-dlp'), \
             mock.patch.object(transcript.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
            transcript._run_ytdlp(['--version'])
        command = run.call_args.args[0]
        for flag in safety.YTDLP_FLAGS:
            self.assertIn(flag, command)

    def test_full_and_section_downloads_use_safe_options(self):
        import candidates
        for sections in ([], [(0.0, 1.0)]):
            with self.subTest(sections=sections), tempfile.TemporaryDirectory() as tmp:
                def download(command, **kwargs):
                    template = command[command.index('-o') + 1]
                    Path(template.replace('%(ext)s', 'mp4')).write_bytes(b'video')
                    return subprocess.CompletedProcess(command, 0)
                with mock.patch.object(candidates.shutil, 'which', return_value='/bin/yt-dlp'), \
                     mock.patch.object(candidates.subprocess, 'run', side_effect=download) as run, \
                     mock.patch.object(candidates, 'probe_media', return_value={'start_time': 0, 'duration': 1, 'frame_duration': 0.04}), \
                     contextlib.redirect_stderr(io.StringIO()):
                    parts = candidates.resolve_parts('https://www.youtube.com/watch?v=fixture', Path(tmp), sections)
                self.assertEqual(len(parts), 1)
                for flag in safety.YTDLP_FLAGS:
                    self.assertIn(flag, run.call_args.args[0])



class FileBoundaryTests(unittest.TestCase):
    def make_summary(self, root):
        summary = root / 'summary-demo'
        assets = summary / 'assets'
        assets.mkdir(parents=True)
        (summary / 'manifest.json').write_text('{}')
        (assets / 'frame-thumb.jpg').write_bytes(b'thumb')
        (assets / 'frame-full.jpg').write_bytes(b'full')
        (summary / 'index.html').write_text('<img src="assets/frame-thumb.jpg" alt="frame">')
        return summary

    def test_bundle_rejects_symlinked_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary = self.make_summary(root)
            outside = root / 'private.html'
            outside.write_text('PRIVATE_SENTINEL')
            (summary / 'index.html').unlink()
            (summary / 'index.html').symlink_to(outside)
            with self.assertRaisesRegex(SystemExit, 'symlinked'):
                bundle.bundle(summary, None)

    def test_bundle_rejects_escaping_full_sibling_and_direct_symlink(self):
        for variant in ('full', 'thumb'):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                summary = self.make_summary(root)
                outside = root / 'outside.jpg'
                outside.write_bytes(b'PRIVATE_SENTINEL')
                target = summary / 'assets' / f'frame-{variant}.jpg'
                target.unlink()
                target.symlink_to(outside)
                with self.assertRaisesRegex(SystemExit, 'Unsafe asset path'):
                    bundle.bundle(summary, None)
                self.assertFalse((root / 'summary-demo.html').exists())

    def test_bundle_rejects_symlinked_assets_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary = self.make_summary(root)
            external = root / 'outside'
            (summary / 'assets').rename(external)
            (summary / 'assets').symlink_to(external, target_is_directory=True)
            with self.assertRaisesRegex(SystemExit, 'Unsafe asset path'):
                bundle.bundle(summary, None)

    def test_output_and_old_temporary_symlinks_cannot_overwrite_outside(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary = self.make_summary(root)
            outside = root / 'outside.txt'
            outside.write_text('PRIVATE_SENTINEL')
            output = root / 'summary-demo.html'
            old_temp = output.with_suffix('.html.tmp')
            old_temp.symlink_to(outside)
            bundle.bundle(summary, output)
            self.assertEqual(outside.read_text(), 'PRIVATE_SENTINEL')
            output.unlink()
            output.symlink_to(outside)
            with self.assertRaisesRegex(SystemExit, 'symlink'):
                bundle.bundle(summary, output)
            self.assertEqual(outside.read_text(), 'PRIVATE_SENTINEL')

    def test_renderer_binds_hash_to_rendered_file_not_manifest_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / 'outside.jpg'
            outside.write_bytes(b'expected')
            assets = root / 'assets'
            assets.mkdir()
            (assets / 'frame.jpg').write_bytes(b'different')
            record = {'file': 'frame.jpg', 'path': str(outside), 'sha256': hashlib.sha256(b'expected').hexdigest()}
            payload = {'assets': [{'full': dict(record), 'thumb': dict(record)}]}
            render._checked_assets(payload, assets)
            actual = Path(payload['assets'][0]['full']['path'])
            self.assertEqual(actual, (assets / 'frame.jpg').resolve())
            self.assertNotEqual(render._sha256(actual), record['sha256'])

    def test_renderer_rejects_unsafe_asset_names_and_unlisted_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('../outside.jpg', '/outside.jpg', 'x\\outside.jpg', 'frame.svg', None):
                with self.subTest(name=name), self.assertRaises(SystemExit):
                    safety.asset_file(root, name)
            (root / 'unused.jpg').symlink_to(root / 'absent.jpg')
            with self.assertRaisesRegex(SystemExit, 'non-regular'):
                render._checked_assets({'assets': []}, root)

    def test_active_html_and_remote_resources_are_rejected(self):
        cases = ['<script>alert(1)</script>', '<img src="assets/f.jpg" onerror="run()">',
                 '<iframe src="https://other.invalid"></iframe>', '<img src="https://other.invalid/x.jpg">',
                 '<img src="assets/../../secret.jpg">', '<img src="file:///secret.jpg">',
                 '<a href="javascript:run()">click</a>', '<meta http-equiv="refresh" content="0;url=https://other.invalid">',
                 '<style>@import "https://other.invalid";</style>',
                 '<style>body {background:url(https://other.invalid/a)}</style>',
                 r'<style>body {background:u\72l(https://other.invalid/a)}</style>']
        for html in cases:
            with self.subTest(html=html), self.assertRaises(SystemExit):
                safety.validate_generated_html(html)

    def test_pdf_rejects_active_html_before_launching_browser(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'summary.html'
            source.write_text('<script>fetch("https://other.invalid")</script>')
            with mock.patch.object(render.subprocess, 'run') as run:
                with self.assertRaises(SystemExit):
                    render.export_pdf(source, root / 'out.pdf')
                run.assert_not_called()

    def test_pdf_fallback_does_not_install_weasyprint_via_uv(self):
        with mock.patch.dict(sys.modules, {'weasyprint': None}), \
             mock.patch.object(render.shutil, 'which', side_effect=lambda name: '/bin/uv' if name == 'uv' else None):
            self.assertIsNone(render._find_weasyprint())

    def test_timestamp_host_is_checked_exactly(self):
        for source in ('https://youtube.com.attacker.invalid/watch?v=x', 'https://youtube.com@attacker.invalid/watch?v=x'):
            self.assertIsNone(render._timestamp_url(source, 10))
        self.assertIn('t=10', render._timestamp_url('https://www.youtube.com/watch?v=x', 10))


class DoctorTests(unittest.TestCase):
    def test_missing_dependencies_produce_actionable_json_without_installing(self):
        with mock.patch.object(doctor.shutil, 'which', return_value=None), \
             mock.patch.object(doctor.importlib.util, 'find_spec', return_value=None), \
             mock.patch.object(doctor.subprocess, 'run') as run:
            result = doctor.check()
        self.assertFalse(result['ready'])
        self.assertIn('off unless', result['cloud_transcription'])
        json.dumps(result)
        run.assert_not_called()

    def test_local_mode_does_not_require_ytdlp_or_read_key_files(self):
        with mock.patch.object(doctor.shutil, 'which', side_effect=lambda n: '/bin/' + n if n in {'ffmpeg', 'ffprobe'} else None), \
             mock.patch.object(doctor.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'version\n')), \
             mock.patch.object(Path, 'read_text', side_effect=AssertionError('Must not read config')), \
             mock.patch.object(doctor.importlib.util, 'find_spec', return_value=None):
            result = doctor.check(local=True)
        self.assertTrue(result['ready'])
        self.assertFalse(next(r for r in result['checks'] if r['name'] == 'yt-dlp')['required'])


if __name__ == '__main__':
    unittest.main()
