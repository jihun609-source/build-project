"""Test sharing actual source while keeping private runtime state out."""
import importlib.util
from pathlib import Path
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'packaging' / (name + '.py'))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_upload_snapshot_includes_workflow_and_excludes_personal_data(tmp_path):
    snapshot = module('source_archive')
    files = {'.env.example': 'GEMINI_API_KEY=\n', 'README.md': 'public',
             'src/main.py': 'code', '.github/workflows/packages.yml': 'workflow',
             'web/src/main.js': 'ui', 'web/package-lock.json': '{}',
             '.env': 'SECRET', 'config.yaml': 'PRIVATE', 'secrets/key.json': 'PRIVATE',
             'db/pipeline.sqlite': 'PRIVATE', '.browser/profile/token': 'PRIVATE',
             'web/node_modules/some.js': 'skip', 'src/__pycache__/main.pyc': 'skip',
             'ready/video.mp4': 'PRIVATE', 'prompts/custom/system.md': 'PRIVATE'}
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    output = tmp_path / 'snapshot.zip'
    snapshot.create_archive(tmp_path, output)
    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == {'.env.example', 'README.md', 'src/main.py',
                                         '.github/workflows/packages.yml', 'web/src/main.js',
                                         'web/package-lock.json'}


def test_upload_snapshot_rejects_example_with_private_key(tmp_path):
    (tmp_path / '.env.example').write_text('GEMINI_API_KEY=private-test-value\n')
    with pytest.raises(ValueError, match='credential'):
        module('source_archive').create_archive(tmp_path, tmp_path / 'source.zip')


def test_outputs_are_separated_by_platform_and_architecture(tmp_path):
    build = module('build')
    assert build.output_directory(tmp_path, 'win32', 'x64') == tmp_path / 'windows'
    assert build.output_directory(tmp_path, 'darwin', 'arm64') == tmp_path / 'macos/arm64'
    assert build.output_directory(tmp_path, 'darwin', 'x64') == tmp_path / 'macos/x64'
