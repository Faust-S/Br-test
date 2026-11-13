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

class AttachRaceTests(unittest.TestCase):
    def test_session_changes_inside_host_hook_must_detach(self):
        connection=Connection()
        removed=[]
        cfg={**CFG,'receipts':{'evaluate':receipt(EPOCH_A)}}
        def attach(*_):
            connection.epoch=EPOCH_B
            return {'attached':True,'bindingRef':'host:race'}
        bridge=U03HostBinding(DESC,{'hostId':'iwxa.host.agent','version':'0.1.0'},
                             connection,lambda:cfg,attach,
                             lambda ref:removed.append(ref) or {'detached':True})
        outcome=bridge.attach()
        self.assertFalse(outcome['attached'])
        self.assertEqual(outcome['assessment']['reasons'],
                         ['U03_CAPABILITY_UNAVAILABLE:iwxa.cli.evaluate'])
        self.assertEqual(outcome['rollback'],'DETACHED')
        self.assertEqual(outcome['hostState'],'DETACHED')
        self.assertEqual(removed,['host:race'])

    def test_new_compatible_receipt_still_invalidates_prior_attach(self):
        connection=Connection()
        cfg={**CFG,'receipts':{'evaluate':receipt(EPOCH_A)}}
        removed=[]
        def attach(*_):
            connection.epoch=EPOCH_B
            cfg['receipts']={'evaluate':receipt(EPOCH_B)}
            return {'attached':True,'bindingRef':'host:changed'}
        bridge=U03HostBinding(DESC,{'hostId':'iwxa.host.agent','version':'0.1.0'},
                             connection,lambda:cfg,attach,
                             lambda ref:removed.append(ref) or {'detached':True})
        outcome=bridge.attach()
        self.assertFalse(outcome['attached'])
        self.assertEqual(outcome['assessment']['reasons'],
                         ['U03_HOST_OBSERVATION_CHANGED'])
        self.assertEqual(outcome['rollback'],'DETACHED')
        self.assertEqual(removed,['host:changed'])

    def test_missing_ref_and_rollback_failures_are_visible(self):
        for mode in ('missing-ref','refused','exception'):
            with self.subTest(mode=mode):
                connection=Connection()
                detach_calls=[]
                def attach(*_):
                    connection.epoch=EPOCH_B
                    return {'attached':True,**(
                        {} if mode=='missing-ref' else {'bindingRef':'host:uncertain'})}
                def detach(ref):
                    detach_calls.append(ref)
                    if mode=='exception':
                        raise RuntimeError('host unavailable')
                    return {'detached':False}
                bridge=U03HostBinding(DESC,{'hostId':'iwxa.host.agent','version':'0.1.0'},
                                     connection,lambda:{**CFG,'receipts':{'evaluate':receipt(EPOCH_A)}},
                                     attach,detach)
                outcome=bridge.attach()
                self.assertFalse(outcome['attached'])
                self.assertEqual(outcome['rollback'],'UNCONFIRMED')
                self.assertEqual(outcome['hostState'],'UNKNOWN')
                self.assertEqual(len(detach_calls),0 if mode=='missing-ref' else 1)

    def test_post_hook_failure_rolls_back(self):
        connection=Connection()
        calls=[]
        removed=[]
        def bindings():
            calls.append(True)
            if len(calls)>1:
                raise RuntimeError('host mapping unavailable')
            return {**CFG,'receipts':{'evaluate':receipt(EPOCH_A)}}
        bridge=U03HostBinding(DESC,{'hostId':'iwxa.host.agent','version':'0.1.0'},
                             connection,bindings,
                             lambda *_:{'attached':True,'bindingRef':'host:lost'},
                             lambda ref:removed.append(ref) or {'detached':True})
        outcome=bridge.attach()
        self.assertFalse(outcome['attached'])
        self.assertEqual(outcome['assessment']['reasons'],
                         ['U03_HOST_OBSERVATION_LOST'])
        self.assertEqual(outcome['rollback'],'DETACHED')
        self.assertEqual(removed,['host:lost'])

