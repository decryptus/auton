"""Capture the real curses UI against disposable local daemons.

Development dependencies: pyte, Pillow, requests, PyYAML and daemon requirements.
Run from the checkout with its Python environment. No production servers are used.
"""
import fcntl
import os
from pathlib import Path
import pty
import select
import socket
import struct
import subprocess
import sys
import tempfile
import termios
import time

import pyte
from PIL import Image, ImageDraw, ImageFont
import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'docs' / 'images'
COLS, ROWS = 112, 22
FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf'
PALETTE = {'default': '#dddddd', 'black': '#101014', 'white': '#dddddd',
           'red': '#de6868', 'green': '#83c993', 'yellow': '#e1c879',
           'blue': '#80a5e0', 'magenta': '#bb96dc', 'cyan': '#7ac6c6'}


class CaptureScreen(pyte.Screen):
    # ncurses uses xterm CSI S/T scrolling, which pyte does not map by default.
    def scroll_up(self, count=1):
        saved = self.cursor.y
        self.cursor.y = self.margins.bottom if self.margins else self.lines - 1
        for _ in range(min(count or 1, self.lines)):
            self.index()
        self.cursor.y = saved

    def scroll_down(self, count=1):
        saved = self.cursor.y
        self.cursor.y = self.margins.top if self.margins else 0
        for _ in range(min(count or 1, self.lines)):
            self.reverse_index()
        self.cursor.y = saved


class CaptureStream(pyte.ByteStream):
    csi = dict(pyte.ByteStream.csi, S='scroll_up', T='scroll_down')
    events = pyte.ByteStream.events | frozenset(('scroll_up', 'scroll_down'))


def render(screen, name):
    font = ImageFont.truetype(FONT, 18)
    cell_width, cell_height = 11, 24
    image = Image.new('RGB', (COLS * cell_width + 32, ROWS * cell_height + 32), '#101014')
    draw = ImageDraw.Draw(image)
    for row in range(ROWS):
        for col in range(COLS):
            char = screen.buffer[row][col]
            fg = PALETTE.get(char.fg, '#dddddd')
            bg = '#101014' if char.bg == 'default' else PALETTE.get(char.bg, '#101014')
            if char.reverse:
                fg, bg = bg, fg
            x, y = 16 + col * cell_width, 16 + row * cell_height
            draw.rectangle((x, y, x + cell_width - 1, y + cell_height - 1), fill=bg)
            draw.text((x, y), char.data, font=font, fill=fg)
    DEST.mkdir(parents=True, exist_ok=True)
    image.save(DEST / name)


def main():
    processes = []
    tui = None
    master = None
    env = dict(os.environ, PYTHONPATH=str(ROOT), TERM='xterm')
    with tempfile.TemporaryDirectory() as directory:
        try:
            uris = []
            for name in ('edge-01', 'edge-02'):
                with socket.socket() as sock:
                    sock.bind(('127.0.0.1', 0))
                    port = sock.getsockname()[1]
                config = yaml.safe_load((ROOT / 'etc/auton/auton.yml.example').read_text())
                config['general'].update(listen_addr='127.0.0.1', listen_port=port,
                                         max_life_time=0, max_requests=0)
                config.pop('import_modules', None)
                config['modules'] = yaml.safe_load((ROOT / 'etc/auton/modules/job.yml').read_text())
                config['endpoints'] = {'diagnostics': {'plugin': 'subproc', 'config': {
                    'prog': sys.executable, 'timeout': 15}}}
                path = Path(directory) / (name + '.yml')
                path.write_text(yaml.safe_dump(config))
                process = subprocess.Popen([sys.executable, str(ROOT / 'bin/autond'), '-f',
                    '-c', str(path), '-p', str(Path(directory) / (name + '.pid')),
                    '--logfile', str(Path(directory) / (name + '.log'))],
                    env=env, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                processes.append(process)
                uri = 'http://127.0.0.1:%s' % port
                for _ in range(100):
                    try:
                        if requests.get(uri + '/health', timeout=0.1).status_code == 200:
                            break
                    except requests.RequestException:
                        time.sleep(0.03)
                else:
                    raise RuntimeError('demo daemon did not start')
                jobs = [('check-system', 'print("Host: %s\\nDisk: OK\\nMemory: OK\\nService: healthy\\nDiagnostic finished successfully.")' % name),
                        ('check-failed', 'import sys; print("Demo check failed", file=sys.stderr); sys.exit(2)')]
                for uid, code in jobs:
                    requests.post(uri + '/run/diagnostics/' + uid, json={'args': ['-c', code]}, timeout=2).raise_for_status()
                for _ in range(100):
                    if all(job['status'] == 'complete' for job in requests.get(uri + '/jobs', timeout=2).json()['jobs']):
                        break
                    time.sleep(0.02)
                uris.extend(['--daemon', name + '=' + uri])
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', ROWS, COLS, 0, 0))
            tui = subprocess.Popen([sys.executable, str(ROOT / 'bin/auton'), '--tui',
                                    '--refresh', '60'] + uris, env=env, cwd=ROOT,
                                   stdin=slave, stdout=slave, stderr=slave)
            os.close(slave)
            screen = CaptureScreen(COLS, ROWS)
            stream = CaptureStream(screen)
            def wait_for(text):
                end = time.monotonic() + 8
                while time.monotonic() < end:
                    if select.select([master], [], [], 0.1)[0]:
                        stream.feed(os.read(master, 65536))
                    if text in '\n'.join(screen.display):
                        while select.select([master], [], [], 0.15)[0]:
                            stream.feed(os.read(master, 65536))
                        return
                raise RuntimeError('missing terminal text: ' + text + '\n' + '\n'.join(screen.display))
            wait_for('check-system')
            os.write(master, b'a')
            wait_for('Jobs coverage 2/2')
            render(screen, 'tui-jobs.png')
            os.write(master, b'\t\t\t')
            wait_for('DAEMON           STATE')
            wait_for('Last refresh completed')
            render(screen, 'tui-daemons.png')
            os.write(master, b'\x1b')
            wait_for('[Jobs]')
            # The stable listing sorts oldest completed job first.
            os.write(master, b'kkkk\n')
            wait_for('Diagnostic finished successfully.')
            render(screen, 'tui-output.png')
            os.write(master, b'q')
            if tui.wait(timeout=3) != 0:
                raise RuntimeError('TUI exited unsuccessfully')
        finally:
            if tui is not None and tui.poll() is None:
                tui.kill()
                tui.wait()
            if master is not None:
                os.close(master)
            for process in processes:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == '__main__':
    main()
