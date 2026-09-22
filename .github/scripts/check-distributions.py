"""Check release metadata and installed contents without contacting PyPI."""
import email
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile

version = Path('VERSION').read_text().strip()
assert version == Path('RELEASE').read_text().strip()
for name in sys.argv[1:]:
    directory = Path('dist') / name if name in ('auton', 'autond') else Path('dist')
    wheels = list(directory.glob('*.whl'))
    sources = list(directory.glob('*.tar.gz'))
    assert len(wheels) == len(sources) == 1, (name, wheels, sources)
    with zipfile.ZipFile(wheels[0]) as archive:
        files = archive.namelist()
        metadata = email.message_from_bytes(archive.read(next(f for f in files if f.endswith('.dist-info/METADATA'))))
        assert metadata['Name'] == name
        assert metadata['Version'] == version
        assert metadata.get_all('Requires-Dist'), name
        scripts = [f.rsplit('/', 1)[-1] for f in files if '.data/scripts/' in f]
        assert scripts == [name], scripts
        if name == 'auton':
            assert not any(f.startswith('auton/') for f in files), 'Client contains daemon modules'
        else:
            module = 'auton' if name == 'autond' else name
            assert module + '/__init__.py' in files
            assert module + '/modules/' in '\n'.join(files)
    with tarfile.open(sources[0]) as archive:
        members = archive.getnames()
        prefix = members[0].split('/')[0] + '/'
        for required in ('setup.yml', 'pyproject.toml', 'VERSION', 'RELEASE'):
            assert prefix + required in members, required
        metadata = email.message_from_bytes(archive.extractfile(prefix + 'PKG-INFO').read())
        assert metadata['Name'] == name and metadata['Version'] == version
    # A PyPI user installing an sdist does not set AUTON_PACKAGE.
    if name in ('auton', 'autond'):
        with tempfile.TemporaryDirectory() as rebuilt:
            env = dict(os.environ)
            env.pop('AUTON_PACKAGE', None)
            subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-deps',
                            '--wheel-dir', rebuilt, str(sources[0].resolve())],
                           env=env, check=True)
            rebuilt_wheels = list(Path(rebuilt).glob('*.whl'))
            assert len(rebuilt_wheels) == 1
            with zipfile.ZipFile(rebuilt_wheels[0]) as archive:
                rebuilt_files = archive.namelist()
                info = next(f for f in rebuilt_files if f.endswith('.dist-info/METADATA'))
                rebuilt_metadata = email.message_from_bytes(archive.read(info))
                assert rebuilt_metadata['Name'] == name
                assert rebuilt_metadata['Version'] == version
                assert set(rebuilt_files) == set(files), 'Source and wheel contents differ'
    with tempfile.TemporaryDirectory() as temp:
        venv.create(temp, with_pip=True)
        python = str(Path(temp) / 'bin/python')
        subprocess.run([python, '-m', 'pip', 'install', '--no-deps', str(wheels[0].resolve())], check=True)
        subprocess.run([python, '-c', 'from importlib.metadata import version; import sys; assert version(sys.argv[1]) == sys.argv[2]', name, version], cwd=temp, check=True)
        script = Path(temp) / 'bin' / name
        assert script.is_file()
        compile(script.read_bytes(), str(script), 'exec')
    print('Validated', name, version)