class ConcurrentAndIncompleteReceiptTests(unittest.TestCase):
    def test_parallel_attach_is_rejected_without_second_host_call(self):
        import threading
        connection=Connection()
        entered=threading.Event()
        release=threading.Event()
        calls=[]
        outcomes=[]
        def attach(*_):
            calls.append(1)
            entered.set()
            if not release.wait(3):
                raise AssertionError('host-test-gate-timeout')
            return {'attached':True,'bindingRef':'host:concurrent'}
        bridge=U03HostBinding(DESC,{'hostId':'iwxa.host.agent','version':'0.1.0'},
            connection,lambda:{**CFG,'receipts':{'evaluate':receipt(EPOCH_A)}},
            attach,lambda *_:{'detached':True})
        thread=threading.Thread(target=lambda:outcomes.append(bridge.attach()))
        try:
            thread.start()
            self.assertTrue(entered.wait(2),'first host attach must enter')
            refused=bridge.attach()
            self.assertFalse(refused['attached'])
            self.assertEqual(refused['assessment']['reasons'],['U03_HOST_ATTACH_IN_PROGRESS'])
            self.assertIsNone(refused['hostReceipt'])
            self.assertEqual(len(calls),1)
        finally:
            release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertTrue(outcomes[0]['attached'])
        self.assertTrue(bridge.attach()['attached'])
        self.assertEqual(len(calls),2)

    def test_positive_receipt_without_binding_ref_is_not_assumed_attached(self):
        for missing in (None,'','  ',12):
            with self.subTest(bindingRef=missing):
                connection=Connection()
                detached=[]
                def attach(*_):
                    return {'attached':True,'bindingRef':missing}
                bridge=U03HostBinding(DESC,{'hostId':'iwxa.host.agent','version':'0.1.0'},
                    connection,lambda:{**CFG,'receipts':{'evaluate':receipt(EPOCH_A)}},
                    attach,lambda ref:detached.append(ref) or {'detached':True})
                result=bridge.attach()
                self.assertFalse(result['attached'])
                self.assertEqual(result['assessment']['reasons'],['U03_HOST_BINDING_REF_MISSING'])
                self.assertEqual(result['rollback'],'UNCONFIRMED')
                self.assertEqual(result['hostState'],'UNKNOWN')
                self.assertEqual(connection.calls,1)
                self.assertEqual(detached,[])

    def test_host_denial_does_not_poison_next_attach(self):
        connection=Connection()
        permitted=[False]
        actions=[]
        def attach(*_):
            actions.append(1)
            if not permitted[0]:
                return {'attached':False,'reason':'owner-denied'}
            return {'attached':True,'bindingRef':'host:later'}
        bridge=U03HostBinding(DESC,{'hostId':'iwxa.host.agent','version':'0.1.0'},
            connection,lambda:{**CFG,'receipts':{'evaluate':receipt(EPOCH_A)}},
            attach,lambda *_:{'detached':True})
        denied=bridge.attach()
        self.assertFalse(denied['attached'])
        self.assertEqual(denied['hostReceipt']['reason'],'owner-denied')
        self.assertNotIn('rollback',denied)
        permitted[0]=True
        self.assertTrue(bridge.attach()['attached'])
        self.assertEqual(len(actions),2)

class OwnerIdentityRaces(unittest.TestCase):
    def test_epoch_program_and_operation_change_roll_back_even_with_same_receipt_ref(self):
        for change in ('epoch','program','operation'):
            with self.subTest(change=change):
                state={'epoch':EPOCH_A,'programId':'JQ_CLI_V1','operation':'evaluate'}
                calls={'read':0,'attach':0}
                detaches=[]
                class DynamicConnection:
                    def capabilities(self):
                        calls['read']+=1
                        return {'schema':SCHEMA,'program_id':state['programId'],
                                'transport':'cli','status':'DECLARED','connected':True,
                                'connection_epoch':state['epoch'],
                                'operations':[{'name':state['operation'],
                                               'declared':True,'verified':True}]}
                def get_bindings():
                    return {'programId':state['programId'],
                            'bindings':[{'operation':state['operation'],
                                         'id':'iwxa.cli.evaluate','version':'1.0.0'}],
                            'receipts':{state['operation']:{
                                'observationRef':'host:stable:receipt',
                                'observation':{**receipt(state['epoch'])['observation'],
                                  'program_id':state['programId'],
                                  'operation':state['operation']}}}}
                def attach(*_):
                    calls['attach']+=1
                    if change=='epoch':state['epoch']=EPOCH_B
                    elif change=='program':state['programId']='YQ_CLI_V1'
                    else:state['operation']='recompute'
                    return {'attached':True,'bindingRef':'host:must-rollback'}
                def detach(ref):
                    detaches.append(ref)
                    return {'detached':True}
                bridge=U03HostBinding(
                    DESC,{'hostId':'iwxa.host.agent','version':'0.1.0'},
                    DynamicConnection(),get_bindings,attach,detach)
                outcome=bridge.attach()
                self.assertFalse(outcome['attached'])
                self.assertEqual(outcome['assessment']['reasons'],
                                 ['U03_HOST_OBSERVATION_CHANGED'])
                self.assertEqual(outcome['rollback'],'DETACHED')
                self.assertEqual(outcome['hostState'],'DETACHED')
                self.assertEqual(detaches,['host:must-rollback'])
                self.assertEqual(calls,{'read':2,'attach':1})


