"""AgentGate CLI."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ENV_TEMPLATE = """\
# AgentGate environment variables
# Copy this to .env and fill in your values

ANTHROPIC_API_KEY=your-key-here
AGENTGATE_DB_PATH=./agentgate.db
AGENTGATE_ENV=development

# Mode: observe (learn) or enforce (protect)
# Start with observe — AgentGate will log
# everything without blocking anything.
# Run: agentgate generate-policy
# when ready to generate your policy.
AGENTGATE_MODE=observe

# Uncomment after running generate-policy:
# AGENTGATE_POLICY_PATH=./policy.yaml
"""


def cmd_check(args: argparse.Namespace) -> None:
    """Run the built-in quickcheck."""
    from agentgate import quickcheck
    quickcheck()


def cmd_version(args: argparse.Namespace) -> None:
    """Print installed version."""
    from agentgate import __version__
    print(f"agentgate {__version__}")


def cmd_init(args: argparse.Namespace) -> None:
    """Create .env.example in current directory."""
    env_path = Path(".env.example")
    if env_path.exists() and not args.force:
        print(".env.example already exists. "
              "Use --force to overwrite.")
        sys.exit(1)
    env_path.write_text(ENV_TEMPLATE)
    print("✓ Created .env.example")
    print()
    print("Next steps:")
    print()
    print("  1. cp .env.example .env")
    print("     Add your ANTHROPIC_API_KEY")
    print()
    print("  2. Set AGENTGATE_MODE=observe")
    print("     in .env — AgentGate will log")
    print("     everything without blocking.")
    print()
    print("  3. Add 3 lines to your agent:")
    print()
    print("     from agentgate.client import"
          " GatewayClient")
    print("     gate = GatewayClient.from_env()")
    print("     decision = await"
          " gate.evaluate(tool_call)")
    print("     if decision.is_allowed:")
    print("         result = await"
          " my_tool(**args)")
    print()
    print("  4. Run your agent normally.")
    print("     AgentGate observes and logs.")
    print()
    print("  5. Generate your policy:")
    print("     agentgate generate-policy")
    print()
    print("  6. Review policy.yaml then set:")
    print("     AGENTGATE_MODE=enforce")
    print("     AGENTGATE_POLICY_PATH="
          "./policy.yaml")
    print()
    print("  Full guide: ONBOARDING.md")
    print("  Book a call: calendly.com/"
          "sk4975-columbia/30min")


def cmd_generate_policy(
    args: argparse.Namespace,
) -> None:
    """Generate policy.yaml from observed
    tool calls using heuristic + AI enrichment."""
    import asyncio
    asyncio.run(_generate_policy_async(args))


async def _generate_policy_async(
    args: argparse.Namespace,
) -> None:
    import json
    from pathlib import Path
    from collections import defaultdict
    from datetime import datetime, timezone

    db_path = os.getenv(
        "AGENTGATE_DB_PATH", "./agentgate.db"
    )
    min_obs = int(
        os.getenv(
            "AGENTGATE_MIN_OBSERVATIONS", "500"
        )
    )
    output_path = Path(
        args.output if args.output else "policy.yaml"
    )

    try:
        import aiosqlite
    except ImportError:
        print("Error: aiosqlite not installed.")
        sys.exit(1)

    # ── Step 1: Count observations ──────────
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM audit_log "
            "WHERE observe_mode = 1"
        ) as cur:
            row = await cur.fetchone()
            total = row[0] if row else 0

    if total == 0:
        print(
            "No observation data found.\n"
            "Set AGENTGATE_MODE=observe in .env "
            "and run your agent first."
        )
        sys.exit(1)

    if total < min_obs:
        print(
            f"\n⚠ Low data warning: {total} "
            f"observations found.\n"
            f"  Recommended minimum: {min_obs}\n"
            f"  Generated policy may not reflect "
            f"full production behavior.\n"
        )
        answer = input(
            "Generate anyway? [y/N] "
        ).strip().lower()
        if answer != "y":
            print("Cancelled.")
            sys.exit(0)

    print(f"\nAnalysing {total} observations...")

    # ── Step 2: Collect per-tool stats ──────
    READONLY_PREFIXES = (
        "get_", "list_", "fetch_",
        "read_", "search_", "find_",
        "lookup_", "check_",
    )
    DESTRUCTIVE_PREFIXES = (
        "delete_", "remove_", "purge_",
        "destroy_", "drop_", "truncate_",
    )

    tool_stats: dict = defaultdict(lambda: {
        "count": 0,
        "numeric_args": defaultdict(list),
        "is_readonly": False,
        "is_destructive": False,
        "sample_args": [],
    })

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT tool_name, args "
            "FROM audit_log "
            "WHERE observe_mode = 1"
        ) as cur:
            async for row in cur:
                tool = row["tool_name"]
                stats = tool_stats[tool]
                stats["count"] += 1
                stats["is_readonly"] = any(
                    tool.startswith(p)
                    for p in READONLY_PREFIXES
                )
                stats["is_destructive"] = any(
                    tool.startswith(p)
                    for p in DESTRUCTIVE_PREFIXES
                )
                try:
                    arg_dict = json.loads(
                        row["args"]
                    )
                    for k, v in arg_dict.items():
                        if isinstance(
                            v, (int, float)
                        ):
                            stats[
                                "numeric_args"
                            ][k].append(float(v))
                    if len(
                        stats["sample_args"]
                    ) < 3:
                        stats[
                            "sample_args"
                        ].append(arg_dict)
                except Exception:
                    pass

    # ── Step 3: Heuristic rule generation ───
    print("Generating heuristic rules...")

    heuristic_rules: list[dict] = []

    for tool, stats in sorted(
        tool_stats.items(),
        key=lambda x: -x[1]["count"]
    ):
        count = stats["count"]
        freq_ratio = count / total

        if stats["is_readonly"]:
            heuristic_rules.append({
                "tool": tool,
                "effect": "allow",
                "heuristic_reason": (
                    f"Read-only tool — observed "
                    f"{count}x "
                    f"({freq_ratio:.0%} of calls)"
                ),
                "conditions": [],
            })

        elif stats["is_destructive"]:
            heuristic_rules.append({
                "tool": tool,
                "effect": "block",
                "heuristic_reason": (
                    f"Destructive tool — block "
                    f"by default. "
                    f"Observed {count}x."
                ),
                "conditions": [],
            })

        elif stats["numeric_args"]:
            conditions = []
            for arg, values in stats[
                "numeric_args"
            ].items():
                if not values:
                    continue
                max_val = max(values)
                sorted_vals = sorted(values)
                p90 = sorted_vals[
                    int(len(sorted_vals) * 0.9)
                ]
                escalate_at = round(p90 * 2, -2)
                block_at = round(max_val * 10, -2)
                conditions.append({
                    "arg": arg,
                    "escalate_at": escalate_at,
                    "block_at": block_at,
                    "p90": p90,
                    "max": max_val,
                    "count": len(values),
                })
            heuristic_rules.append({
                "tool": tool,
                "effect": "threshold",
                "heuristic_reason": (
                    f"Numeric args detected — "
                    f"thresholds from {count} "
                    f"observations"
                ),
                "conditions": conditions,
            })

        elif freq_ratio < 0.01:
            heuristic_rules.append({
                "tool": tool,
                "effect": "escalate",
                "heuristic_reason": (
                    f"Rare tool — only {count}x "
                    f"({freq_ratio:.1%} of calls)"
                ),
                "conditions": [],
            })

        else:
            heuristic_rules.append({
                "tool": tool,
                "effect": "escalate",
                "heuristic_reason": (
                    f"Unknown classification — "
                    f"escalate by default. "
                    f"Observed {count}x."
                ),
                "conditions": [],
            })

    # ── Step 4: AI enrichment ────────────────
    ai_enriched: dict[str, dict] = {}
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")

    if not anthropic_key:
        print(
            "⚠ ANTHROPIC_API_KEY not set — "
            "skipping AI enrichment.\n"
            "  Policy will use heuristic "
            "rules only."
        )
    else:
        print("Enriching with AI analysis...")
        try:
            import anthropic as anthropic_lib
            client = anthropic_lib.AsyncAnthropic(
                api_key=anthropic_key
            )

            obs_summary = []
            for rule in heuristic_rules:
                tool = rule["tool"]
                stats = tool_stats[tool]
                numeric = {}
                for k, v in stats[
                    "numeric_args"
                ].items():
                    if v:
                        sv = sorted(v)
                        numeric[k] = {
                            "min": min(v),
                            "max": max(v),
                            "p90": sv[
                                int(len(sv) * 0.9)
                            ],
                            "count": len(v),
                        }
                obs_summary.append({
                    "tool": tool,
                    "call_count": stats["count"],
                    "frequency_pct": round(
                        stats["count"]
                        / total * 100, 1
                    ),
                    "is_readonly": stats[
                        "is_readonly"
                    ],
                    "is_destructive": stats[
                        "is_destructive"
                    ],
                    "numeric_args": numeric,
                    "sample_args": stats[
                        "sample_args"
                    ],
                    "heuristic_classification":
                        rule["effect"],
                    "heuristic_reason": rule[
                        "heuristic_reason"
                    ],
                })

            prompt = (
                f"You are an AI safety expert "
                f"reviewing an auto-generated "
                f"policy for an AI agent. "
                f"The policy was generated by "
                f"analysing {total} real tool "
                f"call observations.\n\n"
                f"For each tool, review the "
                f"heuristic classification and:\n"
                f"1. Confirm or correct the "
                f"effect (allow/block/escalate/"
                f"threshold)\n"
                f"2. For numeric args, suggest "
                f"better thresholds based on "
                f"the data and industry "
                f"standards\n"
                f"3. Write a clear reason string "
                f"for the dashboard\n"
                f"4. Flag any concerns\n\n"
                f"Observations:\n"
                f"{json.dumps(obs_summary, indent=2)}"
                f"\n\nRespond with a JSON object "
                f"where each key is a tool name "
                f"and value has:\n"
                f'{{"effect": "allow|block|'
                f'escalate|threshold", '
                f'"confirmed": true|false, '
                f'"correction": "why changed or '
                f'null", '
                f'"reason": "human readable for '
                f'dashboard", '
                f'"confidence": "HIGH|MEDIUM|LOW",'
                f'"concern": "any concern or null",'
                f'"thresholds": {{'
                f'"arg_name": {{'
                f'"escalate_at": number, '
                f'"block_at": number'
                f'}}}}}}\n\n'
                f"Return only valid JSON. "
                f"No markdown fences. "
                f"No explanation outside JSON."
            )

            message = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=2000,
                timeout=30.0,
                messages=[{
                    "role": "user",
                    "content": prompt,
                }],
            )

            raw = message.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1]
                raw = raw.rsplit("```", 1)[0].strip()
            ai_enriched = json.loads(raw)
            print(
                f"✓ AI reviewed "
                f"{len(ai_enriched)} tools"
            )

        except Exception as e:
            print(
                f"⚠ AI enrichment failed: {e}\n"
                f"  Falling back to heuristic "
                f"rules only."
            )
            ai_enriched = {}

    # ── Step 5: Merge heuristic + AI ────────
    final_rules: list[dict] = []
    warnings: list[str] = []

    for rule in heuristic_rules:
        tool = rule["tool"]
        ai = ai_enriched.get(tool, {})

        heuristic_effect = rule["effect"]
        ai_effect = ai.get(
            "effect", heuristic_effect
        )

        if (
            ai
            and ai.get("confidence") == "HIGH"
            and ai_effect != heuristic_effect
        ):
            final_effect = ai_effect
            correction_note = (
                f"AI corrected from "
                f"{heuristic_effect}: "
                f"{ai.get('correction', '')}"
            )
        else:
            final_effect = heuristic_effect
            correction_note = None

        reason = (
            ai.get("reason")
            or rule["heuristic_reason"]
        )

        comment_lines = [
            f"# {tool}",
            f"# Observed: "
            f"{tool_stats[tool]['count']}x",
            f"# Heuristic: {heuristic_effect}",
        ]
        if ai:
            confirmed = ai.get("confirmed")
            conf = ai.get("confidence", "?")
            comment_lines.append(
                f"# AI ({conf}): "
                + (
                    "confirmed"
                    if confirmed
                    else f"corrected to "
                         f"{final_effect}"
                )
            )
            if correction_note:
                comment_lines.append(
                    f"# {correction_note}"
                )
            if ai.get("concern"):
                comment_lines.append(
                    f"# ⚠ {ai['concern']}"
                )
                warnings.append(
                    f"{tool}: {ai['concern']}"
                )

        comment = "\n".join(comment_lines)

        if (
            final_effect == "threshold"
            or rule["conditions"]
        ):
            first = True
            for cond in rule["conditions"]:
                arg = cond["arg"]
                ai_thresh = (
                    ai.get("thresholds", {})
                    .get(arg, {})
                )
                block_val = ai_thresh.get(
                    "block_at", cond["block_at"]
                )
                esc_val = ai_thresh.get(
                    "escalate_at",
                    cond["escalate_at"]
                )
                base = (
                    f"{tool}_{arg}"
                    .replace(".", "_")
                )
                final_rules.append({
                    "comment": (
                        comment if first else None
                    ),
                    "name": f"block_{base}_high",
                    "match": {"tool": tool},
                    "conditions": [{
                        "field": f"args.{arg}",
                        "op": "gte",
                        "value": block_val,
                    }],
                    "effect": "block",
                    "reason": (
                        f"{reason} "
                        f"({arg} >= "
                        f"{block_val:,.0f})"
                    ),
                })
                final_rules.append({
                    "comment": None,
                    "name": f"escalate_{base}",
                    "match": {"tool": tool},
                    "conditions": [{
                        "field": f"args.{arg}",
                        "op": "gte",
                        "value": esc_val,
                    }],
                    "effect": "escalate",
                    "reason": (
                        f"{reason} "
                        f"({arg} >= "
                        f"{esc_val:,.0f})"
                    ),
                })
                final_rules.append({
                    "comment": None,
                    "name": f"allow_{tool}",
                    "match": {"tool": tool},
                    "conditions": [],
                    "effect": "allow",
                    "reason": (
                        f"{tool} permitted "
                        f"within normal range"
                    ),
                })
                first = False
        else:
            final_rules.append({
                "comment": comment,
                "name": (
                    f"{final_effect}_{tool}"
                ),
                "match": {"tool": tool},
                "conditions": [],
                "effect": final_effect,
                "reason": reason,
            })

    # ── Step 6: Handle existing policy ──────
    if output_path.exists() and not args.force:
        print(
            f"\n{output_path} already exists.\n"
            f"  [1] Overwrite\n"
            f"  [2] Save as "
            f"policy.generated.yaml\n"
            f"  [3] Cancel\n"
        )
        choice = input(
            "Choice [1/2/3]: "
        ).strip()
        if choice == "2":
            output_path = Path(
                "policy.generated.yaml"
            )
        elif choice == "3":
            print("Cancelled.")
            sys.exit(0)

    # ── Step 7: Write policy.yaml ────────────
    now = datetime.now(
        timezone.utc
    ).strftime("%Y-%m-%d")
    method = (
        "Heuristic + AI (claude-haiku-4-5-20251001)"
        if ai_enriched
        else "Heuristic only"
    )

    lines = [
        "# " + "─" * 55,
        "# AgentGate — Auto-generated policy",
        f"# Generated: {now}",
        f"# Observations: {total} tool calls",
        f"# Method: {method}",
        "#",
        "# REVIEW CHECKLIST before enforcing:",
        "# [ ] Thresholds match your business "
        "rules",
        "# [ ] No legitimate actions blocked",
        "# [ ] Warnings reviewed manually",
        "#",
        "# Run: agentgate validate-policy",
        "#      after manual edits",
        "# " + "─" * 55,
        "",
        "policies:",
    ]

    for rule in final_rules:
        if rule.get("comment"):
            lines.append("")
            for line in rule[
                "comment"
            ].split("\n"):
                lines.append(line)
        lines.append(
            f"- name: {rule['name']}"
        )
        lines.append("  match:")
        lines.append(
            f"    tool: "
            f"{rule['match']['tool']}"
        )
        if rule.get("conditions"):
            lines.append("  conditions:")
            for cond in rule["conditions"]:
                lines.append(
                    f"  - field: "
                    f"{cond['field']}"
                )
                lines.append(
                    f"    op: {cond['op']}"
                )
                lines.append(
                    f"    value: {cond['value']}"
                )
        lines.append(
            f"  effect: {rule['effect']}"
        )
        lines.append(
            f"  reason: \"{rule['reason']}\""
        )

    output_path.write_text(
        "\n".join(lines) + "\n"
    )

    # ── Step 8: Summary ──────────────────────
    print(f"\n✓ {output_path} generated")
    print(
        f"  {len(tool_stats)} tools analysed"
    )
    print(
        f"  {len(final_rules)} policy rules"
    )
    if ai_enriched:
        confirmed = sum(
            1 for v in ai_enriched.values()
            if v.get("confirmed")
        )
        corrected = len(ai_enriched) - confirmed
        print(
            f"  AI: {confirmed} confirmed, "
            f"{corrected} corrected"
        )
    if warnings:
        print(
            f"\n  ⚠ {len(warnings)} warnings:"
        )
        for w in warnings:
            print(f"    {w}")

    print(f"""
