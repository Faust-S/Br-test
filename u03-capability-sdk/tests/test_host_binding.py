"""Host-owned Python U01 port + U03 attach/detach composition tests."""
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'python'))
from host_binding import U03HostBinding

DESC={'schema':'IWXA_MODULE_DESCRIPTOR/1.0.0','id':'iwxa.u03.host.demo',
      'version':'0.1.0','provides':[],
      'requires':[{'id':'iwxa.cli.evaluate','range':'=1.0.0'}],
      'hostCompatibility':[{'hostId':'iwxa.host.agent','range':'=0.1.0'}]}
CFG={'programId':'JQ_CLI_V1','bindings':[
    {'operation':'evaluate','id':'iwxa.cli.evaluate','version':'1.0.0'}]}
SCHEMA='IWXA_PROGRAM_CONNECTION/1.0.0'
EPOCH_A='hostepoch000000001'
EPOCH_B='hostepoch000000002'


def receipt(epoch):
    return {'observationRef':'host:receipt:'+epoch,
            'observation':{'schema':SCHEMA,'program_id':'JQ_CLI_V1',
                           'transport':'cli','status':'EXECUTED','executed':True,
                           'connection_epoch':epoch,'operation':'evaluate','output':'42'}}


class Connection:
    def __init__(self):
        self.epoch=EPOCH_A
        self.alive=True
        self.calls=0

    def capabilities(self):
        self.calls+=1
        if not self.alive:
            raise RuntimeError('host unavailable')
        return {'schema':SCHEMA,'program_id':'JQ_CLI_V1','transport':'cli',
                'status':'DECLARED','connected':True,'connection_epoch':self.epoch,
                'operations':[{'name':'evaluate','declared':True,'verified':True}]}


class BindingTests(unittest.TestCase):
    def test_attach_host_authority_detach_epoch_and_failure(self):
        connection=Connection()
        config={**CFG,'receipts':{'evaluate':receipt(EPOCH_A)}}
        attached=[]
        detached=[]
        approved=[True]
        def attach(_descriptor,_host,_observations):
            if not approved[0]:
                return {'attached':False,'reason':'host-denied'}
            attached.append(True)
            return {'attached':True,'bindingRef':'host:'+str(len(attached))}
        port=U03HostBinding(DESC,{'hostId':'iwxa.host.agent','version':'0.1.0'},
                           connection,lambda:config,attach,
                           lambda ref:detached.append(ref) or {'detached':True})
        self.assertEqual(port.describe()['id'],DESC['id'])
        self.assertTrue(port.assess()['compatible'])
        self.assertTrue(port.attach()['attached'])
        approved[0]=False
        self.assertFalse(port.attach()['attached'])
        self.assertEqual(len(attached),1)
        approved[0]=True
        self.assertTrue(port.detach('host:1')['detached'])
        self.assertEqual(detached,['host:1'])
        connection.epoch=EPOCH_B
        stale=port.attach()
        self.assertFalse(stale['attached'])
        self.assertEqual(stale['assessment']['reasons'],
                         ['U03_CAPABILITY_UNAVAILABLE:iwxa.cli.evaluate'])
        config['receipts']={'evaluate':receipt(EPOCH_B)}
        self.assertTrue(port.attach()['attached'])
        connection.alive=False
        self.assertFalse(port.attach()['attached'])
        self.assertEqual(len(attached),2)

    def test_bad_host_version_never_attaches(self):
        connection=Connection()
        calls=[]
        port=U03HostBinding(DESC,{'hostId':'iwxa.host.agent','version':'0.2.0'},
                           connection,lambda:{**CFG,'receipts':{'evaluate':receipt(EPOCH_A)}},
                           lambda *_:calls.append(True),lambda *_:None)
        self.assertEqual(port.assess()['reasons'],['U03_HOST_VERSION_INCOMPATIBLE'])
        self.assertFalse(port.attach()['attached'])
        self.assertEqual(calls,[])

    def test_unbound_port_is_rejected(self):
        with self.assertRaisesRegex(TypeError,'U03_HOST_BINDING_PORT_REQUIRED'):
            U03HostBinding(DESC,{},None,lambda:CFG,lambda *_:None,lambda *_:None)

if __name__=='__main__':
    unittest.main()
