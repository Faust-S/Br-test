"""Three independent host processes with packed U03 SDK and real U01 jq.

The host, not U03, owns persistent results and executable connection state.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SDK = ROOT / 'u03-capability-sdk'
CLI = ROOT / 'universal-install/host-bindings/program-connection.mjs'
HOST_PORT = ROOT / 'universal-install/host-bindings/product-hosts.mjs'
CATALOG = ROOT / 'universal-install/catalog/cli-connectors-1.json'
HOST_PROGRAM = SDK / 'qualification/restart-host-node.mjs'


def _run(command, cwd):
    process = subprocess.run([str(x) for x in command], cwd=cwd, capture_output=True,
                             text=True, check=False, timeout=90)
    if process.returncode:
        raise AssertionError(
            'U03_RESTART_HOST_FAILED code={} command={} stdout={} stderr={}'.format(
                process.returncode, command, process.stdout[-3000:], process.stderr[-3000:]))
    return process.stdout


def qualify_restart_host(executable):
    """Uses an already installed, SHA-256-pinned jq from existing U01 qualification."""
    executable = Path(executable).resolve()
    assert executable.is_file(), 'U03_RESTART_JQ_MISSING'
    npm = shutil.which('npm.cmd' if os.name == 'nt' else 'npm')
    if not npm:
        raise RuntimeError('U03_RESTART_NPM_MISSING')
    with tempfile.TemporaryDirectory(prefix='iwxa-u03-restart-') as temp:
        work = Path(temp)
        host = work / 'external-host'
        host.mkdir()
        _run([npm, 'pack', str(SDK), '--pack-destination', str(work), '--silent'], host)
        archives = list(work.glob('*.tgz'))
        assert len(archives) == 1, 'U03_RESTART_PACKAGE_MISSING'
        (host / 'package.json').write_text(
            '{"name":"iwxa-u03-external-host","version":"0.1.0","private":true,"type":"module"}',
            encoding='utf-8')
        _run([npm, 'install', '--offline', '--ignore-scripts', '--no-audit',
              '--no-fund', str(archives[0])], host)
        program = host / 'restart-host.mjs'
        shutil.copy2(HOST_PROGRAM, program)
        state = host / 'real-result.json'
        reports = []
        for phase, expected in [('first', [42]), ('restart', [42, 56]), ('reopen', [42, 56])]:
            output = _run(['node', str(program), phase, str(state), str(executable),
                           str(CATALOG), str(CLI), str(HOST_PORT)], host)
            report = json.loads(output.strip())
            assert report['status'] == 'PASS'
            assert report['phase'] == phase
            assert report['resultValues'] == expected
            assert report['installedSdk'] and report['existingU01Port']
            assert report['hostOwnedPersistence']
            if phase != 'first':
                assert report['oldReceiptRejected']
            reports.append(report)
        saved = json.loads(state.read_text(encoding='utf-8'))
        assert [r['value'] for r in saved['results']] == [42, 56]
        assert saved['results'][1]['derivedFrom'] == 42
        assert saved['results'][1]['expression'] == '42 + 14'
        assert [r['lifecycleCalls'] for r in reports] == [1, 1, 0]
        return {'schema': 'IWXA_U03_PACKAGED_RESTART_HOST/1',
                'status': 'PASS', 'three_processes': True, 'real_jq': True,
                'packaged_node_sdk': True, 'result_readback': [42, 56],
                'old_receipts_rejected': True, 'host_owned_persistence': True,
                'private_product_modified': False}
