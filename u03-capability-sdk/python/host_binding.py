"""Thin U03 composition over an existing Python U01 connection; no runtime."""
from __future__ import annotations
from copy import deepcopy
from iwxa_capability_sdk import ModuleAdapterPort, assess
from u01_bridge import observe_u01_capabilities


class U03HostBinding:
    """Host supplies actual connection, execution receipts, attach and detach."""

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
        return self._adapter.attach(deepcopy(self._host), self._observations())

    def detach(self, binding):
        return self._adapter.detach(binding)
