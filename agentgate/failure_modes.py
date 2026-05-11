"""
AgentGate — Failure Modes Catalog
=================================
Single source of truth for the 47 agent failure modes AgentGate tracks.
No database calls in this module. Wiring + helpers only.
"""
from __future__ import annotations

# ================================================
# SECTION 1: STATIC METADATA
# ================================================

CATEGORIES = [
    {
        "id": "agent_wrong_action",
        "label": "The agent did the wrong thing",
        "description": "Agent misunderstood or mishandled the task",
    },
    {
        "id": "agent_stuck",
        "label": "The agent got stuck",
        "description": "Agent failed to make progress and kept trying anyway",
    },
    {
        "id": "bad_information",
        "label": "The agent acted on bad information",
        "description": "Agent made decisions based on wrong or manipulated data",
    },
    {
        "id": "unacceptable_consequences",
        "label": "The action had unacceptable consequences",
        "description": "Agent's action was too risky, irreversible, or policy-violating",
    },
    {
        "id": "sensitive_data",
        "label": "The agent exposed sensitive data",
        "description": "Agent leaked or over-shared information it should not have",
    },
    {
        "id": "abnormal_session",
        "label": "The session behaved abnormally",
        "description": "Something was wrong with the overall pattern of agent behavior",
    },
    {
        "id": "security_adjacent",
        "label": "Security-adjacent failures",
        "description": "Adversarial inputs that redirect or manipulate the agent",
    },
    {
        "id": "output_quality",
        "label": "Output quality failures",
        "description": "Agent's text response was wrong, harmful, or inappropriate",
    },
    {
        "id": "memory_failures",
        "label": "Memory layer failures",
        "description": "Agent's long-term memory was corrupted, poisoned, or unreliable",
    },
]

