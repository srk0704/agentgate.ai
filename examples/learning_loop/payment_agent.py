"""
AgentGate Learning Loop — LangGraph Payment Agent
"""
from __future__ import annotations
import json
import logging
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Optional, TypedDict

# path setup so we can import from fintech_live_agent
_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "examples" / "fintech_live_agent"))

from dotenv import load_dotenv
load_dotenv(override=True)

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, StateGraph

from agentgate.client import GatewayClient
from agentgate.learning_engine import LearningEngine
from agentgate.models import ToolCall
from agentgate.output_logger import OutputLogger
from mock_payment_api import MockPaymentAPI
from tools import PAYMENT_TOOLS

logger = logging.getLogger(__name__)

AGENT_ID = "payment-support-langgraph"

SYSTEM_PROMPT = """You are a helpful payment support agent for a fintech company.
You have access to customer accounts, transaction history, and payment tools.

Guidelines:
- Always look up the customer first before taking action
- Verify transaction details before processing refunds
- Never process refunds for cancelled accounts
- Be helpful but cautious with financial actions
- If an action is blocked or needs approval, explain clearly to the user"""


@dataclass
class AgentResult:
    response: str
    decision: Optional[Any]
    tool_called: Optional[str]
    tool_result: Optional[dict]
    was_blocked: bool
    was_escalated: bool
    latency_ms: float


class AgentState(TypedDict):
    user_input: str
    messages: list
    pending_tool_name: Optional[str]
    pending_tool_args: Optional[dict]
    pending_tool_call_id: Optional[str]   # OpenAI tool_call id
    agentgate_call_id: Optional[str]      # AgentGate call_id for output logging
    decision_outcome: Optional[str]
    decision_reason: Optional[str]
    tool_result: Optional[dict]
    final_response: str
    session_id: str
    was_blocked: bool
    was_escalated: bool
    turn_count: int   # tracks multi-turn iterations


