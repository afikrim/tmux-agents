#!/usr/bin/env python3
"""Build a portable archive without machine configuration or state."""
import hashlib
from pathlib import Path
import tarfile

root = Path(__file__).resolve().parents[1]
version = (root / 'VERSION').read_text().strip()
name = 'tmux-agents-' + version
output = root / 'dist' / (name + '.tar.gz')
output.parent.mkdir(exist_ok=True)
allowed = ['bin', 'lib', 'hooks', 'plugins', 'extras', 'scripts', 'tests', 'README.md',
           'DESIGN.md', 'LICENSE-NOTICE', 'VERSION', 'install.sh', 'Dockerfile.test']
def clean(info):
    if any(part in ('__pycache__', 'node_modules') for part in Path(info.name).parts) or info.name.endswith('.pyc'):
        return None
    info.uid = info.gid = 0
    info.uname = info.gname = ''
    return info
with tarfile.open(output, 'w:gz') as archive:
    for item in allowed:
        archive.add(root / item, arcname=name + '/' + item, filter=clean)
digest = hashlib.sha256(output.read_bytes()).hexdigest()
output.with_suffix(output.suffix + '.sha256').write_text(digest + '  ' + output.name + '\n')
print(output)