FAILURE_MODES = [
    # agent_wrong_action
    {"id": "goal_drift", "name": "Goal drift", "category": "agent_wrong_action",
     "plain_english": "Agent started one task and ended up doing something else",
     "detection_layer": "Pre-execution", "status": "built", "tier": "free"},
    {"id": "excessive_agency", "name": "Excessive agency", "category": "agent_wrong_action",
     "plain_english": "Agent took a much bigger action than the situation called for",
     "detection_layer": "Pre-execution", "status": "built", "tier": "free"},
    {"id": "sycophantic_override", "name": "Sycophantic override", "category": "agent_wrong_action",
     "plain_english": "Agent was pressured into doing something it already refused",
     "detection_layer": "Session pattern", "status": "coming_soon", "tier": "pro"},
    {"id": "hallucinated_action", "name": "Hallucinated action", "category": "agent_wrong_action",
     "plain_english": "Agent called a tool with invented arguments that made no sense",
     "detection_layer": "Pre-execution", "status": "coming_soon", "tier": "pro"},
    {"id": "role_confusion", "name": "Role confusion", "category": "agent_wrong_action",
     "plain_english": "Agent forgot what it is supposed to do and started acting differently",
     "detection_layer": "Output scan", "status": "roadmap", "tier": "enterprise"},
    {"id": "scope_creep", "name": "Scope creep", "category": "agent_wrong_action",
     "plain_english": "Agent gradually expanded what it considers its job over time",
     "detection_layer": "Session pattern", "status": "roadmap", "tier": "enterprise"},

    # agent_stuck
    {"id": "retry_storm", "name": "Retry storm", "category": "agent_stuck",
     "plain_english": "Agent kept calling a failing tool instead of stopping",
     "detection_layer": "Pre-execution", "status": "built", "tier": "free"},
    {"id": "sequence_loop", "name": "Sequence loop", "category": "agent_stuck",
     "plain_english": "Agent kept repeating the same sequence of steps",
     "detection_layer": "Pre-execution", "status": "built", "tier": "free"},
    {"id": "cascading_failure", "name": "Cascading failure", "category": "agent_stuck",
     "plain_english": "Agent completed step 1 irreversibly then failed on step 2",
     "detection_layer": "Post-execution", "status": "coming_soon", "tier": "pro"},
    {"id": "deadlock", "name": "Deadlock", "category": "agent_stuck",
     "plain_english": "Agent waited for something that was never going to happen",
     "detection_layer": "Session pattern", "status": "roadmap", "tier": "enterprise"},
    {"id": "infinite_planning", "name": "Infinite planning", "category": "agent_stuck",
     "plain_english": "Agent kept replanning instead of executing",
     "detection_layer": "Session pattern", "status": "roadmap", "tier": "enterprise"},

    # bad_information
    {"id": "stale_data", "name": "Stale data", "category": "bad_information",
     "plain_english": "Agent made a decision based on outdated information",
     "detection_layer": "Pre-execution", "status": "coming_soon", "tier": "pro"},
    {"id": "tool_result_corruption", "name": "Tool result corruption", "category": "bad_information",
     "plain_english": "Agent read a tool response that contained wrong or manipulated data",
     "detection_layer": "Post-execution", "status": "coming_soon", "tier": "pro"},
    {"id": "memory_drift", "name": "Memory drift", "category": "bad_information",
     "plain_english": "Agent's long-term memory diverged from reality",
     "detection_layer": "Memory layer", "status": "roadmap", "tier": "enterprise"},
    {"id": "hallucinated_facts", "name": "Hallucinated facts", "category": "bad_information",
     "plain_english": "Agent answered confidently with information it invented",
     "detection_layer": "Output scan", "status": "roadmap", "tier": "enterprise"},
    {"id": "context_loss", "name": "Context loss", "category": "bad_information",
     "plain_english": "Agent forgot important context from earlier in the conversation",
     "detection_layer": "Session pattern", "status": "roadmap", "tier": "enterprise"},

    # unacceptable_consequences
    {"id": "high_blast_radius", "name": "High blast radius", "category": "unacceptable_consequences",
     "plain_english": "Action would have caused outsized financial or irreversible damage",
     "detection_layer": "Pre-execution", "status": "built", "tier": "free"},
    {"id": "policy_violation", "name": "Policy violation", "category": "unacceptable_consequences",
     "plain_english": "Action violated an explicit business rule",
     "detection_layer": "Pre-execution", "status": "built", "tier": "free"},
    {"id": "irreversible_action", "name": "Irreversible action", "category": "unacceptable_consequences",
     "plain_english": "Agent took an action that cannot be undone without escalation",
     "detection_layer": "Pre-execution", "status": "coming_soon", "tier": "pro"},
    {"id": "cascading_downstream", "name": "Cascading downstream", "category": "unacceptable_consequences",
     "plain_english": "Action triggered unintended consequences in connected systems",
     "detection_layer": "Post-execution", "status": "roadmap", "tier": "enterprise"},
    {"id": "race_condition", "name": "Race condition", "category": "unacceptable_consequences",
     "plain_english": "Two agent sessions modified the same resource simultaneously",
     "detection_layer": "Session pattern", "status": "roadmap", "tier": "enterprise"},

    # sensitive_data
    {"id": "pii_in_output", "name": "PII in output", "category": "sensitive_data",
     "plain_english": "Agent included personal data in its response",
     "detection_layer": "Output scan", "status": "built", "tier": "free"},
    {"id": "data_exfiltration", "name": "Data exfiltration", "category": "sensitive_data",
     "plain_english": "Agent sent data to somewhere it should not have",
     "detection_layer": "Pre-execution", "status": "built", "tier": "free"},
    {"id": "over_sharing", "name": "Over-sharing", "category": "sensitive_data",
     "plain_english": "Agent revealed more information than the task required",
     "detection_layer": "Output scan", "status": "coming_soon", "tier": "pro"},
    {"id": "cross_tenant_leakage", "name": "Cross-tenant leakage", "category": "sensitive_data",
     "plain_english": "Agent accessed data belonging to a different customer",
     "detection_layer": "Pre-execution", "status": "roadmap", "tier": "enterprise"},
    {"id": "inference_attack", "name": "Inference attack", "category": "sensitive_data",
     "plain_english": "Agent response revealed private data through indirect clues",
     "detection_layer": "Output scan", "status": "roadmap", "tier": "enterprise"},

    # abnormal_session
    {"id": "session_anomaly", "name": "Session anomaly", "category": "abnormal_session",
     "plain_english": "Unusual velocity or pattern of calls within a session",
     "detection_layer": "Session pattern", "status": "built", "tier": "free"},
    {"id": "multi_agent_trust", "name": "Multi-agent trust violation", "category": "abnormal_session",
     "plain_english": "Sub-agent blindly followed instructions from another agent",
     "detection_layer": "Pre-execution", "status": "coming_soon", "tier": "pro"},
    {"id": "model_behavior_drift", "name": "Model behavior drift", "category": "abnormal_session",
     "plain_english": "Underlying LLM changed behavior without any code change",
     "detection_layer": "Session pattern", "status": "roadmap", "tier": "enterprise"},
    {"id": "compounding_errors", "name": "Compounding errors", "category": "abnormal_session",
     "plain_english": "Small errors in sequence produced a large wrong outcome",
     "detection_layer": "Session pattern", "status": "roadmap", "tier": "enterprise"},

    # security_adjacent
    {"id": "prompt_injection", "name": "Prompt injection", "category": "security_adjacent",
     "plain_english": "Hidden instruction in user input redirected the agent",
     "detection_layer": "Pre-execution", "status": "built", "tier": "free"},
    {"id": "goal_hijacking", "name": "Goal hijacking", "category": "security_adjacent",
     "plain_english": "Attacker redirected agent to a completely different task",
     "detection_layer": "Pre-execution", "status": "built", "tier": "free"},
    {"id": "tool_result_poisoning", "name": "Tool result poisoning", "category": "security_adjacent",
     "plain_english": "Instructions hidden inside tool responses the agent reads back",
     "detection_layer": "Post-execution", "status": "coming_soon", "tier": "pro"},
    {"id": "memory_poisoning", "name": "Memory poisoning", "category": "security_adjacent",
     "plain_english": "False memories injected into agent's long-term memory store",
     "detection_layer": "Memory layer", "status": "coming_soon", "tier": "pro"},
    {"id": "indirect_injection", "name": "Indirect injection", "category": "security_adjacent",
     "plain_english": "Instructions hidden in web pages, files, or emails agent reads",
     "detection_layer": "Pre-execution", "status": "roadmap", "tier": "enterprise"},
    {"id": "context_window_flooding", "name": "Context window flooding", "category": "security_adjacent",
     "plain_english": "Attacker fills context to push safety instructions out of window",
     "detection_layer": "Pre-execution", "status": "roadmap", "tier": "enterprise"},

    # output_quality
    {"id": "response_hallucination", "name": "Response hallucination", "category": "output_quality",
     "plain_english": "Agent's text response contradicted what its tools actually returned",
     "detection_layer": "Output scan", "status": "coming_soon", "tier": "pro"},
    {"id": "sycophantic_reversal", "name": "Sycophantic reversal", "category": "output_quality",
     "plain_english": "Agent changed a correct refusal to approval under user pressure",
     "detection_layer": "Output scan", "status": "coming_soon", "tier": "pro"},
    {"id": "policy_violation_in_text", "name": "Policy violation in text", "category": "output_quality",
     "plain_english": "Agent described how to do something it refused to execute",
     "detection_layer": "Output scan", "status": "coming_soon", "tier": "pro"},
    {"id": "confidence_mismatch", "name": "Confidence mismatch", "category": "output_quality",
     "plain_english": "Agent expressed certainty about facts it cannot verify",
     "detection_layer": "Output scan", "status": "roadmap", "tier": "enterprise"},
    {"id": "harmful_content", "name": "Harmful content", "category": "output_quality",
     "plain_english": "Agent generated output that violates acceptable use policy",
     "detection_layer": "Output scan", "status": "roadmap", "tier": "enterprise"},
    {"id": "instruction_leakage", "name": "Instruction leakage", "category": "output_quality",
     "plain_english": "Agent revealed its system prompt or internal instructions",
     "detection_layer": "Output scan", "status": "roadmap", "tier": "enterprise"},

    # memory_failures
    {"id": "memory_write_poisoning", "name": "Memory write poisoning", "category": "memory_failures",
     "plain_english": "Agent stored false information that will affect future sessions",
     "detection_layer": "Memory layer", "status": "coming_soon", "tier": "pro"},
    {"id": "memory_read_anomaly", "name": "Memory read anomaly", "category": "memory_failures",
     "plain_english": "Agent retrieved memory that contradicts the current session",
     "detection_layer": "Memory layer", "status": "coming_soon", "tier": "pro"},
    {"id": "memory_drift_layer", "name": "Memory drift", "category": "memory_failures",
     "plain_english": "What the agent believes diverged from what actually happened",
     "detection_layer": "Memory layer", "status": "roadmap", "tier": "enterprise"},
    {"id": "cross_session_contamination", "name": "Cross-session contamination",
     "category": "memory_failures",
     "plain_english": "Memory from one user's session affected another user's session",
     "detection_layer": "Memory layer", "status": "roadmap", "tier": "enterprise"},
    {"id": "stale_memory_retrieval", "name": "Stale memory retrieval", "category": "memory_failures",
     "plain_english": "Agent acted on memory that is no longer accurate or relevant",
     "detection_layer": "Memory layer", "status": "roadmap", "tier": "enterprise"},
]

