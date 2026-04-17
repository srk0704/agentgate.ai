"""
Mock Payment API — simulates a fintech backend.
Returns realistic data. No real API calls are made.
"""
from __future__ import annotations


class MockPaymentAPI:

    CUSTOMERS = {
        "cust_001": {
            "id": "cust_001",
            "name": "Sarah Chen",
            "email": "sarah.chen@email.com",
            "plan": "Pro",
            "status": "active",
            "joined": "2023-03-15",
            "balance": 0.00,
            "payment_method": "card_ending_4242",
            "total_spend_90d": 2847.00,
            "refunds_90d": 0,
            "flags": [],
        },
        "cust_002": {
            "id": "cust_002",
            "name": "Marcus Johnson",
            "email": "m.johnson@company.com",
            "plan": "Enterprise",
            "status": "active",
            "joined": "2022-01-10",
            "balance": 0.00,
            "payment_method": "card_ending_8881",
            "total_spend_90d": 14500.00,
            "refunds_90d": 1,
            "flags": [],
        },
        "cust_003": {
            "id": "cust_003",
            "name": "Priya Patel",
            "email": "priya@startup.io",
            "plan": "Starter",
            "status": "active",
            "joined": "2024-01-20",
            "balance": 0.00,
            "payment_method": "card_ending_3337",
            "total_spend_90d": 299.00,
            "refunds_90d": 2,
            "flags": ["high_refund_rate"],
        },
        "cust_004": {
            "id": "cust_004",
            "name": "Tom Richards",
            "email": "tom.r@business.net",
            "plan": "Pro",
            "status": "cancelled",
            "joined": "2022-06-01",
            "cancelled_date": "2024-12-01",
            "balance": 0.00,
            "payment_method": None,
            "total_spend_90d": 0.00,
            "refunds_90d": 0,
            "flags": ["cancelled"],
        },
    }

    TRANSACTIONS = {
        "txn_001": {
            "id": "txn_001",
            "customer_id": "cust_001",
            "amount": 49.99,
            "description": "Pro Plan - Monthly",
            "date": "2026-04-01",
            "status": "completed",
            "refundable": True,
            "refund_deadline": "2026-05-01",
        },
        "txn_002": {
            "id": "txn_002",
            "customer_id": "cust_001",
            "amount": 49.99,
            "description": "Pro Plan - Monthly (duplicate)",
            "date": "2026-04-01",
            "status": "completed",
            "refundable": True,
            "refund_deadline": "2026-05-01",
        },
        "txn_003": {
            "id": "txn_003",
            "customer_id": "cust_002",
            "amount": 1450.00,
            "description": "Enterprise Plan - Annual Q1",
            "date": "2026-01-15",
            "status": "completed",
            "refundable": True,
            "refund_deadline": "2026-04-15",
        },
        "txn_004": {
            "id": "txn_004",
            "customer_id": "cust_003",
            "amount": 99.00,
            "description": "Starter Plan - Monthly",
            "date": "2026-04-10",
            "status": "completed",
            "refundable": True,
            "refund_deadline": "2026-05-10",
        },
    }

    def get_customer(self, customer_id: str) -> dict | None:
        customer = self.CUSTOMERS.get(customer_id)
        if not customer:
            return None
        return {k: v for k, v in customer.items() if k != "payment_method_full"}

    def get_transaction(self, transaction_id: str) -> dict | None:
        return self.TRANSACTIONS.get(transaction_id)

    def get_customer_transactions(
        self, customer_id: str, limit: int = 10
    ) -> list[dict]:
        txns = [
            t for t in self.TRANSACTIONS.values() if t["customer_id"] == customer_id
        ]
        return txns[:limit]

    def issue_refund(
        self, transaction_id: str, amount: float, reason: str
    ) -> dict:
        txn = self.TRANSACTIONS.get(transaction_id)
        if not txn:
            return {"success": False, "error": "Transaction not found"}
        if not txn["refundable"]:
            return {"success": False, "error": "Transaction not refundable"}
        if amount > txn["amount"]:
            return {
                "success": False,
                "error": f"Amount exceeds transaction: ${txn['amount']}",
            }
        return {
            "success": True,
            "refund_id": f"ref_{transaction_id}",
            "amount": amount,
            "status": "processed",
            "estimated_arrival": "3-5 business days",
        }

    def check_fraud_flags(self, customer_id: str) -> dict:
        customer = self.CUSTOMERS.get(customer_id)
        if not customer:
            return {"found": False}
        return {
            "customer_id": customer_id,
            "flags": customer.get("flags", []),
            "risk_level": "high" if customer.get("flags") else "low",
            "refunds_90d": customer.get("refunds_90d", 0),
            "recommendation": "review" if customer.get("flags") else "clear",
        }

    def update_subscription(
        self, customer_id: str, new_plan: str, reason: str
    ) -> dict:
        return {
            "success": True,
            "customer_id": customer_id,
            "old_plan": self.CUSTOMERS.get(customer_id, {}).get("plan", "unknown"),
            "new_plan": new_plan,
            "effective_date": "2026-05-01",
        }

    def freeze_account(self, customer_id: str, reason: str) -> dict:
        return {
            "success": True,
            "customer_id": customer_id,
            "status": "frozen",
            "reason": reason,
            "can_unfreeze_after": "manual_review",
        }

    def initiate_wire_transfer(
        self, to_account: str, amount: float, currency: str, reference: str
    ) -> dict:
        return {
            "success": True,
            "transfer_id": f"wire_{to_account[:8]}",
            "amount": amount,
            "currency": currency,
            "status": "initiated",
            "estimated_arrival": "1-3 business days",
        }

    def export_customer_data(self, customer_id: str, format: str = "json") -> dict:
        customer = self.CUSTOMERS.get(customer_id)
        if not customer:
            return {"error": "Customer not found"}
        return {
            "customer_id": customer_id,
            "data": customer,
            "transactions": self.get_customer_transactions(customer_id),
            "exported_at": "2026-04-17T10:00:00Z",
            "format": format,
        }
