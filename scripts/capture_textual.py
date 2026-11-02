"""Capture the actual optional UI with labelled offline fixtures, never live data."""
import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import dwho.tui.textual

os.environ.pop('NO_COLOR', None)
from auton_client.textual_demo import demo_app


async def capture(output, png=False):
    output.mkdir(parents=True, exist_ok=True)
    app = demo_app(operations=True)
    artifacts = []
    async with app.run_test(size=(156, 46)) as pilot:
        await pilot.pause()
        app.tick()
        steps = [('jobs', None), ('endpoints', click_view), ('output', output_view),
                 ('daemons', daemons_view), ('prepare', prepare_view), ('confirm', confirm_view), ('result', result_view)]
        for name, action in steps:
            if action:
                await action(pilot, app)
            await pilot.pause()
            path = output / (name + '.svg')
            path.write_text(app.export_screenshot(title='auton — SYNTHETIC DEMO'), encoding='utf-8')
            artifacts.append(dict(file=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
            if png:
                import resvg_py
                rendered = path.with_suffix('.png')
                rendered.write_bytes(resvg_py.svg_to_bytes(svg_path=str(path), monospace_family='DejaVu Sans Mono'))
                artifacts.append(dict(file=rendered.name, sha256=hashlib.sha256(rendered.read_bytes()).hexdigest()))
    app.preparation.session.close()
    try:
        revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], stderr=subprocess.DEVNULL, text=True).strip()
        dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'], text=True))
    except (OSError, subprocess.CalledProcessError):
        revision, dirty = None, None
    sources = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
               for p in sorted(Path('auton_client').glob('*.py'))}
    shared_root = Path(dwho.tui.textual.__file__).parent
    shared_sources = {f.name: hashlib.sha256(f.read_bytes()).hexdigest()
                      for f in sorted(shared_root.glob('*.py'))}
    (output / 'manifest.json').write_text(json.dumps(dict(product='auton', synthetic=True,
        renderer='Textual', textual_version=importlib.metadata.version('textual'),
        dwho_version=importlib.metadata.version('dwho'), shared_source_sha256=shared_sources,
        source_revision=revision, dirty=dirty, source_sha256=sources, captures=artifacts), indent=2)+'\n')


async def click_view(pilot, app):
    await pilot.click('#dw-nav-1')
    app.tick(); app.tick()


async def output_view(pilot, app):
    app.change_view('jobs')
    await pilot.pause()
    app.change_view('output')
    app.tick(); app.tick()


async def daemons_view(pilot, app):
    app.change_view('daemons'); app.tick(); app.tick()


async def prepare_view(pilot, app):
    from textual.widgets import SelectionList
    app.action_prepare()
    await pilot.pause()
    app.screen.query_one('#op-select-1', SelectionList).select('staging')
    app.screen.query_one('#op-select-2', SelectionList).select('deploy-app')


async def confirm_view(pilot, app):
    await pilot.click('#op-preview')


async def result_view(pilot, app):
    await pilot.click('#dw-confirm')
    for _ in range(100):
        await pilot.pause(0.05)
        if app.preparation.mode == 'result':
            break
    if app.preparation.mode != 'result' or app.preparation.result['status'] != 'failed':
        raise RuntimeError('Synthetic operation did not reach its expected failed result')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--png', action='store_true', help='Also render PNG with resvg-py')
    args = parser.parse_args()
    asyncio.run(capture(args.output, args.png))
