"""Create a source snapshot for GitHub; runtime files are never selected."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parent.parent
TOP_FILES = ['.env.example', '.gitignore', 'README.md', 'requirements.txt',
             'build_mac.command', 'build_windows.bat', 'build_web.bat',
             'dev_web.bat', 'install.bat', 'run.bat', 'stop.bat']
TREES = ['src', 'tests', 'packaging', '.github', 'chrome-extension', 'web/src', 'web/tests', 'web/public']
WEB_FILES = ['web/index.html', 'web/package.json', 'web/package-lock.json', 'web/vite.config.js']


def source_files(root: Path):
    candidates = [root / name for name in TOP_FILES + WEB_FILES]
    for directory in TREES:
        candidates.extend((root / directory).rglob('*'))
    for path in sorted(set(candidates)):
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            continue
        relative = path.relative_to(root)
        if any(part in {'__pycache__', '.pytest_cache', '.env', '.DS_Store', 'node_modules'} for part in relative.parts):
            continue
        if path.suffix in {'.pyc', '.pyo'}:
            continue
        yield path


def create_archive(root: Path, output: Path):
    example = (root / '.env.example').read_text(encoding='utf-8-sig')
    if re.search(r'^\w*(?:KEY|TOKEN|SECRET)\w*[ \t]*=[ \t]*\S+', example, re.MULTILINE):
        raise ValueError('.env.example contains a credential; clear it before sharing.')
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in source_files(root):
            archive.write(path, path.relative_to(root).as_posix())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', default='1.0.0')
    args = parser.parse_args()
    if not re.fullmatch(r'\d+\.\d+\.\d+', args.version):
        parser.error('Version must be N.N.N')
    output = ROOT / 'releases' / f'AutoSet-{args.version}-github-source.zip'
    create_archive(ROOT, output)
    print(output)


if __name__ == '__main__':
    main()
