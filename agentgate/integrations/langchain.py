from __future__ import annotations
import asyncio
import functools
import logging
from typing import Any, Callable, TypeVar, Union, overload

from agentgate.client import GatewayClient
from agentgate.models import ToolCall

logger = logging.getLogger(__name__)

# Type variables
F = TypeVar("F", bound=Callable[..., Any])
T = TypeVar("T")


class ToolException(Exception):
    """Exception raised when a tool is blocked by AgentGate."""

    pass


def _extract_from_langchain_context() -> tuple[str, dict[str, Any]]:
    """
    Extract agent_id and context from LangChain's run context.
    Returns (agent_id, context_dict).
    """
    try:
        # Try to get from LangChain's langchain_context (if available in newer versions)
        # This is a best-effort approach; different LangChain versions may vary
        from langchain.callbacks.manager import get_callback_manager

        # Attempt to extract from the callback manager
        # In practice, this is often set via run_id or callback metadata
        return "unknown", {}
    except (ImportError, AttributeError):
        return "unknown", {}


def guarded_tool(
    gateway: GatewayClient | None = None,
    *,
    agent_id: str | None = None,
    context: dict[str, Any] | None = None,
) -> Callable[[F], F]:
    """
    Decorator to protect a LangChain tool with AgentGate.
    Works with both sync and async functions.

    Usage:
        gate = GatewayClient.from_env()

        @gate.guarded_tool(agent_id="my-agent")
        def my_tool(user_id: str, amount: float) -> dict:
            ...

        @gate.guarded_tool(agent_id="my-agent", context={"role": "admin"})
        async def async_tool(data: str) -> str:
            ...

    Raises ToolException if blocked (LangChain catches this gracefully).
    """

    def decorator(fn: F) -> F:
        _gate = gateway or GatewayClient.from_env()
        _agent_id = agent_id or "unknown"
        _context = context or {}

        is_async = asyncio.iscoroutinefunction(fn)

        if is_async:

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                # Extract any additional context from kwargs
                extracted_agent_id = kwargs.pop("__agent_id__", _agent_id)
                extracted_context = kwargs.pop("__context__", _context)

                tool_call = ToolCall(
                    tool_name=fn.__name__,
                    args=kwargs,
                    agent_id=extracted_agent_id,
                    context=extracted_context,
                )

                decision = await _gate.evaluate(tool_call)

                if not decision.is_allowed:
                    raise ToolException(
                        f"AgentGate blocked '{fn.__name__}': {decision.reason}"
                    )

                return await fn(*args, **kwargs)

            return async_wrapper  # type: ignore

        else:

            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                # Extract any additional context from kwargs
                extracted_agent_id = kwargs.pop("__agent_id__", _agent_id)
                extracted_context = kwargs.pop("__context__", _context)

                tool_call = ToolCall(
                    tool_name=fn.__name__,
                    args=kwargs,
                    agent_id=extracted_agent_id,
                    context=extracted_context,
                )

                # For sync functions, we need to run the async evaluation in a new event loop
                # (or use an existing one if available)
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    should_close = True
                else:
                    should_close = False

                try:
                    decision = loop.run_until_complete(_gate.evaluate(tool_call))
                finally:
                    if should_close:
                        loop.close()

                if not decision.is_allowed:
                    raise ToolException(
                        f"AgentGate blocked '{fn.__name__}': {decision.reason}"
                    )

                return fn(*args, **kwargs)

            return sync_wrapper  # type: ignore

    return decorator


def guarded_tool_from_langchain(
    gateway: GatewayClient | None = None,
) -> Callable[[F], F]:
    """
    Alternative decorator that attempts to extract agent_id and context
    from LangChain's callback/context system.

    Less reliable than guarded_tool with explicit parameters,
    but may work with some LangChain integrations.
    """

    def decorator(fn: F) -> F:
        _gate = gateway or GatewayClient.from_env()
        is_async = asyncio.iscoroutinefunction(fn)

        if is_async:

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                agent_id, context = _extract_from_langchain_context()

                tool_call = ToolCall(
                    tool_name=fn.__name__,
                    args=kwargs,
                    agent_id=agent_id,
                    context=context,
                )

                decision = await _gate.evaluate(tool_call)

                if not decision.is_allowed:
                    raise ToolException(
                        f"AgentGate blocked '{fn.__name__}': {decision.reason}"
                    )

                return await fn(*args, **kwargs)

            return async_wrapper  # type: ignore

        else:

            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                agent_id, context = _extract_from_langchain_context()

                tool_call = ToolCall(
                    tool_name=fn.__name__,
                    args=kwargs,
                    agent_id=agent_id,
                    context=context,
                )

                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    should_close = True
                else:
                    should_close = False

                try:
                    decision = loop.run_until_complete(_gate.evaluate(tool_call))
                finally:
                    if should_close:
                        loop.close()

                if not decision.is_allowed:
                    raise ToolException(
                        f"AgentGate blocked '{fn.__name__}': {decision.reason}"
                    )

                return fn(*args, **kwargs)

            return sync_wrapper  # type: ignore

    return decorator
