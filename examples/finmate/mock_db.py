"""
FinMate — mock financial database.

SQLite-backed, auto-creates and seeds on first instantiation.
Realistic enterprise data for the demo: expenses awaiting approval,
vendor invoices, quarterly budgets, employee accounts, audit trail.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "finmate.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS expenses (
    id          TEXT PRIMARY KEY,
    employee    TEXT NOT NULL,
    amount      REAL NOT NULL,
    category    TEXT NOT NULL,
    description TEXT,
    status      TEXT DEFAULT 'pending',
    submitted   TEXT,
    approved_by TEXT,
    receipt_url TEXT
);

CREATE TABLE IF NOT EXISTS invoices (
    id          TEXT PRIMARY KEY,
    vendor      TEXT NOT NULL,
    amount      REAL NOT NULL,
    due_date    TEXT,
    status      TEXT DEFAULT 'pending',
    description TEXT,
    po_number   TEXT
);

CREATE TABLE IF NOT EXISTS budgets (
    team        TEXT NOT NULL,
    quarter     TEXT NOT NULL,
    allocated   REAL NOT NULL,
    spent       REAL NOT NULL,
    PRIMARY KEY (team, quarter)
);

CREATE TABLE IF NOT EXISTS accounts (
    user_id      TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    balance      REAL NOT NULL,
    credit_limit REAL NOT NULL,
    department   TEXT
);

CREATE TABLE IF NOT EXISTS audit_trail (
    id           TEXT PRIMARY KEY,
    action       TEXT NOT NULL,
    amount       REAL,
    performed_by TEXT,
    timestamp    TEXT,
    details      TEXT
);
"""

SEED = """
INSERT OR IGNORE INTO expenses VALUES
  ('EXP-001','sarah.chen@acme.com',49.99,'meals',
   'Team lunch at Chipotle','pending',
   '2026-04-14',NULL,'receipt_001.pdf'),
  ('EXP-002','marcus.j@acme.com',2499.00,'software',
   'Annual Figma license','pending',
   '2026-04-13',NULL,'receipt_002.pdf'),
  ('EXP-003','priya.p@acme.com',149.99,'travel',
   'Uber to client meeting','approved',
   '2026-04-12','manager@acme.com','receipt_003.pdf');

INSERT OR IGNORE INTO invoices VALUES
  ('INV-2024-001','Acme Cloud Services',1450.00,
   '2026-04-30','pending','Monthly cloud infrastructure',
   'PO-2024-087'),
  ('INV-2024-002','Design Studio LLC',25000.00,
   '2026-04-15','pending','Q1 brand refresh project',
   'PO-2024-088'),
  ('INV-2024-003','Office Supplies Co',234.50,
   '2026-04-20','paid','Office supplies Q1',
   'PO-2024-089');

INSERT OR IGNORE INTO budgets VALUES
  ('engineering','Q1-2026',1000000,247832),
  ('engineering','Q2-2026',1000000,89441),
  ('marketing','Q1-2026',500000,412000),
  ('marketing','Q2-2026',500000,156000),
  ('operations','Q1-2026',250000,198000),
  ('operations','Q2-2026',250000,67000);

INSERT OR IGNORE INTO accounts VALUES
  ('emp_001','Sarah Chen',12450.00,5000.00,'engineering'),
  ('emp_002','Marcus Johnson',8230.00,10000.00,'marketing'),
  ('emp_003','Priya Patel',3890.00,2500.00,'operations'),
  ('mgr_001','Alex Rivera',45000.00,50000.00,'finance');
"""


class FinMateDB:
    def __init__(self) -> None:
        self.path = str(DB_PATH)
        self._init()

    def _init(self) -> None:
        conn = sqlite3.connect(self.path)
        conn.executescript(SCHEMA)
        conn.executescript(SEED)
        conn.commit()
        conn.close()

    # ── Expenses ──────────────────────────────────────────────────────────
    def get_expense(self, expense_id: str) -> dict | None:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM expenses WHERE id=?", (expense_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_pending_expenses(self) -> list[dict]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM expenses WHERE status='pending'"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def approve_expense(self, expense_id: str, approved_by: str) -> dict:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute(
            "UPDATE expenses SET status='approved', approved_by=? WHERE id=?",
            (approved_by, expense_id),
        )
        conn.commit()
        expense = conn.execute(
            "SELECT * FROM expenses WHERE id=?", (expense_id,)
        ).fetchone()
        conn.close()
        if not expense:
            return {"success": False, "error": "Expense not found"}
        return {
            "success": True,
            "expense_id": expense_id,
            "amount": expense["amount"],
            "status": "approved",
            "approved_by": approved_by,
        }

    def reject_expense(self, expense_id: str, reason: str) -> dict:
        conn = sqlite3.connect(self.path)
        conn.execute(
            "UPDATE expenses SET status='rejected' WHERE id=?", (expense_id,)
        )
        conn.commit()
        conn.close()
        return {
            "success": True,
            "expense_id": expense_id,
            "status": "rejected",
            "reason": reason,
        }

    # ── Invoices ──────────────────────────────────────────────────────────
    def get_invoice(self, invoice_id: str) -> dict | None:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM invoices WHERE id=?", (invoice_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def process_invoice(self, invoice_id: str, approved_by: str) -> dict:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        invoice = conn.execute(
            "SELECT * FROM invoices WHERE id=?", (invoice_id,)
        ).fetchone()
        if not invoice:
            conn.close()
            return {"success": False, "error": "Invoice not found"}
        conn.execute(
            "UPDATE invoices SET status='approved' WHERE id=?", (invoice_id,)
        )
        conn.commit()
        conn.close()
        return {
            "success": True,
            "invoice_id": invoice_id,
            "vendor": invoice["vendor"],
            "amount": invoice["amount"],
            "status": "approved_for_payment",
        }

    # ── Budgets / accounts ────────────────────────────────────────────────
    def get_budget(self, team: str, quarter: str) -> dict | None:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM budgets WHERE team=? AND quarter=?",
            (team.lower(), quarter.upper()),
        ).fetchone()
        conn.close()
        if not row:
            return None
        d = dict(row)
        d["remaining"] = d["allocated"] - d["spent"]
        d["utilization_pct"] = round(d["spent"] / d["allocated"] * 100, 1)
        return d

    def get_account_balance(self, user_id: str) -> dict | None:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM accounts WHERE user_id=?", (user_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    # ── Export (intentionally always succeeds; gate decides whether agent
    #    should be the one calling it) ─────────────────────────────────────
    def export_financials(self, format: str) -> dict:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        expenses = conn.execute("SELECT * FROM expenses").fetchall()
        invoices = conn.execute("SELECT * FROM invoices").fetchall()
        conn.close()
        return {
            "success": True,
            "format": format,
            "records": len(expenses) + len(invoices),
            "file": f"financials_export.{format}",
            "warning": "Contains sensitive financial data",
        }
