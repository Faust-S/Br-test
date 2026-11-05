"""Official SHA-256-pinned jq with existing generic U01 installer + Python and JS consumers."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'universal-install'))
sys.path.insert(0,str(ROOT/'u03-capability-sdk/python'))
from iwxa_universal_install import ApprovedInstall,ApprovedRemoval,ManagedArtifactBackend,ManagedArtifactSpec
from iwxa_universal_install.managed import _native_platform
from iwxa_universal_install.program_connection import CliConnection
from iwxa_capability_sdk import assess
from u01_bridge import observe_u01_capabilities

def qualify():
    catalog=json.loads((ROOT/'universal-install/catalog/jq-1.8.1.json').read_text())
    connectors=json.loads((ROOT/'universal-install/catalog/cli-connectors-1.json').read_text())
    platform=_native_platform()
    binary=catalog['platforms'].get(platform)
    if binary is None:raise RuntimeError('U03_PINNED_JQ_UNSUPPORTED_PLATFORM:'+platform)
    manifest={**next(x for x in connectors['programs'] if x['program_id']=='JQ_CLI_V1'),
              'executable_name':binary['executable']}
    spec=ManagedArtifactSpec(program_id=catalog['program_id'],slug=catalog['slug'],
      release_id=catalog['upstream_tag'],platform=platform,executable_name=binary['executable'],
      archive_name=binary['asset'],archive_format='binary',archive_sha256=binary['sha256'],
      executable_sha256=binary['sha256'],
      download_url='https://github.com/jqlang/jq/releases/download/'+catalog['upstream_tag']+'/'+binary['asset'])
    descriptor={'schema':'IWXA_MODULE_DESCRIPTOR/1.0.0','id':'iwxa.u03.jq.python','version':'0.1.0',
      'provides':[],'requires':[{'id':'iwxa.cli.evaluate','range':'=1.0.0'}],
      'hostCompatibility':[{'hostId':'iwxa.bridge.python','range':'=0.1.0'}]}
    host={'hostId':'iwxa.bridge.python','version':'0.1.0'}
    config={'programId':spec.program_id,'bindings':[{'operation':'evaluate',
      'id':'iwxa.cli.evaluate','version':'1.0.0'}]}
    with tempfile.TemporaryDirectory(prefix='iwxa-u03-jq-') as temp:
        root=Path(temp).resolve()
        installed_root=root/'software'
        installed_root.mkdir()
        (installed_root/'user-data.txt').write_text('preserve',encoding='utf-8')
        backend=ManagedArtifactBackend()
        staged=backend.prepare(spec,str(root/'staging'))
        assert staged.status=='STAGED_NOT_ACTIVATED'
        approval=ApprovedInstall(spec.program_id,spec.fingerprint,
          'PINS-u03-install',str(installed_root),True)
        installed=backend.apply(spec,str(root/'staging'),approval)
        assert installed.installed and not installed.connected and not installed.receipt_written
        executable=Path(installed.executable)
        actual=hashlib.sha256(executable.read_bytes()).hexdigest()
        assert actual==binary['sha256'],'U03_JQ_SHA256_MISMATCH'
        c=CliConnection(manifest,str(executable))
        def compatible():
            return assess(descriptor,host,observe_u01_capabilities(c,config))['compatible']
        assert compatible() is False
        assert c.inspect()['status']=='DISCOVERED'
        assert c.connect()['status']=='CONNECTED'
        assert compatible() is False
        output=c.invoke('evaluate',{'expression':'6 * 7'})
        assert output['status']=='EXECUTED' and output['output'].strip()=='42'
        assert compatible() is False,'missing host-issued receipt'
        digest=hashlib.sha256(output['output'].encode()).hexdigest()
        config['receipts']={'evaluate':{'observation':output,
          'observationRef':'qualification:python:sha256:'+digest}}
        assert compatible() is True
        assert assess(descriptor,{**host,'version':'0.2.0'},
          observe_u01_capabilities(c,config))['compatible'] is False
        assert c.disconnect()['status']=='DISCONNECTED'
        assert compatible() is False
        assert c.recover()['status']=='CONNECTED'
        assert compatible() is False
        replay=c.invoke('evaluate',{'expression':'6 * 7'})
        assert replay['status']=='EXECUTED'
        assert replay['connection_epoch']!=output['connection_epoch']
        assert compatible() is False,'old receipt must not be replayed after reconnect and new invoke'
        config['receipts']={'evaluate':{'observation':replay,
          'observationRef':'qualification:python:epoch2:'+hashlib.sha256(replay['output'].encode()).hexdigest()}}
        assert compatible() is True
        node=subprocess.run(['node',str(ROOT/'u03-capability-sdk/qualification/real-u01-node.mjs'),
           str(executable)],capture_output=True,text=True,timeout=40,check=True)
        report=json.loads(node.stdout.strip())
        assert report['status']=='PASS' and report['output']=='42'
        from restart_host_qualification import qualify_restart_host
        restart = qualify_restart_host(executable)
        assert restart['status']=='PASS' and restart['result_readback']==[42, 56]
        assert backend.recover(spec,str(installed_root)).installed
        removal=ApprovedRemoval(spec.program_id,spec.fingerprint,
          'PINS-u03-remove',str(installed_root),True)
        assert backend.remove(spec,removal).status=='REMOVED'
        assert backend.recover(spec,str(installed_root)).status=='NOT_INSTALLED'
        assert (installed_root/'user-data.txt').read_text(encoding='utf-8')=='preserve'
        assert compatible() is False
        print(json.dumps({'schema':'IWXA_U03_REAL_U01_QUALIFICATION/1',
          'status':'PASS','platform':platform,'binary_sha256':actual,'python_consumer':'PASS',
          'node_consumer':'PASS','installed':True,'executed':True,'removed':True,
          'user_data_preserved':True,'packaged_restart_host':'PASS','product_hosts_modified':False},sort_keys=True))

if __name__=='__main__':
    qualify()
