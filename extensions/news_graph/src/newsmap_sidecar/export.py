"""Fresh, read-only offline exports; never a write server or deployment."""
import json
import os
from pathlib import Path

from .common import checked_path, outside_repositories, overlaps, require, digest
from .projection import build

ASSETS = Path(__file__).resolve().parent / 'assets'


def export_view(db, output, allowlist=None):
    output = outside_repositories(output)
    require(not output.exists(), 'export requires a fresh directory')
    require(not overlaps(output, db.path.parent), 'export overlaps authority directory')
    for row in db.conn.execute('SELECT root FROM namespaces'):
        require(not overlaps(output, Path(row[0])), 'export overlaps source vault')
    for ancestor in output.parents:
        require(not any(ancestor.glob('*.sqlite')), 'export cannot be nested in an authority or backup directory')
    require(output.parent.is_dir(), 'create a dedicated export parent directory first')
    data = build(db, allowlist)
    manifest = json.loads((ASSETS / 'vendor-manifest.json').read_text())
    for filename, expected in manifest['files'].items():
        require(digest((ASSETS / filename).read_bytes()) == expected, 'bundled renderer checksum mismatch')
    serialized = json.dumps(data, ensure_ascii=False, allow_nan=False).replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    html = (ASSETS / 'index.html').read_text().replace('<!--NEWSMAP_DATA-->', serialized)
    # Authorization/validation completes before any output directory is created.
    output.mkdir(mode=0o700)
    filenames = ['app.js', 'style.css', 'cytoscape.min.js', 'CYTOSCAPE-LICENSE.txt', 'vendor-manifest.json']
    for filename in filenames:
        with (output / filename).open('xb') as f:
            f.write((ASSETS / filename).read_bytes())
    (output / 'index.html').write_text(html)
    return {'directory': str(output), 'scope': data['scope'], 'counts': data['counts'],
            'snapshot_at': data['snapshot_at'], 'files': ['index.html', *filenames]}
