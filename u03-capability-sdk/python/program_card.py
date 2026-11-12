"""Programs Center display-only view of existing U01/U03/U04 evidence.

This is not a normative schema, a registry, host authority, or runtime.
"""
from __future__ import annotations
from copy import deepcopy
import re
from iwxa_capability_sdk import validate_descriptor, validate_status
from u01_bridge import observe_u01_capabilities

LEVELS = ('I0','I1','I2','I3','I4','I5')
CHANNELS = {
    'DISCOVERY': ('I0','U01'),
    'DATA': ('I1','U02'),
    'OPERATION': ('I2','U01'),
    'LINKED_WINDOW': ('I3','U04'),
    'EMBEDDED_VIEW': ('I4','U04'),
    'SHARED_LIVE_SURFACE': ('I4','U04'),
    'NATIVE_CONTRACT': ('I5','HOST'),
}
PROGRAM = 'IWXA_PROGRAM_CONNECTION/1.0.0'
STATUS = 'IWXA_CAPABILITY_STATUS/1.0.0'
UI = 'IWXA_UI_INTEGRATION/1.0.0'
BRIDGE = 'IWXA_PRESENTATION_BRIDGE/1.0.0'
SURFACE = 'IWXA_PRESENTATION_SURFACE/1.0.0'
_PROGRAM = re.compile(r'[A-Z][A-Z0-9_]{2,79}\Z')
_OP = re.compile(r'[a-z][a-z0-9_]*\Z')
_EPOCH = re.compile(r'[A-Za-z0-9_-]{16,128}\Z')
_ALLOWED_CHANNEL_FIELDS = {'id','kind','operation','target'}


def _invalid():
    raise ValueError('U03_PROGRAM_CARD_INPUT_INVALID')


