"""Thin U03 composition over a Python U01 connection, without a new runtime."""
from __future__ import annotations
from copy import deepcopy
from threading import Lock
from iwxa_capability_sdk import ModuleAdapterPort, assess
from u01_bridge import observe_u01_capabilities


class U03HostBinding:
    """Host retains authorization, connection and binding lifecycle authority."""

    def __init__(self, descriptor, host, connection, get_bindings, attach, detach):
        if not callable(get_bindings) or not callable(getattr(connection, 'capabilities', None)):
            raise TypeError('U03_HOST_BINDING_PORT_REQUIRED')
        self._adapter = ModuleAdapterPort(descriptor, attach, detach)
        self._host = deepcopy(host)
        self._connection = connection
        self._get_bindings = get_bindings
        self._attach_lock = Lock()  # Per-binding nonblocking guard; no registry.

    def describe(self):
        return self._adapter.describe()

    def _observations(self):
        return observe_u01_capabilities(self._connection, self._get_bindings())

    def assess(self):
        return assess(self.describe(), deepcopy(self._host), self._observations())

    def attach(self):
        if not self._attach_lock.acquire(blocking=False):
            return {'attached': False,
                    'assessment': {'compatible': False,
                                   'reasons': ['U03_HOST_ATTACH_IN_PROGRESS']},
                    'hostReceipt': None}
        try:
            before = self._observations()
            result = self._adapter.attach(deepcopy(self._host), before)
            if not result['attached']:
                return result
            receipt = result.get('hostReceipt')
            ref = receipt.get('bindingRef') if isinstance(receipt, dict) else None
            if not isinstance(ref, str) or not ref.strip():
                return {'attached': False,
                        'assessment': {'compatible': False,
                                       'reasons': ['U03_HOST_BINDING_REF_MISSING']},
                        'hostReceipt': receipt,
                        'rollback': 'UNCONFIRMED', 'hostState': 'UNKNOWN'}
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
            rollback = 'UNCONFIRMED'
            try:
                confirmation = self._adapter.detach(ref)
                if isinstance(confirmation, dict) and confirmation.get('detached') is True:
                    rollback = 'DETACHED'
            except Exception:
                pass  # Host owns recovery. Never assert detach without confirmation.
            return {'attached': False, 'assessment': assessment,
                    'hostReceipt': receipt, 'rollback': rollback,
                    'hostState': 'DETACHED' if rollback == 'DETACHED' else 'UNKNOWN'}
        finally:
            self._attach_lock.release()

    def detach(self, binding):
        return self._adapter.detach(binding)
