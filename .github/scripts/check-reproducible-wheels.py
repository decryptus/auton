"""Verify wheel bytes reproduce from tracked source in the same build environment."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = ('auton', 'autond')


def main():
    epoch = subprocess.check_output(['git', 'show', '-s', '--format=%ct', 'HEAD'], cwd=ROOT, text=True).strip()
    paths = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    with tempfile.TemporaryDirectory() as directory:
        for package in PACKAGES:
            hashes = []
            for index in range(2):
                source = Path(directory) / (package + str(index))
                source.mkdir()
                for name in paths:
                    if not name or not (ROOT / name).is_file():
                        continue
                    destination = source / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(ROOT / name, destination)
                env = dict(os.environ, AUTON_PACKAGE=package, SOURCE_DATE_EPOCH=epoch)
                subprocess.run([sys.executable, '-m', 'build', '--wheel', '--no-isolation'],
                               cwd=source, env=env, check=True)
                wheels = list((source / 'dist').glob('*.whl'))
                if len(wheels) != 1:
                    raise RuntimeError('expected exactly one wheel')
                hashes.append(hashlib.sha256(wheels[0].read_bytes()).hexdigest())
            if hashes[0] != hashes[1]:
                raise RuntimeError(package + ' wheel is not reproducible')
            print(package + ': identical wheel bytes in two independent build directories')


if __name__ == '__main__':
    main()
