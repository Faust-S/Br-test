"""Opt-in bridge to the existing Agent ToolProviderPort; no private code copied.

The normal Agent registry/ToolRuntime still owns descriptor selection, approval,
review, idempotency, Task and Result. This bridge does not authenticate users
or authorize an action merely because a matching request reaches it.
"""
from __future__ import annotations
import re
from typing import Any

from .controller import ObscuraControllerError
from .interaction import ObscuraInteractiveController, ObscuraInteractionError

_SHA = re.compile(r"[0-9a-f]{64}\Z")
_READ = frozenset({"web.fetch", "web.inspect", "web.follow_link"})
_ACTION = "web.click_control"


class ObscuraAgentToolPort:
    contract_id = "PA.PORT.TOOL_PROVIDER"
    contract_version = "1.0.0"

    def __init__(self, controller, *, provider_id: str,
                 binding_digest: str, descriptor_digest: str | dict[str, str],
                 delegate: Any = None):
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise ValueError("provider identity required")
        if not isinstance(binding_digest, str) or _SHA.fullmatch(binding_digest) is None:
            raise ValueError("authoritative binding digest required")
        if isinstance(descriptor_digest, str):
            descriptors = {"web.fetch": descriptor_digest}
        elif isinstance(descriptor_digest, dict):
            descriptors = dict(descriptor_digest)
        else:
            raise ValueError("tool descriptor digest required")
        if not descriptors or not set(descriptors) <= (_READ | {_ACTION}):
            raise ValueError("unsupported tool descriptors")
        if any(not isinstance(v, str) or _SHA.fullmatch(v) is None for v in descriptors.values()):
            raise ValueError("invalid tool descriptor digest")
        if not hasattr(controller, "observe"):
            raise ValueError("Obscura worker controller required")
        self.descriptor_digests = descriptors
        self.controller = controller
        self.provider_id = provider_id
        self.binding_digest = binding_digest
        self.delegate = delegate

    def invoke(self, request):
        from providers.tool_provider_port import ToolProviderPortError, ToolProviderResult

        request.validate()
        if request.logical_provider_id != self.provider_id:
            if self.delegate is None:
                raise ToolProviderPortError("PORT_UNAVAILABLE", "unbound_obscura_provider")
            return self.delegate.invoke(request)
        ref = request.tool_descriptor_ref
        operation = request.operation
        if (ref.get("provider_slot") != "web_acquisition"
                or ref.get("provider_binding_digest") != self.binding_digest
                or ref.get("descriptor_digest") != self.descriptor_digests.get(operation)
                or ref.get("tool_id") != operation
                or operation not in self.descriptor_digests
                or request.target is not None):
            raise ToolProviderPortError("PORT_PROTOCOL_ERROR", "obscura_binding_denied")
        args = request.arguments
        if not isinstance(args.get("url"), str):
            raise ToolProviderPortError("PORT_PROTOCOL_ERROR", "obscura_url_required")
        is_action = operation == _ACTION
        if is_action:
            if (not request.idempotency_key_or_none or
                    not request.tool_plan_digest_or_none or
                    set(args) != {"url", "selector", "confirmation_selector", "expected_text"}):
                raise ToolProviderPortError("PORT_PROTOCOL_ERROR", "obscura_reviewed_action_required")
        elif request.idempotency_key_or_none is not None or request.tool_plan_digest_or_none is not None:
            raise ToolProviderPortError("PORT_PROTOCOL_ERROR", "obscura_read_binding_denied")
        if operation == "web.fetch" and set(args) != {"url"}:
            raise ToolProviderPortError("PORT_PROTOCOL_ERROR", "obscura_fetch_arguments_denied")
        if operation == "web.inspect" and set(args) != {"url", "selector"}:
            raise ToolProviderPortError("PORT_PROTOCOL_ERROR", "obscura_inspect_arguments_denied")
        if operation == "web.follow_link" and set(args) != {"url", "selector"}:
            raise ToolProviderPortError("PORT_PROTOCOL_ERROR", "obscura_follow_arguments_denied")
        if operation != "web.fetch" and not isinstance(self.controller, ObscuraInteractiveController):
            raise ToolProviderPortError("PORT_UNAVAILABLE", "obscura_interactive_worker_not_qualified")
        try:
            if operation == "web.fetch":
                observed = self.controller.observe(args["url"])
                data = observed.as_data()
            elif operation == "web.inspect":
                data = self.controller.inspect(args["url"], selector=args["selector"])
            elif operation == "web.follow_link":
                data = self.controller.follow_link(args["url"], selector=args["selector"])
            else:
                data = self.controller.click_control(
                    args["url"], selector=args["selector"],
                    confirmation_selector=args["confirmation_selector"],
                    expected_text=args["expected_text"],
                )
        except ObscuraInteractionError as error:
            raise ToolProviderPortError(
                "PORT_UNAVAILABLE", error.code,
                external_effect_state=error.external_effect_state,
            ) from None
        except ObscuraControllerError as error:
            raise ToolProviderPortError("PORT_UNAVAILABLE", error.code) from None
        # A DOM click does not attest a committed server-side change. The
        # downstream Agent must not convert UNKNOWN into ACKNOWLEDGED.
        effect = "UNKNOWN" if is_action else "NONE"
        return ToolProviderResult.from_adapter_result({
            "status": 200,  # provider transport result, NOT HTTP status
            "external_effect_state": effect,
            "request_id": request.request_id,
            "data": data,
        }, request=request)
