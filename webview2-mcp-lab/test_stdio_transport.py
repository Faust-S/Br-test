import json
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

from stdio_transport import StdioMcpTransport, StdioTransportError

# Real subprocess and actual newline-delimited JSON-RPC; not a mocked Popen.
SERVER = r'''
import json,sys,time
count=0
mode=sys.argv[1]
def send(value):
    print(json.dumps(value),flush=True)
for line in sys.stdin:
    msg=json.loads(line)
    method=msg.get('method')
    if 'id' not in msg: continue
    id=msg['id']
    if method=='initialize':
        send({'jsonrpc':'2.0','id':id,'result':{'protocolVersion':'bad' if mode=='bad_version' else msg['params']['protocolVersion'],'serverInfo':{'name':'fixture','version':'1'},'capabilities':{'tools':{}}}})
        continue
    if mode=='malformed': print('not-json',flush=True);continue
    if mode=='oversize': print('x'*4096,flush=True);continue
    if mode=='wrong_id': send({'jsonrpc':'2.0','id':id+1,'result':{}});continue
    if mode=='timeout': time.sleep(30);continue
    if mode=='rpc_error': send({'jsonrpc':'2.0','id':id,'error':{'code':-32000,'message':'uncertain'}});continue
    if method=='tools/list':
        send({'jsonrpc':'2.0','method':'notifications/tools/list_changed'})
        send({'jsonrpc':'2.0','id':'server-ping','method':'ping'})
        answer=json.loads(sys.stdin.readline())
        assert answer=={'jsonrpc':'2.0','id':'server-ping','result':{}}
        result={'tools':[{'name':'original','inputSchema':{'type':'object'}}]}
    else:
        count+=1
        result={'content':[{'type':'text','text':str(count)},{'type':'image','mimeType':'image/png','data':'AA=='}],'isError':mode=='tool_error'}
    send({'jsonrpc':'2.0','id':id,'result':result})
'''

class TransportTest(unittest.TestCase):
    def transport(self, mode='normal', **kwargs):
        value=StdioMcpTransport([sys.executable,'-u','-c',SERVER,mode],**kwargs)
        self.addCleanup(value.close)
        return value

    def init(self, mode='normal', **kwargs):
        value=self.transport(mode,**kwargs)
        self.assertEqual(value.initialize(client_name='test',client_version='1')['protocolVersion'],'2025-11-25')
        return value

    def test_handshake_catalog_ping_and_persistent_calls(self):
        value=self.init()
        self.assertEqual(value.request('tools/list')['tools'][0]['name'],'original')
        self.assertEqual(value.catalog_epoch,1)
        for i in (1,2):
            result=value.request('tools/call',{'name':'original','arguments':{}},write=True)
            self.assertEqual(result['content'][0]['text'],str(i))
            self.assertEqual(result['content'][1]['data'],'AA==')
        self.assertEqual(value.initialize(client_name='test',client_version='1'),value.negotiated)

    def test_preinitialize_and_protocol_mismatch(self):
        value=self.transport('bad_version')
        with self.assertRaisesRegex(StdioTransportError,'not_initialized'):
            value.request('tools/list')
        with self.assertRaisesRegex(StdioTransportError,'unsupported_negotiation'):
            value.initialize(client_name='test',client_version='1')

    def test_write_timeout_is_unknown_without_replay(self):
        value=self.init('timeout')
        with self.assertRaises(StdioTransportError) as caught:
            value.request('tools/call',write=True,timeout=.25)
        self.assertEqual(caught.exception.external_effect_state,'UNKNOWN')
        self.assertTrue(caught.exception.dispatched)
        with self.assertRaises(StdioTransportError) as second:
            value.request('tools/call',write=True)
        self.assertEqual(second.exception.external_effect_state,'NONE')

    def test_read_protocol_failure_does_not_claim_effect(self):
        for mode in ('malformed','oversize','wrong_id'):
            with self.subTest(mode=mode):
                value=self.init(mode,max_message_bytes=1024)
                with self.assertRaises(StdioTransportError) as caught:
                    value.request('tools/list',timeout=2)
                self.assertEqual(caught.exception.external_effect_state,'NONE')

    def test_rpc_error_is_not_success(self):
        value=self.init('rpc_error')
        with self.assertRaises(StdioTransportError) as caught:
            value.request('tools/call',write=True)
        self.assertEqual(caught.exception.code,'mcp_rpc_error')
        self.assertEqual(caught.exception.external_effect_state,'UNKNOWN')

    def test_preserves_original_tool_error_for_common_adapter(self):
        value=self.init('tool_error')
        self.assertTrue(value.request('tools/call',write=True)['isError'])

    def test_request_limit_before_dispatch(self):
        value=self.init(max_request_bytes=1024)
        with self.assertRaises(StdioTransportError) as caught:
            value.request('tools/call',{'value':'x'*2048},write=True)
        self.assertFalse(caught.exception.dispatched)
        self.assertEqual(caught.exception.external_effect_state,'NONE')
        self.assertEqual(value.request('tools/call')['content'][0]['text'],'1')

    def test_concurrent_requests_serialize(self):
        value=self.init()
        results=[]
        threads=[threading.Thread(target=lambda:results.append(value.request('tools/call')['content'][0]['text'])) for _ in range(8)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(timeout=4)
        self.assertEqual(sorted(results),list(map(str,range(1,9))))

    def test_close_interrupts_inflight_call(self):
        value=self.init('timeout')
        errors=[]
        def invoke():
            try:value.request('tools/call',write=True,timeout=30)
            except StdioTransportError as error:errors.append(error)
        thread=threading.Thread(target=invoke);thread.start()
        time.sleep(.15);value.close();thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors[0].external_effect_state,'UNKNOWN')
        self.assertIsNotNone(value.process.poll())

if __name__=='__main__':unittest.main()
