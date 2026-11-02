"""Opt-in bridge to the existing Agent ToolProviderPort. No private code copied.

The module can be imported standalone; Agent types are imported on invoke so
public controller development does not depend on a private repository.
"""
from __future__ import annotations
from typing import Any
from .controller import ObscuraController, ObscuraControllerError


class ObscuraAgentToolPort:
    contract_id = "PA.PORT.TOOL_PROVIDER"
    contract_version = "1.0.0"

    def __init__(self, controller: ObscuraController, *, provider_id: str,
                 binding_digest: str, descriptor_digest: str, delegate: Any = None):
        if not provider_id or len(binding_digest) != 64 or any(c not in "0123456789abcdef" for c in binding_digest):
            raise ValueError("provider identity and authoritative binding required")
        if len(descriptor_digest) != 64 or any(c not in "0123456789abcdef" for c in descriptor_digest):
            raise ValueError("tool descriptor digest required")
        self.descriptor_digest = descriptor_digest
        self.controller = controller
        self.provider_id = provider_id
        self.binding_digest = binding_digest
        self.delegate = delegate

    def invoke(self, request):
        # Only the Agent's authoritative port owns the validated result type.
        # This bridge never reimplements ToolPolicy, ToolRuntime or receipts.
        from providers.tool_provider_port import (
            ToolProviderPortError, ToolProviderResult,
        )
        request.validate()
        if request.logical_provider_id != self.provider_id:
            if self.delegate is None:
                raise ToolProviderPortError("PORT_UNAVAILABLE", "unbound_obscura_provider")
            return self.delegate.invoke(request)
        ref = request.tool_descriptor_ref
        if (ref.get("provider_slot") != "web_acquisition" or
            ref.get("provider_binding_digest") != self.binding_digest or
            ref.get("descriptor_digest") != self.descriptor_digest or
            ref.get("tool_id") != "web.fetch" or request.operation != "web.fetch" or
            request.idempotency_key_or_none is not None or
            request.tool_plan_digest_or_none is not None or
            request.target is not None):
            raise ToolProviderPortError("PORT_PROTOCOL_ERROR", "obscura_read_binding_denied")
        if set(request.arguments) != {"url"} or not isinstance(request.arguments.get("url"), str):
            raise ToolProviderPortError("PORT_PROTOCOL_ERROR", "obscura_arguments_denied")
        try:
            observed = self.controller.observe(request.arguments["url"])
        except ObscuraControllerError as error:
            # Never claim HTTP failure/success for a rejected or incomplete capture.
            raise ToolProviderPortError("PORT_UNAVAILABLE", error.code) from None
        return ToolProviderResult.from_adapter_result({
            "status": 200,  # Tool observation succeeded; NOT an HTTP response code.
            "external_effect_state": "NONE",
            "request_id": request.request_id,
            "data": observed.as_data(),
        }, request=request)