# ================================================
# SECTION 2: DETECTOR WIRING
# Verified against actual schema May 2026.
# ================================================

DETECTOR_WIRING = {
    "goal_drift": {
        "table": "audit_log",
        # Match the gateway's drift_escalate threshold so mild structural
        # mismatches (score 30 from uncategorized tools etc.) don't inflate
        # the "goal_drift detected" count.
        "where": "drift_score >= 60 AND drift_score IS NOT NULL",
        "time_field": "decided_at",
    },
    "excessive_agency": {
        "table": "audit_log",
        "where": (
            "attack_type = 'excessive_agency' "
            "AND injection_score >= 70 "
            "AND outcome = 'blocked'"
        ),
        "time_field": "decided_at",
    },
    "retry_storm": {
        "table": "audit_log",
        "where": "loop_score >= 70 AND loop_score IS NOT NULL",
        "time_field": "decided_at",
    },
    "sequence_loop": {
        "table": "audit_log",
        "where": "loop_score >= 85 AND loop_score IS NOT NULL",
        "time_field": "decided_at",
    },
    "high_blast_radius": {
        "table": "audit_log",
        "where": (
            "json_extract(blast_radius,'$.severity') IN ('high','critical') "
            "AND outcome != 'allowed'"
        ),
        "time_field": "decided_at",
    },
    "policy_violation": {
        "table": "audit_log",
        "where": "policy_matched IS NOT NULL AND outcome = 'blocked'",
        "time_field": "decided_at",
    },
    "pii_in_output": {
        "table": "pii_scan_log",
        "where": "safe = 0 AND recommendation IN ('redact','block')",
        "time_field": "scanned_at",
    },
    "data_exfiltration": {
        "table": "audit_log",
        "where": (
            "outcome = 'blocked' AND ("
            "tool_name LIKE '%export%' "
            "OR tool_name LIKE '%send%' "
            "OR tool_name LIKE '%webhook%')"
        ),
        "time_field": "decided_at",
    },
    "session_anomaly": {
        "table": "audit_log",
        "where": "anomaly_score >= 50 AND anomaly_score IS NOT NULL",
        "time_field": "decided_at",
    },
    "prompt_injection": {
        "table": "audit_log",
        "where": "injection_score >= 70 AND injection_score IS NOT NULL",
        "time_field": "decided_at",
    },
    "goal_hijacking": {
        "table": "audit_log",
        "where": "attack_type = 'goal_hijacking'",
        "time_field": "decided_at",
    },
}

# ================================================
# SECTION 3: HELPERS
# ================================================

def get_all_modes() -> list[dict]:
    return FAILURE_MODES


def get_built_modes() -> list[dict]:
    return [m for m in FAILURE_MODES if m["status"] == "built"]


def get_all_categories() -> list[dict]:
    return CATEGORIES


def get_modes_by_category(cat_id: str) -> list[dict]:
    return [m for m in FAILURE_MODES if m["category"] == cat_id]


def get_summary() -> dict:
    built = sum(1 for m in FAILURE_MODES if m["status"] == "built")
    coming = sum(1 for m in FAILURE_MODES if m["status"] == "coming_soon")
    roadmap = sum(1 for m in FAILURE_MODES if m["status"] == "roadmap")
    return {
        "built": built,
        "coming_soon": coming,
        "roadmap": roadmap,
        "total": len(FAILURE_MODES),
    }
