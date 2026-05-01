"""
FinMate tool definitions + execution dispatcher.

Tool schemas are passed to Claude. `execute_tool` is what runs *after*
AgentGate clears a call.
"""
from __future__ import annotations

import json
from examples.finmate.mock_db import FinMateDB

db = FinMateDB()

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_expense",
            "description": (
                "Look up a specific expense by ID. Returns expense details, "
                "amount, status, and employee information."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "expense_id": {
                        "type": "string",
                        "description": "Expense ID e.g. EXP-001",
                    }
                },
                "required": ["expense_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_pending_expenses",
            "description": (
                "Get all pending expenses awaiting approval. Returns list of "
                "expenses that need review."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "approve_expense",
            "description": (
                "Approve a pending expense for payment. This actually updates "
                "the database and authorizes the expenditure. You MUST first "
                "look up the expense to obtain its amount, then pass that "
                "amount in this call so policy controls can be applied."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "expense_id": {"type": "string", "description": "Expense ID to approve"},
                    "amount": {
                        "type": "number",
                        "description": "Expense amount in dollars (look up via get_expense first)",
                    },
                    "approved_by": {"type": "string", "description": "Email of approver"},
                },
                "required": ["expense_id", "amount", "approved_by"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reject_expense",
            "description": "Reject a pending expense with a reason.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expense_id": {"type": "string", "description": "Expense ID to reject"},
                    "reason": {"type": "string", "description": "Reason for rejection"},
                },
                "required": ["expense_id", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_invoice",
            "description": (
                "Look up a vendor invoice by ID. Returns invoice details, "
                "vendor, amount, due date, and current status."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "invoice_id": {"type": "string", "description": "Invoice ID e.g. INV-2024-001"},
                },
                "required": ["invoice_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "process_invoice",
            "description": (
                "Approve a vendor invoice for payment processing. This authorizes "
                "the vendor payment and updates accounts payable. You MUST first "
                "look up the invoice to obtain its amount, then pass that amount "
                "in this call so policy controls can be applied."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "invoice_id": {"type": "string", "description": "Invoice ID to process"},
                    "amount": {
                        "type": "number",
                        "description": "Invoice amount in dollars (look up via get_invoice first)",
                    },
                    "approved_by": {"type": "string", "description": "Email of approver"},
                },
                "required": ["invoice_id", "amount", "approved_by"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_budget",
            "description": (
                "Check budget allocation and spending for a team and quarter. "
                "Returns allocated, spent, remaining, and utilization percentage."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "team": {
                        "type": "string",
                        "description": "Team name: engineering, marketing, operations",
                    },
                    "quarter": {
                        "type": "string",
                        "description": "Quarter e.g. Q1-2026, Q2-2026",
                    },
                },
                "required": ["team", "quarter"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_account_balance",
            "description": "Get account balance and credit information for an employee.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "User ID e.g. emp_001"},
                },
                "required": ["user_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "export_financials",
            "description": (
                "Export all financial records including expenses, invoices, and "
                "budget data to a file. Use only when explicitly requested for "
                "compliance or audit purposes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "format": {
                        "type": "string",
                        "enum": ["csv", "json", "xlsx"],
                        "description": "Export format",
                    }
                },
                "required": ["format"],
            },
        },
    },
]


def execute_tool(name: str, args: dict) -> str:
    """Run a tool against the mock DB. Returns a JSON string."""
    handlers = {
        "get_expense": lambda a: db.get_expense(a["expense_id"]) or {"error": "Expense not found"},
        "get_pending_expenses": lambda a: db.get_pending_expenses(),
        "approve_expense": lambda a: db.approve_expense(
            a["expense_id"], a.get("approved_by", "finmate@acme.com")
        ),
        "reject_expense": lambda a: db.reject_expense(
            a["expense_id"], a.get("reason", "Does not meet policy")
        ),
        "get_invoice": lambda a: db.get_invoice(a["invoice_id"]) or {"error": "Invoice not found"},
        "process_invoice": lambda a: db.process_invoice(
            a["invoice_id"], a.get("approved_by", "finmate@acme.com")
        ),
        "get_budget": lambda a: db.get_budget(
            a["team"], a.get("quarter", "Q1-2026")
        ) or {"error": "Budget not found"},
        "get_account_balance": lambda a: db.get_account_balance(a["user_id"]) or {"error": "Account not found"},
        "export_financials": lambda a: db.export_financials(a.get("format", "csv")),
    }
    handler = handlers.get(name)
    if not handler:
        return json.dumps({"error": f"Unknown tool: {name}"})
    return json.dumps(handler(args), indent=2, default=str)
