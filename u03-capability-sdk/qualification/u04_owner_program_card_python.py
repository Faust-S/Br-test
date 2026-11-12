"""Independent Python U03 Programs Center view of REAL pinned U04 owner responses.

Reads bounded JSON vectors from JS test stdin. No executable/window/inference
launch, no second observations and no standalone execution authority.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'python'))
from program_card import project_program_card


def main():
    input_data=json.load(sys.stdin)
    if not isinstance(input_data,list) or not 1 <= len(input_data) <= 32:
        raise ValueError('U03_U04_VECTORS_INVALID')
    outputs=[]
    for entry in input_data:
        if not isinstance(entry,dict) or not isinstance(entry.get('label'),str) or \
                not isinstance(entry.get('request'),dict):
            raise ValueError('U03_U04_VECTOR_INVALID')
        view=project_program_card(**entry['request'])
        if len(view['capabilities'])!=3:
            raise AssertionError('U03_U04_CAPABILITY_CARD_INCOMPLETE')
        if 'invoke' in view or 'grants' in view:
            raise AssertionError('U03_U04_OWNER_AUTHORITY_LEAK')
        outputs.append({'label':entry['label'],'projection':view})
    return {'status':'PASS','results':outputs}


if __name__=='__main__':
    try:
        print(json.dumps(main(),sort_keys=True,ensure_ascii=False))
    except Exception as error:
        print(type(error).__name__+':'+str(error),file=sys.stderr)
        raise SystemExit(1)
