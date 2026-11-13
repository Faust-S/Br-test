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

    def _snapshot(self):
        # Preserve the exact read used by the U01 bridge, not a second probe.
        # Stable receipt references can be reused across different programs,
        # operation mappings or connection epochs: all three are owner identity.
        selected = self._get_bindings()
        # The owning program/operation/receipt selection can mutate inside
        # capabilities() (e.g. a user switches projects). Never attach against
        # the new values under an observation started for the old selection.
        selected_at_start = deepcopy(selected)
        actual = [None]
        connection = self._connection

        class OneRead:
            def capabilities(self):
                actual[0] = connection.capabilities()
                return actual[0]

        observations = observe_u01_capabilities(OneRead(), selected)
        mapping = tuple(sorted((row['operation'], row['id'], row['version'])
                               for row in selected['bindings']))
        source = actual[0]
        if isinstance(source, dict):
            operations = source.get('operations')
            operations = tuple(sorted(
                ((row.get('name'), row.get('declared'), row.get('verified'))
                 for row in operations if isinstance(row, dict)),
                key=lambda item: str(item[0])
            )) if isinstance(operations, list) else None
            owner_identity = (source.get('schema'), source.get('program_id'),
                              source.get('transport'), source.get('status'),
                              source.get('connected'), source.get('connection_epoch'),
                              operations)
        else:
            owner_identity = None
        identity = (selected['programId'], mapping, owner_identity)
        return observations, identity, selected != selected_at_start

    def _observations(self):
        return self._snapshot()[0]

    def assess(self):
        observations, _, changed = self._snapshot()
        if changed:
            return {'compatible': False,
                    'reasons': ['U03_HOST_OBSERVATION_CHANGED']}
        return assess(self.describe(), deepcopy(self._host), observations)

    def attach(self):
        if not self._attach_lock.acquire(blocking=False):
            return {'attached': False,
                    'assessment': {'compatible': False,
                                   'reasons': ['U03_HOST_ATTACH_IN_PROGRESS']},
                    'hostReceipt': None}
        try:
            before, owner_before, before_changed = self._snapshot()
            if before_changed:
                return {'attached': False,
                        'assessment': {'compatible': False,
                                       'reasons': ['U03_HOST_OBSERVATION_CHANGED']},
                        'hostReceipt': None}
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
                after, owner_after, after_changed = self._snapshot()
                assessment = assess(self.describe(), deepcopy(self._host), after)
            except Exception:
                after = None
                owner_after = None
                after_changed = True
                assessment = {'compatible': False, 'reasons': ['U03_HOST_OBSERVATION_LOST']}
            if assessment['compatible'] and not after_changed and before == after and owner_before == owner_after:
                return result
            if assessment['compatible']:
                assessment = {'compatible': False, 'reasons': ['U03_HOST_OBSERVATION_CHANGED']}
            rollback = 'UNCONFIRMED'
            try:
                confirmation = self._adapter.detach(ref)
                if isinstance(confirmation, dict) and confirmation.get('detached') is True and (
                        'bindingRef' not in confirmation or confirmation['bindingRef'] == ref):
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
