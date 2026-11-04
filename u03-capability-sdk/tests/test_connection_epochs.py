"""U01 Python CLI epoch regression, without network or external executable."""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'universal-install'))
sys.path.insert(0,str(ROOT/'u03-capability-sdk/python'))
from iwxa_universal_install.program_connection import CliConnection
from u01_bridge import observe_u01_capabilities

class EpochTests(unittest.TestCase):
    def test_cli_epoch_is_stable_on_verify_but_rotates_after_disconnect(self):
        with tempfile.TemporaryDirectory(prefix='iwxa-u03-epoch-') as temp:
            executable=Path(temp)/('jq.exe' if sys.platform=='win32' else 'jq')
            executable.write_bytes(b'fixture')
            executable.chmod(0o755)
            spec={'schema':'IWXA_CLI_PROGRAM/1','program_id':'JQ_CLI_V1',
                  'executable_name':executable.name,'version_args':['--version'],
                  'version_contains':'jq-1.8.1','operations':{'evaluate':['-n','{expression}']}}
            config={'programId':'JQ_CLI_V1','bindings':[
                {'operation':'evaluate','id':'iwxa.cli.evaluate','version':'1.0.0'}]}
            def fake_run(path,args):
                if args==['--version']:
                    return 0,'jq-1.8.1'
                if args==['-n','6 * 7']:
                    return 0,'42\n'
                raise AssertionError(args)
            with patch('iwxa_universal_install.program_connection.run_command',side_effect=fake_run):
                c=CliConnection(spec,str(executable))
                first=c.connect()
                self.assertEqual(first['status'],'CONNECTED')
                epoch=first['connection_epoch']
                self.assertEqual(c.verify()['connection_epoch'],epoch)
                result=c.invoke('evaluate',{'expression':'6 * 7'})
                self.assertEqual(result['connection_epoch'],epoch)
                previous={'observation':result,'observationRef':'host:execution:1'}
                self.assertTrue(observe_u01_capabilities(c,{**config,'receipts':{
                    'evaluate':previous}})[0]['available'])
                c.disconnect()
                self.assertNotIn('connection_epoch',c.capabilities())
                self.assertNotEqual(c.recover()['connection_epoch'],epoch)
                self.assertFalse(observe_u01_capabilities(c,{**config,'receipts':{
                    'evaluate':previous}})[0]['available'])
                fresh=c.invoke('evaluate',{'expression':'6 * 7'})
                self.assertNotEqual(fresh['connection_epoch'],epoch)
                self.assertFalse(observe_u01_capabilities(c,{**config,'receipts':{
                    'evaluate':previous}})[0]['available'])
                self.assertTrue(observe_u01_capabilities(c,{**config,'receipts':{
                    'evaluate':{'observation':fresh,'observationRef':'host:execution:2'}}})[0]['available'])

if __name__=='__main__':
    unittest.main()