class PaymentSupportAgent:
    def __init__(
        self,
        gateway: GatewayClient,
        learning_engine: LearningEngine,
        output_logger: OutputLogger,
        agent_id: str = AGENT_ID,
        session_id: str | None = None,
    ):
        self.gateway = gateway
        self.learning_engine = learning_engine
        self.output_logger = output_logger
        self.agent_id = agent_id
        self.session_id = session_id or str(uuid.uuid4())[:8]
        self._api = MockPaymentAPI()
        self._llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0,
        ).bind_tools(PAYMENT_TOOLS)
        self._graph = self._build_graph()

    def _get_system_prompt(self) -> str:
        return self.learning_engine.get_enhanced_system_prompt(SYSTEM_PROMPT)

    def _build_graph(self):
        builder = StateGraph(AgentState)
        builder.add_node("plan_action", self._plan_action)
        builder.add_node("evaluate_action", self._evaluate_action)
        builder.add_node("execute_action", self._execute_action)
        builder.add_node("handle_block", self._handle_block)
        builder.add_node("handle_escalation", self._handle_escalation)
        builder.add_node("synthesize_response", self._synthesize_response)
        builder.add_node("log_outcome", self._log_outcome)

        builder.set_entry_point("plan_action")
        builder.add_conditional_edges(
            "plan_action",
            self._route_after_plan,
            {
                "has_tool": "evaluate_action",
                "no_tool": "synthesize_response",
            },
        )
        builder.add_conditional_edges(
            "evaluate_action",
            self._route_after_eval,
            {
                "execute": "execute_action",
                "blocked": "handle_block",
                "escalated": "handle_escalation",
            },
        )
        # After executing a tool, loop back to plan_action for the next step
        builder.add_conditional_edges(
            "execute_action",
            self._route_after_execute,
            {
                "continue": "plan_action",
                "done": "synthesize_response",
            },
        )
        builder.add_edge("handle_block", "log_outcome")
        builder.add_edge("handle_escalation", "log_outcome")
        builder.add_edge("synthesize_response", "log_outcome")
        builder.add_edge("log_outcome", END)
        return builder.compile()

    async def _plan_action(self, state: AgentState) -> dict:
        """LLM decides what tool to call (or responds directly)."""
        system_msg = SystemMessage(content=self._get_system_prompt())
        msgs = [system_msg] + state["messages"]
        response = await self._llm.ainvoke(msgs)
        new_messages = state["messages"] + [response]
        turn = state.get("turn_count", 0) + 1

        if response.tool_calls:
            tc = response.tool_calls[0]
            return {
                "messages": new_messages,
                "pending_tool_name": tc["name"],
                "pending_tool_args": tc["args"],
                "pending_tool_call_id": tc["id"],
                "turn_count": turn,
                # reset gate fields for this new tool call
                "agentgate_call_id": None,
                "decision_outcome": None,
                "decision_reason": None,
            }
        else:
            return {
                "messages": new_messages,
                "final_response": response.content,
                "turn_count": turn,
                "pending_tool_name": None,
            }

    def _route_after_execute(self, state: AgentState) -> Literal["continue", "done"]:
        """After a tool executes, loop back unless we've hit max turns."""
        if state.get("turn_count", 0) >= 4:
            return "done"
        return "continue"

    def _route_after_plan(self, state: AgentState) -> Literal["has_tool", "no_tool"]:
        return "has_tool" if state.get("pending_tool_name") else "no_tool"

    async def _evaluate_action(self, state: AgentState) -> dict:
        """Run AgentGate evaluation."""
        tool_call = ToolCall(
            tool_name=state["pending_tool_name"],
            args=state["pending_tool_args"],
            agent_id=self.agent_id,
            context={"role": "support", "team": "customer_success"},
            original_task=state["user_input"],
            session_id=state["session_id"],
        )
        decision = await self.gateway.evaluate(tool_call)
        return {
            "decision_outcome": decision.outcome.value,
            "decision_reason": decision.reason,
            "agentgate_call_id": tool_call.call_id,
        }

    def _route_after_eval(
        self, state: AgentState
    ) -> Literal["execute", "blocked", "escalated"]:
        outcome = state.get("decision_outcome", "")
        if outcome in ("blocked",):
            return "blocked"
        if outcome in ("escalated",):
            return "escalated"
        return "execute"

    async def _execute_action(self, state: AgentState) -> dict:
        """Execute the tool and log result."""
        name = state["pending_tool_name"]
        args = state["pending_tool_args"]
        try:
            result = await self._call_api(name, args)
            success = result.get("success", True) if isinstance(result, dict) else True
            await self.output_logger.log_tool_result(
                call_id=state["agentgate_call_id"],
                tool_name=name,
                tool_result=result,
                agent_id=self.agent_id,
                success=success,
                financial_impact=args.get("amount"),
            )
            tool_msg = ToolMessage(
                content=json.dumps(result, default=str),
                tool_call_id=state["pending_tool_call_id"],
            )
            return {
                "tool_result": result,
                "messages": state["messages"] + [tool_msg],
            }
        except Exception as e:
            await self.output_logger.log_tool_result(
                call_id=state["agentgate_call_id"],
                tool_name=name,
                tool_result={},
                agent_id=self.agent_id,
                success=False,
                error=str(e),
            )
            tool_msg = ToolMessage(
                content=json.dumps({"error": str(e)}),
                tool_call_id=state["pending_tool_call_id"],
            )
            return {
                "tool_result": {"error": str(e)},
                "messages": state["messages"] + [tool_msg],
            }

    async def _handle_block(self, state: AgentState) -> dict:
        reason = state.get("decision_reason", "Policy violation")
        resp = f"I'm unable to complete that action: {reason}"
        return {"final_response": resp, "was_blocked": True}

    async def _handle_escalation(self, state: AgentState) -> dict:
        reason = state.get("decision_reason", "Requires human approval")
        resp = (
            f"This action requires human approval and has been queued for review: "
            f"{reason}. A reviewer will process it shortly."
        )
        return {"final_response": resp, "was_escalated": True}

    async def _synthesize_response(self, state: AgentState) -> dict:
        """If final_response already set (no_tool path), use it. Otherwise ask LLM."""
        if state.get("final_response"):
            return {}
        system_msg = SystemMessage(content=self._get_system_prompt())
        msgs = [system_msg] + state["messages"]
        response = await self._llm.ainvoke(msgs)
        return {"final_response": response.content}

    async def _log_outcome(self, state: AgentState) -> dict:
        """Log agent response to output_logger."""
        if state.get("agentgate_call_id") and state.get("final_response"):
            try:
                await self.output_logger.log_agent_response(
                    call_id=state["agentgate_call_id"],
                    agent_response=state["final_response"],
                )
            except Exception as e:
                logger.debug("log_outcome error: %s", e)
        return {}

    async def _call_api(self, name: str, args: dict) -> dict:
        """Dispatch to MockPaymentAPI."""
        if name == "get_customer_info":
            return self._api.get_customer(args["customer_id"]) or {"error": "not found"}
        elif name == "get_transaction":
            return self._api.get_transaction(args["transaction_id"]) or {"error": "not found"}
        elif name == "get_customer_transactions":
            return {
                "transactions": self._api.get_customer_transactions(
                    args["customer_id"], args.get("limit", 5)
                )
            }
        elif name == "issue_refund":
            return self._api.issue_refund(
                args["transaction_id"], args["amount"], args["reason"]
            )
        elif name == "check_fraud_flags":
            return self._api.check_fraud_flags(args["customer_id"])
        elif name == "update_subscription":
            return self._api.update_subscription(
                args["customer_id"], args["new_plan"], args["reason"]
            )
        elif name == "freeze_account":
            return self._api.freeze_account(args["customer_id"], args["reason"])
        elif name == "initiate_wire_transfer":
            return self._api.initiate_wire_transfer(
                args["to_account"],
                args["amount"],
                args.get("currency", "USD"),
                args["reference"],
            )
        elif name == "export_customer_data":
            return self._api.export_customer_data(
                args["customer_id"], args.get("format", "json")
            )
        elif name == "get_account_status":
            try:
                return self._api.get_account_status(args["account_id"])
            except ConnectionError as e:
                # Surface as a tool failure — output_log captures success=False, which
                # feeds the LoopDetector retry-storm signal in the next call.
                return {"error": str(e), "success": False}
        return {"error": f"Unknown tool: {name}"}

    async def run(self, user_input: str) -> AgentResult:
        t0 = time.monotonic()
        initial_state: AgentState = {
            "user_input": user_input,
            "messages": [HumanMessage(content=user_input)],
            "pending_tool_name": None,
            "pending_tool_args": None,
            "pending_tool_call_id": None,
            "agentgate_call_id": None,
            "decision_outcome": None,
            "decision_reason": None,
            "tool_result": None,
            "final_response": "",
            "session_id": self.session_id,
            "was_blocked": False,
            "was_escalated": False,
            "turn_count": 0,
        }
        final = await self._graph.ainvoke(initial_state)
        latency = (time.monotonic() - t0) * 1000

        return AgentResult(
            response=final.get("final_response", "(no response)"),
            decision=None,
            tool_called=final.get("pending_tool_name"),
            tool_result=final.get("tool_result"),
            was_blocked=final.get("was_blocked", False),
            was_escalated=final.get("was_escalated", False),
            latency_ms=latency,
        )
