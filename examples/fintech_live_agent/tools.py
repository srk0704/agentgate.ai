"""
OpenAI-compatible tool definitions for the payment support agent.
"""

PAYMENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_customer_info",
            "description": "Look up customer account information by customer ID",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "string",
                        "description": "Customer ID (e.g. cust_001)",
                    }
                },
                "required": ["customer_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_transaction",
            "description": "Get details of a specific transaction",
            "parameters": {
                "type": "object",
                "properties": {
                    "transaction_id": {
                        "type": "string",
                        "description": "Transaction ID (e.g. txn_001)",
                    }
                },
                "required": ["transaction_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_customer_transactions",
            "description": "Get recent transactions for a customer",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {"type": "string"},
                    "limit": {
                        "type": "integer",
                        "description": "Max transactions to return",
                        "default": 5,
                    },
                },
                "required": ["customer_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "issue_refund",
            "description": "Issue a refund for a transaction",
            "parameters": {
                "type": "object",
                "properties": {
                    "transaction_id": {"type": "string"},
                    "amount": {
                        "type": "number",
                        "description": "Amount to refund in USD",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Reason for refund",
                    },
                },
                "required": ["transaction_id", "amount", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_fraud_flags",
            "description": "Check if a customer has fraud or risk flags",
            "parameters": {
                "type": "object",
                "properties": {"customer_id": {"type": "string"}},
                "required": ["customer_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_subscription",
            "description": "Change a customer subscription plan",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {"type": "string"},
                    "new_plan": {
                        "type": "string",
                        "enum": ["Starter", "Pro", "Enterprise"],
                    },
                    "reason": {"type": "string"},
                },
                "required": ["customer_id", "new_plan", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "freeze_account",
            "description": "Freeze a customer account due to suspicious activity",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["customer_id", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "initiate_wire_transfer",
            "description": "Initiate an international wire transfer",
            "parameters": {
                "type": "object",
                "properties": {
                    "to_account": {"type": "string"},
                    "amount": {"type": "number"},
                    "currency": {"type": "string", "default": "USD"},
                    "reference": {"type": "string"},
                },
                "required": ["to_account", "amount", "reference"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "export_customer_data",
            "description": "Export all customer data for compliance or portability",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {"type": "string"},
                    "format": {
                        "type": "string",
                        "enum": ["json", "csv"],
                        "default": "json",
                    },
                },
                "required": ["customer_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_account_status",
            "description": "Get real-time live account status from external monitoring service",
            "parameters": {
                "type": "object",
                "properties": {
                    "account_id": {
                        "type": "string",
                        "description": "Account ID",
                    }
                },
                "required": ["account_id"],
            },
        },
    },
]
