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
AGENTGATE_POLICY_PATH=./policy.yaml
AGENTGATE_ENV=development
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
    print("  1. cp .env.example .env")
    print("     Add your ANTHROPIC_API_KEY")
    print()
    print("  2. Run agentgate check to verify")
    print("     installation (no API key needed)")
    print()
    print("  3. Add 3 lines to your agent:")
    print()
    print("     gate = GatewayClient.from_env()")
    print("     decision = await gate.evaluate(tool_call)")
    print("     if decision.is_allowed:")
    print("         result = await my_tool(**args)")
    print()
    print("  4. Start the dashboard:")
    print("     uvicorn agentgate.api.main:app --port 8000")
    print()
    print("  5. Open http://localhost:8000/v2")
    print()
    print("Need help? Book a call:")
    print("  https://calendly.com/sk4975-columbia/30min")


def cmd_generate_policy(
    args: argparse.Namespace
) -> None:
    """Generate policy.yaml from observed tool calls."""
    import asyncio
    asyncio.run(_generate_policy_async(args))


async def _generate_policy_async(
    args: argparse.Namespace
) -> None:
    import json
    from collections import defaultdict
    from datetime import datetime, timezone

    db_path = os.getenv(
        "AGENTGATE_DB_PATH", "./agentgate.db"
    )
    min_obs = int(
        os.getenv("AGENTGATE_MIN_OBSERVATIONS", "500")
    )
    output_path = Path(
        args.output if args.output else "policy.yaml"
    )

    try:
        import aiosqlite
    except ImportError:
        print("Error: aiosqlite not installed.")
        sys.exit(1)

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

    print(
        f"\nAnalysing {total} observations..."
    )

    tool_stats: dict = defaultdict(lambda: {
        "count": 0,
        "numeric_args": defaultdict(list),
        "is_readonly": False,
        "is_destructive": False,
    })

    READONLY_PREFIXES = (
        "get_", "list_", "fetch_",
        "read_", "search_", "find_",
        "lookup_", "check_",
    )
    DESTRUCTIVE_PREFIXES = (
        "delete_", "remove_", "purge_",
        "destroy_", "drop_", "truncate_",
    )

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
                        if isinstance(v, (int, float)):
                            stats[
                                "numeric_args"
                            ][k].append(float(v))
                except Exception:
                    pass

    rules = []
    warnings = []

    for tool, stats in sorted(
        tool_stats.items(),
        key=lambda x: -x[1]["count"]
    ):
        count = stats["count"]
        freq_ratio = count / total

        if stats["is_readonly"]:
            rules.append({
                "tool": tool,
                "action": "allow",
                "comment": (
                    f"Read-only tool — observed "
                    f"{count}x ({freq_ratio:.0%} "
                    f"of all calls)"
                ),
            })

        elif stats["is_destructive"]:
            rules.append({
                "tool": tool,
                "action": "block",
                "comment": (
                    f"Destructive tool — block "
                    f"by default. Observed {count}x."
                ),
            })
            if count < 10:
                warnings.append(
                    f"⚠ {tool}: destructive, "
                    f"low sample ({count} obs) — "
                    f"verify block is correct"
                )

        elif stats["numeric_args"]:
            conditions = []
            arg_comments = []
            for arg, values in stats[
                "numeric_args"
            ].items():
                if not values:
                    continue
                max_val = max(values)
                p90 = sorted(values)[
                    int(len(values) * 0.9)
                ]
                escalate_at = round(p90 * 2, -2)
                block_at = round(max_val * 10, -2)
                conditions.append({
                    "arg": arg,
                    "escalate_at": escalate_at,
                    "block_at": block_at,
                })
                arg_comments.append(
                    f"{arg}: p90={p90:.0f} "
                    f"max={max_val:.0f} → "
                    f"escalate>={escalate_at:.0f} "
                    f"block>={block_at:.0f}"
                )
                if count < 20:
                    warnings.append(
                        f"⚠ {tool}.{arg}: low "
                        f"sample ({count} obs) — "
                        f"review threshold"
                    )
            rules.append({
                "tool": tool,
                "action": "threshold",
                "conditions": conditions,
                "comment": (
                    f"Observed {count}x. "
                    + " | ".join(arg_comments)
                ),
            })

        elif freq_ratio < 0.01:
            rules.append({
                "tool": tool,
                "action": "escalate",
                "comment": (
                    f"Rare tool — only {count}x "
                    f"({freq_ratio:.1%} of calls). "
                    f"Escalate as unusual."
                ),
            })
            warnings.append(
                f"⚠ {tool}: low frequency "
                f"({count} obs) — review "
                f"escalation"
            )
        else:
            rules.append({
                "tool": tool,
                "action": "escalate",
                "comment": (
                    f"Observed {count}x — "
                    f"escalate by default"
                ),
            })

    if output_path.exists() and not args.force:
        print(
            f"\npolicy.yaml already exists.\n"
            f"Options:\n"
            f"  [1] Overwrite with generated policy\n"
            f"  [2] Save as policy.generated.yaml\n"
            f"  [3] Cancel\n"
        )
        choice = input("Choice [1/2/3]: ").strip()
        if choice == "2":
            output_path = Path("policy.generated.yaml")
        elif choice == "3":
            print("Cancelled.")
            sys.exit(0)

    now = datetime.now(timezone.utc).strftime(
        "%Y-%m-%d"
    )
    lines = [
        "# " + "─" * 55,
        "# AgentGate — Auto-generated policy",
        f"# Generated: {now}",
        f"# Based on: {total} observed tool calls",
        "#",
        "# REVIEW CHECKLIST before enforcing:",
        "# [ ] Thresholds match your business rules",
        "# [ ] No legitimate actions will be blocked",
        "# [ ] Low-sample warnings reviewed manually",
        "# [ ] Destructive tools correctly blocked",
        "#",
        "# Run: agentgate validate-policy",
        "#      to re-validate after edits",
        "# " + "─" * 55,
        "",
        "policies:",
    ]

    for rule in rules:
        lines.append(f"  # {rule['comment']}")
        if rule["action"] == "allow":
            lines.append(f"  - tool: {rule['tool']}")
            lines.append(f"    action: allow")
        elif rule["action"] == "block":
            lines.append(f"  - tool: {rule['tool']}")
            lines.append(f"    action: block")
        elif rule["action"] == "escalate":
            lines.append(f"  - tool: {rule['tool']}")
            lines.append(f"    action: escalate")
        elif rule["action"] == "threshold":
            for cond in rule["conditions"]:
                lines.append(
                    f"  - tool: {rule['tool']}"
                )
                lines.append(f"    conditions:")
                lines.append(
                    f"      {cond['arg']} >= "
                    f"{cond['block_at']:.0f}: block"
                )
                lines.append(
                    f"      {cond['arg']} >= "
                    f"{cond['escalate_at']:.0f}: "
                    f"escalate"
                )
        lines.append("")

    output_path.write_text("\n".join(lines))

    print(f"\n✓ {output_path} generated")
    print(f"  {len(rules)} tools covered")

    if warnings:
        print(f"\n  {len(warnings)} warnings:")
        for w in warnings:
            print(f"  {w}")
        print(
            "\n  Review warnings before enforcing."
        )

    print(f"""
Next steps:
  1. Review {output_path}
     Pay attention to any warnings above

  2. agentgate validate-policy
     Re-validate after manual edits

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
        tool = rule.get("tool", "")
        action = rule.get("action", "")

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

        if not action and not rule.get("conditions"):
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
