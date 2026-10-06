"""Build on the target OS. Only explicitly listed public resources enter the package."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def output_directory(base: Path, system: str, arch: str) -> Path:
    return base / 'windows' if system == 'win32' else base / 'macos' / arch


def run(args, **kwargs):
    print(' '.join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), cwd=ROOT, check=True, **kwargs)


def licenses(stage: Path, ffmpeg: Path):
    target = stage / 'licenses'
    target.mkdir(parents=True, exist_ok=True)
    inventory = []
    for dist in importlib.metadata.distributions():
        name = dist.metadata['Name']
        if name.lower().startswith('nvidia-') or name.lower() in {'pytest', 'pyinstaller', 'pip'}:
            continue
        inventory.append({'name': name, 'version': dist.version})
        for entry in dist.files or []:
            if any(part.lower().startswith(('license', 'copying', 'notice')) for part in entry.parts):
                source = Path(dist.locate_file(entry))
                if source.is_file():
                    out = target / name / str(entry).replace('..', '_parent').lstrip('/\\')
                    out.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, out)
    (target / 'packages.json').write_text(json.dumps(inventory, indent=2), encoding='utf-8')
    for parent in (ffmpeg, ffmpeg.parent):
        for source in parent.glob('*'):
            if source.is_file() and source.name.lower().startswith(('license', 'copying')):
                shutil.copy2(source, target / ('FFmpeg-' + source.name))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', default='1.0.0')
    parser.add_argument('--ffmpeg-dir', type=Path)
    parser.add_argument('--iscc', type=Path)
    parser.add_argument('--bundle-only', action='store_true')
    parser.add_argument('--skip-freeze', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'\d+\.\d+\.\d+', args.version):
        parser.error('version must be N.N.N')
    if sys.platform not in ('win32', 'darwin'):
        parser.error('Build on Windows or macOS; cross-compilation is not supported.')
    ffmpeg = args.ffmpeg_dir or (Path(shutil.which('ffmpeg')).parent if shutil.which('ffmpeg') else None)
    suffix = '.exe' if sys.platform == 'win32' else ''
    if not ffmpeg or not all((ffmpeg / (tool + suffix)).is_file() for tool in ('ffmpeg', 'ffprobe')):
        parser.error('Provide --ffmpeg-dir with ffmpeg and ffprobe.')
    if not (ROOT / 'web/dist/index.html').is_file():
        parser.error('Build the web UI first: cd web && npm ci && npm run build')
    example = (ROOT / '.env.example').read_text(encoding='utf-8-sig')
    if re.search(r'^\w*(?:KEY|TOKEN|SECRET)\w*[ \t]*=[ \t]*\S+', example, re.MULTILINE):
        parser.error('.env.example must not contain populated credentials.')
    stage = ROOT / 'build/package-resources'
    licenses(stage, ffmpeg)
    env = {**os.environ, 'AUTOSET_FFMPEG_DIR': str(ffmpeg.resolve()), 'AUTOSET_VERSION': args.version,
           'PYINSTALLER_CONFIG_DIR': str(ROOT / 'build/pyinstaller-cache')}
    # Some restricted build shells cannot let Tcl read the base Python directory.
    for key, folder in (('TCL_LIBRARY', 'tcl8.6'), ('TK_LIBRARY', 'tk8.6')):
        local = ROOT / '.build-tools/tcl' / folder
        if local.is_dir():
            env[key] = str(local)
    if not args.skip_freeze:
        run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', ROOT / 'packaging/AutoSet.spec'], env=env)
    arch = 'arm64' if platform.machine().lower() in ('arm64', 'aarch64') else 'x64'
    artifact_dir = output_directory(ROOT / 'releases', sys.platform, arch)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    label = f'AutoSet-{args.version}-{"windows" if sys.platform == "win32" else "macos"}-{arch}'
    executable = ROOT / ('dist/AutoSet/AutoSet.exe' if sys.platform == 'win32' else 'dist/AutoSet.app/Contents/MacOS/AutoSet')
    report = artifact_dir / f'{label}-self-test.json'
    run([executable, '--self-test', '--report', report], env={**env, 'AUTOSET_HOME': str(ROOT / 'build/smoke-data')})
    diagnostic = json.loads(report.read_text(encoding='utf-8'))
    if not diagnostic['ok']:
        raise RuntimeError(f'Packaged self-test failed: {diagnostic}')
    # Offline child-process path used by the downloader, with the same pipes as production.
    child = subprocess.run([str(executable), '--run-yt-dlp', '--version'], capture_output=True,
                           text=True, timeout=60, env=env)
    if child.returncode or not re.search(r'\d{4}\.\d{2}\.\d{2}', child.stdout):
        raise RuntimeError(f'Bundled yt-dlp failed: {child.stdout} {child.stderr}')
    if not args.bundle_only:
        if sys.platform == 'win32':
            iscc = args.iscc or shutil.which('iscc')
            if not iscc:
                for candidate in (ROOT / '.build-tools/InnoSetup/ISCC.exe',
                                  Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Inno Setup 6/ISCC.exe'):
                    if candidate.is_file():
                        iscc = candidate
                        break
            if not iscc:
                raise RuntimeError('Install Inno Setup 6 or supply --iscc; the standalone folder is in dist/AutoSet.')
            run([iscc, '/Qp', f'/DAppVersion={args.version}', f'/DSourceDir={ROOT / "dist/AutoSet"}',
                 f'/DArtifactDir={artifact_dir}', ROOT / 'packaging/windows.iss'])
            shutil.make_archive(str(artifact_dir / (label + '-portable')), 'zip', ROOT / 'dist', 'AutoSet')
        else:
            dmg_stage = stage / ('dmg-' + arch)
            dmg_stage.mkdir(exist_ok=True)
            shutil.copytree(ROOT / 'dist/AutoSet.app', dmg_stage / 'AutoSet.app', dirs_exist_ok=True, symlinks=True)
            shutil.copy2(ROOT / 'README.md', dmg_stage / 'README.md')
            link = dmg_stage / 'Applications'
            if not link.exists():
                link.symlink_to('/Applications')
            run(['hdiutil', 'create', '-volname', 'AutoSet', '-srcfolder', dmg_stage, '-ov',
                 '-format', 'UDZO', artifact_dir / (label + '.dmg')])
    for artifact in artifact_dir.glob(label + '*'):
        if artifact.suffix in ('.exe', '.zip', '.dmg'):
            digest = hashlib.file_digest(artifact.open('rb'), 'sha256').hexdigest()
            artifact.with_name(artifact.name + '.sha256').write_text(f'{digest}  {artifact.name}\n', encoding='utf-8')


if __name__ == '__main__':
    main()
