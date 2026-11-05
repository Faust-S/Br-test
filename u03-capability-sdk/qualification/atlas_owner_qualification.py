"""Run the installed U03 SDK against a PINNED unchanged public Atlas owner port.

The test uses a clean external Node host directory and three OS processes.
Atlas owns the canonical model, serialization, revisions, access and adapters.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

SDK = Path(__file__).resolve().parents[1]
ATLAS_SCRIPT = SDK / 'qualification/atlas-owner-host.mjs'


def execute(args, cwd):
    completed = subprocess.run([str(x) for x in args], cwd=cwd, text=True,
                               capture_output=True, check=False, timeout=120)
    if completed.returncode:
        raise AssertionError(
            'U03_ATLAS_HOST_FAILED code={} command={} stdout={} stderr={}'.format(
                completed.returncode, args,
                completed.stdout[-4000:], completed.stderr[-4000:]))
    return completed.stdout


def main():
    import sys
    if len(sys.argv) != 2:
        raise SystemExit('U03_PINNED_ATLAS_CHECKOUT_REQUIRED')
    atlas = Path(sys.argv[1]).resolve()
    if not (atlas / 'src/project-host-port.mjs').is_file():
        raise AssertionError('U03_ATLAS_CANONICAL_PORT_UNAVAILABLE')
    npm = shutil.which('npm.cmd' if os.name == 'nt' else 'npm')
    if not npm:
        raise RuntimeError('U03_ATLAS_NPM_MISSING')
    with tempfile.TemporaryDirectory(prefix='iwxa-u03-atlas-owner-') as temporary:
        base = Path(temporary)
        host = base / 'clean-external-host'
        host.mkdir()
        execute([npm, 'pack', str(SDK), '--silent', '--pack-destination', str(base)], host)
        artifacts = list(base.glob('*.tgz'))
        assert len(artifacts) == 1, 'U03_ATLAS_PACK_MISSING'
        (host / 'package.json').write_text(
            '{"name":"iwxa-u03-atlas-qualification-host","version":"0.1.0",'
            '"private":true,"type":"module"}', encoding='utf-8')
        execute([npm, 'install', '--offline', '--ignore-scripts',
                 '--no-audit', '--no-fund', str(artifacts[0])], host)
        program = host / 'consumer.mjs'
        shutil.copy2(ATLAS_SCRIPT, program)
        state = host / 'atlas-snapshot.json'
        reference = host / 'selected-work.json'
        results = []
        for phase in ('create', 'reopen', 'change'):
            output = execute(['node', str(program), phase,
                              str(state), str(reference), str(atlas)], host)
            result = json.loads(output.strip())
            assert result['status'] == 'PASS'
            assert result['phase'] == phase
            assert result['canonicalSchema'] == 'atlas.project/1'
            assert result['ownerPortUnmodified'] and result['installedSdk']
            assert result['ownerDeniedUnauthorized'] and result['hostAttachDelegated']
            results.append(result)
        assert results[-1]['staleRevisionRejected']
        assert results[-1]['revision'] > results[0]['revision']
        saved = json.loads(state.read_text(encoding='utf-8'))
        assert saved['schema'] == 'atlas.project/1'
        assert len(saved['projects']) == 1
        assert len(saved['projects'][0]['items']) == 1
        print(json.dumps({
            'schema': 'IWXA_U03_ATLAS_SOURCE_QUALIFICATION/1',
            'status': 'PASS',
            'three_processes': True,
            'read_only_atlas_host_port': True,
            'atlas_canonical_model': True,
            'saved_work_ref': True,
            'stale_work_ref_rejected': True,
            'owner_authorization_retained': True,
            'product_source_modified': False,
            'installed_sdk': True,
            'phase_revisions': [x['revision'] for x in results],
        }, sort_keys=True))


if __name__ == '__main__':
    main()