class OwnerDetachReceiptTests(unittest.TestCase):
    def test_roll_back_requires_matching_ref_if_owner_returns_one(self):
        cases=(
            ('wrong-ref',{'detached':True,'bindingRef':'host:someone-else'},'UNCONFIRMED'),
            ('null-ref',{'detached':True,'bindingRef':None},'UNCONFIRMED'),
            ('matching-ref',{'detached':True,'bindingRef':'host:owned'},'DETACHED'),
            ('legacy-without-ref',{'detached':True},'DETACHED'),
        )
        for label,confirmation,expected in cases:
            with self.subTest(label=label):
                connection=Connection()
                calls=[]
                def attach(*_):
                    connection.epoch=EPOCH_B
                    return {'attached':True,'bindingRef':'host:owned'}
                def detach(ref):
                    calls.append(ref)
                    return confirmation
                bridge=U03HostBinding(
                    DESC,{'hostId':'iwxa.host.agent','version':'0.1.0'},
                    connection,lambda:{**CFG,'receipts':{'evaluate':receipt(EPOCH_A)}},
                    attach,detach)
                result=bridge.attach()
                self.assertFalse(result['attached'])
                self.assertEqual(result['rollback'],expected)
                self.assertEqual(result['hostState'],
                                 'DETACHED' if expected=='DETACHED' else 'UNKNOWN')
                self.assertEqual(result['assessment']['reasons'],
                                 ['U03_CAPABILITY_UNAVAILABLE:iwxa.cli.evaluate'])
                self.assertEqual(connection.calls,2)
                self.assertEqual(calls,['host:owned'])


class MutableSelectionDuringReadTests(unittest.TestCase):
    def test_switch_program_operation_or_receipt_during_first_read_is_rejected(self):
        for change in ('program','operation','receipt'):
            with self.subTest(change=change):
                from copy import deepcopy
                selection={**deepcopy(CFG),'receipts':{'evaluate':receipt(EPOCH_A)}}
                counts={'reads':0,'attaches':0,'detaches':0}
                class SwitchingConnection:
                    def capabilities(self):
                        counts['reads']+=1
                        if counts['reads']==1:
                            if change=='program':
                                selection['programId']='ALT_CLI_V1'
                                selection['receipts']['evaluate']['observation']['program_id']='ALT_CLI_V1'
                            elif change=='operation':
                                selection['bindings'][0]['operation']='recompute'
                                old=receipt(EPOCH_A)
                                old['observation']['operation']='recompute'
                                selection['receipts']={'recompute':old}
                            else:
                                selection['receipts']['evaluate']['observation']['output']='43'
                        return {'schema':SCHEMA,
                                'program_id':selection['programId'],
                                'transport':'cli','status':'DECLARED','connected':True,
                                'connection_epoch':EPOCH_A,
                                'operations':[{'name':selection['bindings'][0]['operation'],
                                               'declared':True,'verified':True}]}
                def attach(*_):
                    counts['attaches']+=1
                    return {'attached':True,'bindingRef':'host:wrong-selection'}
                def detach(ref):
                    counts['detaches']+=1
                    return {'detached':True}
                bridge=U03HostBinding(
                    DESC,{'hostId':'iwxa.host.agent','version':'0.1.0'},
                    SwitchingConnection(),lambda:selection,attach,detach)
                outcome=bridge.attach()
                self.assertFalse(outcome['attached'])
                self.assertEqual(outcome['assessment']['reasons'],
                                 ['U03_HOST_OBSERVATION_CHANGED'])
                self.assertIsNone(outcome['hostReceipt'])
                self.assertEqual(counts,{'reads':1,'attaches':0,'detaches':0})


class MutableReceiptOnSecondObservation(unittest.TestCase):
    def test_mutation_after_host_action_rolls_back_instead_of_accepting_stale_proof(self):
        from copy import deepcopy
        selected={**deepcopy(CFG),'receipts':{'evaluate':receipt(EPOCH_A)}}
        counters={'reads':0,'attaches':0}
        detach_refs=[]
        class ConnectionWithReceiptChange:
            def capabilities(self):
                counters['reads']+=1
                if counters['reads']==2:
                    selected['receipts']['evaluate']['observation']['output']='43'
                return {'schema':SCHEMA,'program_id':'JQ_CLI_V1',
                        'transport':'cli','status':'DECLARED','connected':True,
                        'connection_epoch':EPOCH_A,
                        'operations':[{'name':'evaluate','declared':True,'verified':True}]}
        def attach(*_):
            counters['attaches']+=1
            return {'attached':True,'bindingRef':'host:one-attach'}
        def detach(ref):
            detach_refs.append(ref)
            return {'detached':True,'bindingRef':ref}
        bridge=U03HostBinding(DESC,{'hostId':'iwxa.host.agent','version':'0.1.0'},
                             ConnectionWithReceiptChange(),lambda:selected,
                             attach,detach)
        result=bridge.attach()
        self.assertFalse(result['attached'])
        self.assertEqual(result['assessment']['reasons'],
                         ['U03_HOST_OBSERVATION_CHANGED'])
        self.assertEqual(result['rollback'],'DETACHED')
        self.assertEqual(result['hostState'],'DETACHED')
        self.assertEqual(detach_refs,['host:one-attach'])
        self.assertEqual(counters,{'reads':2,'attaches':1})
