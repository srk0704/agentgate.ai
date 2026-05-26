from __future__ import annotations
import asyncio
import logging
from typing import Any, Sequence, Union

from agentgate.client import GatewayClient
from agentgate.models import ToolCall
from agentgate.integrations.langchain import (
    _run_async,
    ToolException,
)

logger = logging.getLogger(__name__)


class GuardedToolNode:
    """
    A LangGraph ToolNode wrapper that evaluates
    every tool call through AgentGate before
    execution.

    Replaces LangGraph's ToolNode in your graph.
    Every tool call is evaluated — blocked if
    unsafe, escalated if risky, allowed if clean.
    When something goes wrong, agent_guidance is
    injected back into the graph state.

    Usage:
        from langgraph.prebuilt import ToolNode
        from agentgate.integrations.langgraph
            import GuardedToolNode

        gate = GatewayClient.from_env()

        # Replace ToolNode with GuardedToolNode
        # Before:
        #   tool_node = ToolNode(tools)
        # After:
        tool_node = GuardedToolNode(
            tools,
            gateway=gate,
            agent_id="my-agent",
        )

        # Use in graph exactly as before
        graph.add_node("tools", tool_node)
        graph.add_edge("tools", "agent")
    """

    def __init__(
        self,
        tools: Sequence[Any],
        gateway: GatewayClient | None = None,
        agent_id: str = "langgraph-agent",
        original_task: str | None = None,
    ) -> None:
        from langgraph.prebuilt import ToolNode
        self._inner = ToolNode(tools)
        self._gate = gateway or GatewayClient.from_env()
        self._agent_id = agent_id
        self._original_task = original_task
        self._tools_by_name: dict[str, Any] = {
            getattr(t, "name", getattr(t, "__name__", str(t))): t
            for t in tools
        }

    async def __call__(
        self, state: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Intercept tool calls from graph state,
        evaluate each through AgentGate, then
        execute allowed ones via the inner
        ToolNode.
        """
        messages = state.get("messages", [])

        # Find the last AI message with tool calls
        last_ai = None
        for msg in reversed(messages):
            role = getattr(msg, "type", None)
            if role == "ai" and getattr(
                msg, "tool_calls", None
            ):
                last_ai = msg
                break

        if not last_ai:
            # No tool calls — pass through
            return await self._inner(state)

        tool_calls = last_ai.tool_calls
        blocked_calls = []
        guidance_messages = []

        for tc in tool_calls:
            tool_name = tc.get("name", "unknown")
            tool_args = tc.get("args", {})
            tool_id = tc.get("id", "")

            ag_call = ToolCall(
                tool_name=tool_name,
                args=tool_args,
                agent_id=self._agent_id,
                original_task=(
                    self._original_task or ""
                ),
            )

            decision = await self._gate.evaluate(
                ag_call
            )

            if decision.agent_guidance:
                logger.warning(
                    "AgentGate guidance: %s",
                    decision.agent_guidance,
                )

            if not decision.is_allowed:
                blocked_calls.append({
                    "id": tool_id,
                    "name": tool_name,
                    "reason": decision.reason,
                    "guidance": (
                        decision.agent_guidance
                    ),
                })

        if blocked_calls:
            # Build tool result messages for
            # blocked calls so graph can continue
            from langchain_core.messages import (
                ToolMessage,
                SystemMessage,
            )

            tool_messages = []
            for bc in blocked_calls:
                tool_messages.append(
                    ToolMessage(
                        content=(
                            f"[AgentGate] Blocked: "
                            f"{bc['reason']}"
                        ),
                        tool_call_id=bc["id"],
                    )
                )
                if bc["guidance"]:
                    guidance_messages.append(
                        SystemMessage(
                            content=bc["guidance"]
                        )
                    )

            # If ALL calls blocked — return
            # guidance without executing anything
            if len(blocked_calls) == len(
                tool_calls
            ):
                return {
                    "messages": (
                        tool_messages
                        + guidance_messages
                    )
                }

            # Some blocked, some allowed —
            # remove blocked from state and
            # execute allowed ones
            allowed_ids = {
                tc["id"]
                for tc in tool_calls
                if not any(
                    bc["id"] == tc["id"]
                    for bc in blocked_calls
                )
            }
            # Filter tool_calls on last_ai msg
            # to only allowed ones
            import copy
            filtered_state = copy.deepcopy(state)
            filtered_msgs = filtered_state[
                "messages"
            ]
            for msg in reversed(filtered_msgs):
                if msg is last_ai or (
                    hasattr(msg, "tool_calls")
                    and msg.tool_calls
                ):
                    msg.tool_calls = [
                        tc for tc in msg.tool_calls
                        if tc["id"] in allowed_ids
                    ]
                    break

            result = await self._inner(
                filtered_state
            )
            result_messages = result.get(
                "messages", []
            )
            return {
                "messages": (
                    tool_messages
                    + result_messages
                    + guidance_messages
                )
            }

        # All calls allowed — execute normally
        return await self._inner(state)

    def __call___sync(
        self, state: dict[str, Any]
    ) -> dict[str, Any]:
        """Sync wrapper for non-async graphs."""
        return _run_async(self.__call__(state))


def create_guarded_tool_node(
    tools: Sequence[Any],
    gateway: GatewayClient | None = None,
    agent_id: str = "langgraph-agent",
    original_task: str | None = None,
) -> GuardedToolNode:
    """
    Factory function to create a GuardedToolNode.

    Usage:
        tool_node = create_guarded_tool_node(
            tools=[my_tool, another_tool],
            gateway=GatewayClient.from_env(),
            agent_id="finance-agent",
            original_task="Process Q1 invoices",
        )
        graph.add_node("tools", tool_node)
    """
    return GuardedToolNode(
        tools=tools,
        gateway=gateway,
        agent_id=agent_id,
        original_task=original_task,
    )
