#!/usr/bin/env python3
"""Render Auton brand exports. Install: pip install cairosvg Pillow"""
from pathlib import Path
from io import BytesIO
import json
import cairosvg
from PIL import Image
ROOT = Path(__file__).resolve().parents[1] / 'assets' / 'brand'
SVG = ROOT / 'svg'
PNG = ROOT / 'png'
FAV = ROOT / 'favicon'
PNG.mkdir(exist_ok=True)
FAV.mkdir(exist_ok=True)
def render(name, size, target, background=None):
    data = cairosvg.svg2png(url=str(SVG / (name + '.svg')), output_width=size, background_color=background)
    target.write_bytes(data)
    return Image.open(BytesIO(data))
for name in ('logo-horizontal', 'logo-stacked', 'wordmark'):
    for variant in ('', '-dark', '-mono-black', '-mono-white'):
        render(name + variant, 1200, PNG / f'{name}{variant}-1200.png')
for size in (128, 256, 512, 1024):
    render('icon', size, PNG / f'icon-{size}.png')
for variant in ('-dark', '-mono-black', '-mono-white'):
    render('icon' + variant, 512, PNG / f'icon{variant}-512.png')
for size in (16, 32, 48):
    render('icon', size, FAV / f'favicon-{size}x{size}.png')
icon = render('icon', 256, FAV / 'icon-256.png')
icon.save(FAV / 'favicon.ico', sizes=[(16, 16), (32, 32), (48, 48)])
(FAV / 'icon-256.png').unlink()
render('icon', 180, FAV / 'apple-touch-icon.png', '#FFFFFF')
for size in (192, 512):
    render('icon', size, FAV / f'android-chrome-{size}x{size}.png')
favicon = (SVG / 'icon.svg').read_text().replace('<title>Auton</title>', '<title>Auton</title><style>@media(prefers-color-scheme:dark){path[fill="#0E2F5E"]{fill:#E8EFFA}}</style>')
(FAV / 'favicon.svg').write_text(favicon)
(FAV / 'site.webmanifest').write_text(json.dumps({'name':'Auton','short_name':'Auton','icons':[{'src':f'android-chrome-{s}x{s}.png','sizes':f'{s}x{s}','type':'image/png'} for s in (192,512)],'theme_color':'#0E2F5E','background_color':'#FFFFFF','display':'standalone'},indent=2)+'\n')
print('Brand exports generated.')
