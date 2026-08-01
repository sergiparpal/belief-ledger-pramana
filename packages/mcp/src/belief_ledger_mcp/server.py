"""Official Python MCP SDK binding for the safe application surface."""

from __future__ import annotations

import json
from base64 import b64encode
from typing import Any

from belief_ledger_core import EpisodeContext

from .proxy import BeliefLedgerMcp, proxy_tool_name


def create_server(application: BeliefLedgerMcp) -> Any:
    """Create an SDK server. Importing the package itself does not start I/O."""

    from mcp.server import MCPServer

    server = MCPServer("Belief Ledger")

    @server.resource("belief-ledger://capabilities")
    async def capabilities() -> str:
        return json.dumps(application.read_resource("belief-ledger://capabilities"), sort_keys=True)

    @server.resource("belief-ledger://policies")
    async def policies() -> str:
        return json.dumps(application.read_resource("belief-ledger://policies"), sort_keys=True)

    @server.resource("belief-ledger://episodes/{episode_id}/beliefs")
    async def beliefs(episode_id: str) -> str:
        return json.dumps(
            application.read_resource(f"belief-ledger://episodes/{episode_id}/beliefs"),
            sort_keys=True,
        )

    @server.resource("belief-ledger://episodes/{episode_id}/conflicts")
    async def conflicts(episode_id: str) -> str:
        return json.dumps(
            application.read_resource(f"belief-ledger://episodes/{episode_id}/conflicts"),
            sort_keys=True,
        )

    @server.resource("belief-ledger://episodes/{episode_id}/decisions/{decision_id}")
    async def decision(episode_id: str, decision_id: str) -> str:
        return json.dumps(
            application.read_resource(
                f"belief-ledger://episodes/{episode_id}/decisions/{decision_id}"
            ),
            sort_keys=True,
        )

    @server.resource("belief-ledger://episodes/{episode_id}/audit")
    async def audit(episode_id: str) -> str:
        return json.dumps(
            application.read_resource(f"belief-ledger://episodes/{episode_id}/audit"),
            sort_keys=True,
        )

    @server.tool(name="belief_ledger_query")
    async def query(episode_id: str, text: str, limit: int = 20) -> str:
        return json.dumps(application.query(episode_id, text, limit=limit), sort_keys=True)

    @server.tool(name="belief_ledger_explain_decision")
    async def explain(episode_id: str, decision_id: str) -> str:
        return json.dumps(application.explain(episode_id, decision_id), sort_keys=True)

    @server.tool(name="belief_ledger_record_inference")
    async def record_inference(episode_id: str, content: str, premise_ids: list[str]) -> str:
        return json.dumps(
            application.record_inference(episode_id, content, premise_ids=tuple(premise_ids)),
            sort_keys=True,
        )

    @server.tool(name="belief_ledger_verify_chain")
    async def verify_chain() -> str:
        from belief_ledger_core.events import to_primitive

        return json.dumps(to_primitive(application.ledger.verify_chain()), sort_keys=True)

    def register_wrapped(descriptor: Any) -> None:
        exposed_name = proxy_tool_name(descriptor.namespace, descriptor.name)

        @server.tool(name=exposed_name, description=descriptor.description)
        async def wrapped(
            episode_id: str,
            arguments: dict[str, Any],
            session_id: str = "mcp",
            turn_id: str = "mcp-turn",
        ) -> str:
            context = EpisodeContext.normalize(
                session_id=session_id,
                turn_id=turn_id,
                task_id="mcp-proxy",
                platform="mcp",
            )
            result = application.invoke(
                episode_id,
                context,
                descriptor.name,
                arguments,
                namespace=descriptor.namespace,
            )
            return json.dumps(
                {
                    "schema_version": 1,
                    "forwarded": result.forwarded,
                    "reason_code": result.reason_code,
                    "content_base64": b64encode(result.content).decode("ascii"),
                },
                sort_keys=True,
            )

    for descriptor in application.wrapped_descriptors():
        register_wrapped(descriptor)

    return server
