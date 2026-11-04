"""Independent Python readback of frozen public T2 module contract declarations."""
from pathlib import Path
import json
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'python'))
from t2_manifest import inspect_t2_contract, is_expected_t2_schema


def qualify(source_root):
    base=Path(source_root).resolve()/'t2/current/final-successor/canonical-source/src'
    def read(path):
        return (base/path).read_text(encoding='utf-8')
    def load(path):
        return json.loads(read(path))
    schema=load('config/schemas/keys-2.0/workspace-module-manifest.schema.json')
    assert is_expected_t2_schema(schema),'U03_T2_SCHEMA_DRIFT'
    match=re.search(r'WORKSPACE_CONTRACT_VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"',
                    read('workspace-contract/version.ts'))
    assert match,'U03_T2_WORKSPACE_VERSION_UNAVAILABLE'
    workspace=match.group(1)
    runtime=load('kernel-client/contracts/runtime-1.3.0/contract-manifest.json')['contract_version']
    assert schema['properties']['compatibility']['properties']['runtime_contract']['const']==runtime
    target={'workspaceContract':workspace,'runtimeContract':runtime}
    modules=[]
    for name in ('base','exchange','view-state','lifecycle'):
        manifest=load(f'modules/{name}/manifest.json')
        result=inspect_t2_contract(manifest,target)
        assert result['declaredCompatible'] and not result['reasons'],f'U03_T2_CONTRACT_FAILURE:{name}'
        assert result['admission']=='NOT_ASSESSED'
        assert result['module']['id']==manifest['module_id']
        assert result['module']['version']==manifest['module_version']
        assert 'provides' not in result['module'] and 'grants' not in result
        assert not inspect_t2_contract(manifest,{**target,'runtimeContract':'0.0.0'})['declaredCompatible']
        modules.append({'id':result['module']['id'],'version':result['module']['version'],
                        'declaredContributions':sum(result['module']['declaredContributionCounts'].values())})
    print(json.dumps({'schema':'IWXA_U03_T2_READBACK/1.0.0','status':'PASS','consumer':'python',
        'owner':'T2','source':'public-pinned-checkout','workspaceContract':workspace,
        'runtimeContract':runtime,'modules':modules,'admission':'NOT_ASSESSED',
        'hostGranted':False,'hostModified':False},sort_keys=True))


if __name__=='__main__':
    if len(sys.argv)!=2:raise SystemExit('U03_T2_PUBLIC_SOURCE_REQUIRED')
    qualify(sys.argv[1])
