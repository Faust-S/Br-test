"""Independent Python consumer of current U02/K02 + owner-executed U01 jq.

No external invoke and no second Task/source read; validates host-owned
evidence from the JavaScript public qualification.
"""
from __future__ import annotations
import hashlib
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'python'))
from iwxa_capability_sdk import assess
from u01_bridge import observe_u01_capabilities

def require(condition,reason):
    if not condition:raise AssertionError(reason)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def main():
    data=json.load(sys.stdin)
    source=data['source']
    checkpoint=data['checkpoint']
    require(len(source)==2 and len(checkpoint['sources'])==2,'SOURCE_COUNT_WRONG')
    require(checkpoint['schema']=='IWXA_U02_SELECTED_PROJECT_CHECKPOINT/1',
            'CHECKPOINT_SCHEMA_WRONG')
    require({x['key'] for x in source}=={'orders','instructions'},
            'UNEXPECTED_TASK_INPUTS')
    pinned={x['key']:x for x in checkpoint['sources']}
    for row in source:
        key=row['key']
        old=pinned[key]
        require(row['providerId']==old['pin']['providerId'] and
                row['sourceRef']==old['pin']['sourceRef'] and
                row['revision']==old['pin']['revision'] and
                row['contentRef']==old['contentRef'] and
                row['authority']=='OBSERVATION_ONLY' and
                old['pin']['authority']=='OBSERVATION_ONLY' and
                row['revision'].startswith('sha256:'),
                'SOURCE_PROVENANCE_MISMATCH:'+key)

    plan=data['plan']
    require(len(plan['planDigest'])==64 and
            plan['invocationAuthority']=='HOST_ONLY' and
            plan['invocationState']=='NOT_INVOKED' and
            plan['proofOfInvoke'] is None,'K02_PLAN_PRETENDED_EXECUTION')
    result=data['result']
    require(result['grandTotalCents']==data['expectedGrandTotalCents']==
            sum(line['totalCents'] for line in result['lines']),
            'CALCULATION_RESULT_INCONSISTENT')
    require([x['documentId'] for x in result['lines']]==['INV-1042','INV-1043'],
            'CALCULATION_DOCUMENT_REBOUND')
    canonical={'lines':[{
        'documentId':x['documentId'],'subtotalCents':x['subtotalCents'],
        'discountCents':x['discountCents'],'totalCents':x['totalCents']
    } for x in result['lines']],'grandTotalCents':result['grandTotalCents']}
    canonical_digest=sha(json.dumps(canonical,ensure_ascii=False,
                                   separators=(',',':')).encode('utf-8'))
    require(canonical_digest==data['canonicalResultSha256'],
            'CANONICAL_RESULT_NOT_PORTABLE')
    current=data['capabilities']
    require(current['schema']=='IWXA_PROGRAM_CONNECTION/1.0.0' and
            current['program_id']==data['programId'] and
            current['status']=='DECLARED' and current['connected'] is True,
            'U01_HOST_IDENTITY_MISMATCH')
    receipts=data['receipts']
    receipt=receipts['calculate']
    observation=receipt['observation']
    require(observation['program_id']==data['programId'] and
            observation['status']=='EXECUTED' and
            observation['executed'] is True and
            observation['connection_epoch']==current['connection_epoch'] and
            sha(observation['output'].encode('utf-8'))==data['outputSha256'] and
            json.loads(observation['output'])==result,
            'U01_RECEIPT_OUTPUT_MISMATCH')
    require(isinstance(receipt['observationRef'],str) and
            receipt['observationRef'].startswith('u03:qualification:sha256:'),
            'HOST_RECEIPT_REFERENCE_MISSING')

    class ReadonlyConnection:
        def capabilities(self):
            return current
    config={'programId':data['programId'],'bindings':[{
        'operation':'calculate','id':'invoice.calculate','version':'1.0.0'
    }],'receipts':receipts}
    resultStatus=observe_u01_capabilities(ReadonlyConnection(),config)[0]
    require(resultStatus==data['status'] and resultStatus['available'] is True and
            resultStatus['observationRef']==receipt['observationRef'],
            'U03_PYTHON_RECEIPT_DIVERGED')
    descriptor=data['moduleDescriptor']
    host=data['host']
    require(assess(descriptor,host,[resultStatus])['compatible'] is True,
            'U03_PYTHON_HOST_NOT_COMPATIBLE')
    require(assess(descriptor,{**host,'version':'0.4.0'},[resultStatus])['compatible'] is False,
            'U03_PYTHON_BAD_HOST_VERSION_ACCEPTED')

    missing=observe_u01_capabilities(ReadonlyConnection(),{**config,'receipts':{}})[0]
    require(missing['available'] is False and
            missing['reason']=='execution-receipt-missing',
            'U03_PYTHON_UNOWNED_EXECUTION')
    stale=json.loads(json.dumps(receipts))
    stale['calculate']['observation']['connection_epoch']='stale-epoch-number'
    outdated=observe_u01_capabilities(ReadonlyConnection(),{**config,'receipts':stale})[0]
    require(outdated['available'] is False and
            outdated['reason']=='execution-receipt-missing',
            'U03_PYTHON_STALE_EPOCH_ACCEPTED')
    return {'schema':'IWXA_U03_AGENT05_PYTHON_CONSUMER/1','status':'PASS',
            'receiptRef':receipt['observationRef'],
            'grandTotalCents':result['grandTotalCents'],
            'canonicalResultSha256':canonical_digest,'invocations':0,
            'sourceCount':len(source),'authorityGranted':False}

if __name__=='__main__':
    try:
        print(json.dumps(main(),sort_keys=True))
    except Exception as error:
        print(type(error).__name__+':'+str(error),file=sys.stderr)
        raise SystemExit(1)
