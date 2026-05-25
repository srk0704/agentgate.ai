"""AgentGate CLI."""
from __future__ import annotations

import argparse
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

    args = parser.parse_args()

    if args.command == "init":
        cmd_init(args)
    elif args.command == "check":
        cmd_check(args)
    elif args.command == "version":
        cmd_version(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