Next steps:
  1. Review {output_path}
  2. agentgate validate-policy
  3. Set in .env:
     AGENTGATE_MODE=enforce
     AGENTGATE_POLICY_PATH=./{output_path}
  4. Restart your agent
""")


def cmd_validate_policy(
    args: argparse.Namespace
) -> None:
    """Validate policy.yaml for consistency."""
    import yaml

    policy_path = Path(
        args.path if args.path else "policy.yaml"
    )

    if not policy_path.exists():
        print(
            f"Error: {policy_path} not found.\n"
            f"Run: agentgate generate-policy"
        )
        sys.exit(1)

    try:
        with open(policy_path) as f:
            data = yaml.safe_load(f)
    except Exception as e:
        print(f"Error reading {policy_path}: {e}")
        sys.exit(1)

    policies = data.get("policies", [])
    if not policies:
        print(
            f"⚠ No policies found in "
            f"{policy_path}"
        )
        sys.exit(1)

    errors = []
    warnings = []
    seen_tools = {}

    READONLY_PREFIXES = (
        "get_", "list_", "fetch_",
        "read_", "search_", "find_",
        "lookup_", "check_",
    )

    for i, rule in enumerate(policies):
        tool = rule.get("match", {}).get("tool", "")
        action = rule.get("effect", "") or rule.get("action", "")

        if tool in seen_tools:
            warnings.append(
                f"Tool '{tool}' appears in rules "
                f"{seen_tools[tool]+1} and {i+1} "
                f"— only first match will fire"
            )
        seen_tools[tool] = i

        if any(
            tool.startswith(p)
            for p in READONLY_PREFIXES
        ) and action in ("block", "escalate"):
            warnings.append(
                f"'{tool}' looks read-only but "
                f"is set to {action} — intentional?"
            )

        if not action and not rule.get("conditions") \
           and not rule.get("effect"):
            errors.append(
                f"Rule {i+1} for '{tool}' has "
                f"no action or conditions"
            )

    if errors:
        print(f"\n✗ {len(errors)} error(s):")
        for e in errors:
            print(f"  {e}")

    if warnings:
        print(f"\n⚠ {len(warnings)} warning(s):")
        for w in warnings:
            print(f"  {w}")

    if not errors and not warnings:
        print(
            f"\n✓ {policy_path} looks good — "
            f"{len(policies)} rules, "
            f"no issues found"
        )
    elif not errors:
        print(
            f"\n✓ No errors — review warnings "
            f"before enforcing"
        )
    else:
        print(
            f"\n✗ Fix errors before enforcing"
        )
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="agentgate",
        description="The reliability layer for AI agents"
    )
    subparsers = parser.add_subparsers(dest="command")

    # init — creates .env.example only
    init_parser = subparsers.add_parser(
        "init",
        help="Create .env.example in current directory"
    )
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing files"
    )

    # check — runs quickcheck()
    subparsers.add_parser(
        "check",
        help="Verify installation (no API key needed)"
    )

    # version — prints __version__
    subparsers.add_parser(
        "version",
        help="Show installed version"
    )

    # generate-policy
    gen_parser = subparsers.add_parser(
        "generate-policy",
        help="Generate policy.yaml from "
             "observed tool calls"
    )
    gen_parser.add_argument(
        "--output",
        default="policy.yaml",
        help="Output file path "
             "(default: policy.yaml)"
    )
    gen_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing policy.yaml"
    )

    # validate-policy
    val_parser = subparsers.add_parser(
        "validate-policy",
        help="Validate policy.yaml for errors"
    )
    val_parser.add_argument(
        "--path",
        default="policy.yaml",
        help="Path to policy file "
             "(default: policy.yaml)"
    )

    args = parser.parse_args()

    if args.command == "init":
        cmd_init(args)
    elif args.command == "check":
        cmd_check(args)
    elif args.command == "version":
        cmd_version(args)
    elif args.command == "generate-policy":
        cmd_generate_policy(args)
    elif args.command == "validate-policy":
        cmd_validate_policy(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