def _reference(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 512


def _assert_ui(snapshot):
    if snapshot is None:
        return
    if not isinstance(snapshot, dict) or snapshot.get('schema') != UI:
        _invalid()
    levels = snapshot.get('levels')
    if not isinstance(levels, list) or len(levels) != len(LEVELS) or not all(
            isinstance(x, dict) and x.get('level') == name and type(x.get('verified')) is bool
            for x,name in zip(levels,LEVELS)):
        _invalid()
    actual = next((name for name,row in reversed(list(zip(LEVELS,levels)))
                  if row['verified']), None)
    if snapshot.get('highestVerified') != actual:
        _invalid()


def _assert_channels(descriptor,channels):
    if not isinstance(channels,list) or len(channels) != len(descriptor['provides']):
        _invalid()
    declared = {item['id']:item['version'] for item in descriptor['provides']}
    seen, operations = set(),set()
    for row in channels:
        if not isinstance(row,dict) or row.get('kind') not in CHANNELS or \
                not set(row) <= _ALLOWED_CHANNEL_FIELDS or \
                row.get('id') not in declared or row['id'] in seen or \
                ('target' in row and type(row['target']) is not bool):
            _invalid()
        if row['kind']=='OPERATION':
            op = row.get('operation')
            if not isinstance(op,str) or not _OP.fullmatch(op) or op in operations:
                _invalid()
            operations.add(op)
        elif 'operation' in row:
            _invalid()
        seen.add(row['id'])


def _assert_statuses(statuses):
    if not isinstance(statuses,list) or len(statuses)>128 or \
            not all(validate_status(x) for x in statuses) or \
            len({x['id'] for x in statuses}) != len(statuses):
        _invalid()


def _initial(row,version):
    level,owner=CHANNELS[row['kind']]
    target=row.get('target') is True
    return {'id':row['id'],'kind':row['kind'],'level':level,
            'version':version,'owner':owner,'state':'DECLARED','verified':False,
            'executionConfirmed':False,'firstUseCandidate':False,
            'canOfferInvocation':False,
            'observationSource':None,'observationRef':None,
            'reason':'TARGET_NOT_QUALIFIED' if target else 'OBSERVATION_MISSING',
            'target':target}


def _state(row,state,reason=None,source=None,reference=None):
    row.update({'state':state,'reason':reason,
                'verified':state in ('AVAILABLE','EXECUTABLE'),
                'observationSource':source,
                'observationRef':reference if _reference(reference) else None})
    return row


def _data(row,status,ui):
    if status is None: return row
    if not status['available']:
        return _state(row,'BLOCKED',status.get('reason','CAPABILITY_UNAVAILABLE'),STATUS)
    if not status.get('observationRef') or not status.get('version'):
        return _state(row,'BLOCKED','CAPABILITY_UNVERIFIED',STATUS)
    if status['version'] != row['version']:
        return _state(row,'BLOCKED','CAPABILITY_VERSION_MISMATCH',STATUS)
    if row['kind']=='NATIVE_CONTRACT' and not (ui is not None and ui['levels'][5]['verified']):
        return _state(row,'BLOCKED','NATIVE_CONTRACT_NOT_OBSERVED',STATUS)
    return _state(row,'AVAILABLE',None,STATUS,status['observationRef'])


def _discovered(row,inspect,program_id):
    if not isinstance(inspect,dict) or inspect.get('schema')!=PROGRAM:
        return row
    if inspect.get('program_id')!=program_id:
        return _state(row,'BLOCKED','DISCOVERY_IDENTITY_MISMATCH',PROGRAM)
    if inspect.get('status')=='DISCOVERED' and inspect.get('installed') is True:
        return _state(row,'AVAILABLE',None,PROGRAM)
    if inspect.get('status')=='CONFIGURED':
        return _state(row,'INTEGRATED','CONFIGURED_NOT_INSTALLED',PROGRAM)
    return _state(row,'BLOCKED',inspect.get('status','PROGRAM_NOT_DISCOVERED'),PROGRAM)


def _invocation(row,observed,status,operation,program_id):
    if observed is None:return row
    valid=isinstance(observed,dict) and observed.get('schema')==PROGRAM and \
        observed.get('program_id')==program_id and observed.get('status')=='DECLARED' and \
        observed.get('connected') is True and \
        isinstance(observed.get('connection_epoch'),str) and \
        _EPOCH.fullmatch(observed['connection_epoch']) is not None and \
        isinstance(observed.get('operations'),list)
    found=next((x for x in observed['operations']
                if isinstance(x,dict) and x.get('name')==operation),None) if valid else None
    # Existing U01 bridge already verifies the source, transport and receipt.
    # A current execution proof is a UI hint, never a host invocation grant.
    if status['available']:
        if status.get('version')!=row['version']:
            return _state(row,'BLOCKED','CAPABILITY_VERSION_MISMATCH',PROGRAM)
        if not found or found.get('declared') is not True or found.get('verified') is not True:
            return _state(row,'BLOCKED','OWNER_SOURCE_INVALID',PROGRAM)
        row['canOfferInvocation']=True
        row['executionConfirmed']=True
        return _state(row,'EXECUTABLE',None,PROGRAM,status.get('observationRef'))
    first=bool(found and found.get('declared') is True and
               found.get('verified') is False and
               status.get('reason')=='operation-unverified')
    receipt_missing=bool(found and found.get('declared') is True and
                         found.get('verified') is True and
                         status.get('reason')=='execution-receipt-missing')
    if first or receipt_missing:
        row['canOfferInvocation']=True
        row['firstUseCandidate']=first
        return _state(row,'INTEGRATED',status.get('reason'),PROGRAM)
    return _state(row,'BLOCKED',status.get('reason','OPERATION_UNAVAILABLE'),PROGRAM)



def _owner_operation(row, owner, operation, program_id, raw_status):
    """Display the current Core-owned projection without calling U01 twice."""
    source=owner.get('source')
    if not isinstance(source,dict) or source.get('schema')!=PROGRAM or \
            source.get('program_id')!=program_id:
        return _state(row,'BLOCKED','SOURCE_IDENTITY_MISMATCH',PROGRAM)
    live=source.get('status')=='DECLARED' and source.get('connected') is True and \
        isinstance(source.get('connection_epoch'),str) and \
        _EPOCH.fullmatch(source['connection_epoch']) is not None and \
        isinstance(source.get('operations'),list)
    if not live:return _state(row,'BLOCKED','source-unavailable',PROGRAM)
    # Reuse the existing U01 contract validation for this in-memory snapshot.
    # No extra U01 capabilities() call or execution.
    if not isinstance(raw_status,dict) or raw_status.get('reason') not in (
            'operation-unverified','execution-receipt-missing'):
        return _state(row,'BLOCKED',raw_status.get('reason','OWNER_SOURCE_INVALID')
                      if isinstance(raw_status,dict) else 'OWNER_SOURCE_INVALID',PROGRAM)
    raw=[x for x in source['operations'] if isinstance(x,dict) and x.get('name')==operation]
    if len(raw)!=1 or raw[0].get('declared') is not True:
        return _state(row,'BLOCKED','OWNER_OPERATION_AMBIGUOUS' if len(raw)>1 else
                      'operation-unsupported',PROGRAM)
    matched=[x for x in owner['operations'] if isinstance(x,dict) and
             (x.get('operation')==operation or x.get('capability_id')==row['id'])]
    if len(matched)!=1 or matched[0].get('operation')!=operation or \
            matched[0].get('capability_id')!=row['id']:
        return _state(row,'BLOCKED','OWNER_OPERATION_MISMATCH',STATUS)
    item=matched[0]
    observation=item.get('observation')
    if type(item.get('can_offer_invocation')) is not bool or \
            type(item.get('execution_verified')) is not bool or \
            not validate_status(observation) or observation['id']!=row['id']:
        return _state(row,'BLOCKED','OWNER_OBSERVATION_INVALID',STATUS)
    if item['execution_verified'] != observation['available']:
        return _state(row,'BLOCKED','OWNER_OBSERVATION_INCONSISTENT',STATUS)
    declared=raw[0]
    can_offer=item['can_offer_invocation'] is True
    if observation['available']:
        if declared.get('verified') is not True or not can_offer:
            return _state(row,'BLOCKED','OWNER_OBSERVATION_INCONSISTENT',STATUS)
        if observation.get('version')!=row['version']:
            return _state(row,'BLOCKED','CAPABILITY_VERSION_MISMATCH',STATUS)
        if not _reference(observation.get('observationRef')):
            return _state(row,'BLOCKED','CAPABILITY_UNVERIFIED',STATUS)
        row['executionConfirmed']=True
        row['canOfferInvocation']=True  # Display hint only; P01 owns execution.
        return _state(row,'EXECUTABLE',None,STATUS,observation['observationRef'])
    if can_offer and ((declared.get('verified') is False and
                       observation.get('reason')=='operation-unverified') or
                      (declared.get('verified') is True and
                       observation.get('reason')=='execution-receipt-missing')):
        row['canOfferInvocation']=True
        row['firstUseCandidate']=declared.get('verified') is False
        return _state(row,'INTEGRATED',observation['reason'],STATUS)
    return _state(row,'BLOCKED',observation.get('reason','OPERATION_UNAVAILABLE'),STATUS)


def _present(row,presentation):
    if not isinstance(presentation,dict):return row
    caps=presentation.get('capabilities')
    if not isinstance(caps,dict) or caps.get('schema')!=BRIDGE or \
            not isinstance(caps.get('capabilities'),list):
        return row
    mode=row['kind']
    cap=next((x for x in caps['capabilities']
              if isinstance(x,dict) and x.get('mode')==mode),None)
    if cap is None:return row
    if cap.get('supported') is not True:
        return _state(row,'BLOCKED',cap.get('reason','PRESENTATION_UNSUPPORTED'),BRIDGE)
    observations=presentation.get('observations') or {}
    actual=observations.get(mode) if isinstance(observations,dict) else None
    if not isinstance(actual,dict):return row
    binding=actual.get('binding')
    if not isinstance(binding,dict) or binding.get('appRef')!=presentation.get('appRef') or \
            binding.get('hostRef')!=presentation.get('hostRef') or \
            not _reference(binding.get('bindingRef')):
        return _state(row,'BLOCKED','PRESENTATION_BINDING_MISMATCH',BRIDGE)
    if mode=='LINKED_WINDOW':
        if actual.get('schema')!=BRIDGE or not _reference(binding.get('windowRef')) or \
                type(binding.get('processId')) is not int or binding['processId']<=0:
            return _state(row,'BLOCKED','WINDOW_PROOF_INVALID',BRIDGE)
        if actual.get('status')=='WINDOW_FOCUSED' and actual.get('focused') is True:
            return _state(row,'AVAILABLE',None,BRIDGE,binding['bindingRef'])
        if actual.get('status')=='WINDOW_OBSERVED':
            return _state(row,'INTEGRATED','WINDOW_OBSERVED_FOCUS_NOT_CONFIRMED',
                          BRIDGE,binding['bindingRef'])
        return _state(row,'BLOCKED',actual.get('status','WINDOW_NOT_OBSERVED'),BRIDGE)
    proof=binding.get('presentation')
    if actual.get('schema')!=SURFACE or binding.get('mode')!=mode or \
            binding.get('official') is not True or binding.get('verified') is not True or \
            binding.get('existingRuntime') is not True or binding.get('spawned') is not False or \
            binding.get('mounted') is not True or not _reference(binding.get('mountRef')) or \
            not isinstance(proof,dict) or proof.get('official') is not True:
        return _state(row,'BLOCKED','OFFICIAL_SURFACE_PROOF_INVALID',SURFACE)
    if actual.get('status')=='SURFACE_LIVE':
        return _state(row,'AVAILABLE',None,SURFACE,binding['bindingRef'])
    if actual.get('status')=='SURFACE_MOUNTED':
        return _state(row,'INTEGRATED','SURFACE_LIVE_RECHECK_REQUIRED',
                      SURFACE,binding['bindingRef'])
    return _state(row,'BLOCKED',actual.get('status','SURFACE_NOT_LIVE'),SURFACE)


def project_program_card(*,programId,descriptor,channels,statuses=None,u01=None,
                         ui=None,presentation=None):
    if not validate_descriptor(descriptor) or not isinstance(programId,str) or \
            _PROGRAM.fullmatch(programId) is None:
        _invalid()
    _assert_channels(descriptor,channels)
    statuses=[] if statuses is None else statuses
    _assert_statuses(statuses)
    _assert_ui(ui)
    if u01 is not None and not isinstance(u01,dict):_invalid()
    if presentation is not None and not isinstance(presentation,dict):_invalid()
    status_index={row['id']:row for row in statuses}
    declared={row['id']:row['version'] for row in descriptor['provides']}
    ops=[row for row in channels if row['kind']=='OPERATION']
    owner=u01.get('ownerProjection') if isinstance(u01,dict) else None
    if isinstance(u01,dict) and 'ownerProjection' in u01:
        if 'capabilities' in u01 or 'receipts' in u01 or \
                not isinstance(owner,dict) or owner.get('program_id')!=programId or \
                owner.get('execution_authority_granted') is not False or \
                not isinstance(owner.get('operations'),list) or len(owner['operations'])>128:
            _invalid()
    owner_status={}
    if owner is not None and ops:
        source=owner.get('source')
        class OwnerSnapshot:
            def capabilities(self):return source
        bindings={'programId':programId,'bindings':[{
            'id':row['id'],'operation':row['operation'],'version':declared[row['id']]
        } for row in ops]}
        owner_status={row['id']:row for row in observe_u01_capabilities(
            OwnerSnapshot(),bindings)}
    projected={}
    if ops and isinstance(u01,dict) and u01.get('capabilities') is not None:
        source=u01['capabilities']
        class Connection:
            def capabilities(self):return source
        bindings={'programId':programId,'bindings':[{
            'id':row['id'],'operation':row['operation'],'version':declared[row['id']]
        } for row in ops],'receipts':u01.get('receipts') or {}}
        projected={row['id']:row for row in observe_u01_capabilities(Connection(),bindings)}
    result=[]
    for binding in channels:
        row=_initial(binding,declared[binding['id']])
        if binding['kind']=='DISCOVERY':
            result.append(_discovered(row,u01.get('inspect') if isinstance(u01,dict) else None,programId))
        elif binding['kind'] in ('DATA','NATIVE_CONTRACT'):
            result.append(_data(row,status_index.get(binding['id']),ui))
        elif binding['kind']=='OPERATION':
            if owner is not None:
                result.append(_owner_operation(row,owner,binding['operation'],programId,
                                               owner_status.get(binding['id'])))
            else:
                result.append(_invocation(row,u01['capabilities'],projected[binding['id']],
                             binding['operation'],programId) if binding['id'] in projected else row)
        else:
            result.append(_present(row,presentation))
    return {'programId':programId,'levels':deepcopy(ui['levels']) if ui is not None else None,
            'highestVerified':ui.get('highestVerified') if ui is not None else None,
            'capabilities':result}
