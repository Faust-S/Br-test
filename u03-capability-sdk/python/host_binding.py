"""Thin U03 composition over an existing Python U01 connection; no runtime."""
from __future__ import annotations
from copy import deepcopy
from iwxa_capability_sdk import ModuleAdapterPort, assess
from u01_bridge import observe_u01_capabilities


class U03HostBinding:
    """Host supplies the connection, execution receipts and lifecycle hooks."""

    def __init__(self, descriptor, host, connection, get_bindings, attach, detach):
        if not callable(get_bindings) or not callable(getattr(connection, 'capabilities', None)):
            raise TypeError('U03_HOST_BINDING_PORT_REQUIRED')
        self._adapter = ModuleAdapterPort(descriptor, attach, detach)
        self._host = deepcopy(host)
        self._connection = connection
        self._get_bindings = get_bindings

    def describe(self):
        return self._adapter.describe()

    def _observations(self):
        return observe_u01_capabilities(self._connection, self._get_bindings())

    def assess(self):
        return assess(self.describe(), deepcopy(self._host), self._observations())

    def attach(self):
        before = self._observations()
        result = self._adapter.attach(deepcopy(self._host), before)
        if not result['attached']:
            return result
        try:
            after = self._observations()
            assessment = assess(self.describe(), deepcopy(self._host), after)
        except Exception:
            after = None
            assessment = {'compatible': False, 'reasons': ['U03_HOST_OBSERVATION_LOST']}
        if assessment['compatible'] and before == after:
            return result
        if assessment['compatible']:
            assessment = {'compatible': False, 'reasons': ['U03_HOST_OBSERVATION_CHANGED']}
        receipt = result.get('hostReceipt')
        binding_ref = receipt.get('bindingRef') if isinstance(receipt, dict) else None
        rollback = 'UNCONFIRMED'
        if isinstance(binding_ref, str) and binding_ref:
            try:
                confirmation = self._adapter.detach(binding_ref)
                if isinstance(confirmation, dict) and confirmation.get('detached') is True:
                    rollback = 'DETACHED'
            except Exception:
                pass  # Host owns recovery; report uncertainty explicitly.
        return {'attached': False, 'assessment': assessment,
                'hostReceipt': receipt, 'rollback': rollback,
                'hostState': 'DETACHED' if rollback == 'DETACHED' else 'UNKNOWN'}

    def detach(self, binding):
        return self._adapter.detach(binding)
