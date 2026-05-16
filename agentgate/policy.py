from __future__ import annotations
import logging
import operator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agentgate.models import Effect, ToolCall

logger = logging.getLogger(__name__)


OPS = {
    "eq": operator.eq,
    "ne": operator.ne,
    "gt": operator.gt,
    "gte": operator.ge,
    "lt": operator.lt,
    "lte": operator.le,
    "in": lambda a, b: a in b,
    "not_in": lambda a, b: a not in b,
}


@dataclass
class PolicyResult:
    effect: Effect
    policy_name: str | None
    reason: str


class PolicyLoader:
    def __init__(self, path: str):
        self.path = Path(path)
        self._policies: list[dict] = []
        self._observer: Any = None
        self._load_failed: bool = False
        self._load()

    @classmethod
    def from_list(cls, policies: list) -> "PolicyLoader":
        """Create a PolicyLoader from a list of policy dicts — no file needed."""
        instance = object.__new__(cls)
        instance.path = Path(":memory:")
        instance._policies = policies
        instance._observer = None
        instance._load_failed = False
        return instance

    _VALID_EFFECTS = frozenset({"allow", "block", "escalate"})

    def _load(self) -> None:
        # Fail closed on any load error — empty / missing / malformed YAML
        # must NOT be silently treated as "no policies" because that would let
        # every request through under the default-deny evaluator below.
        if not self.path.exists():
            self._load_failed = True
            self._policies = []
            logger.error(
                "Policy file not found at %s — all requests will be DENIED "
                "until policy is in place.",
                self.path,
            )
            return
        try:
            with open(self.path) as f:
                data = yaml.safe_load(f) or {}
            raw = data.get("policies", [])
            self._policies = self._validate(raw)
            self._load_failed = False
        except Exception as e:
            self._load_failed = True
            self._policies = []
            logger.error(
                "Policy file failed to load: %s — all requests will be "
                "DENIED until policy is fixed.",
                e,
            )

    def _validate(self, policies: list) -> list:
        """Validate policies at load time; warn and skip malformed entries."""
        valid = []
        for i, p in enumerate(policies):
            name = p.get("name", f"policy[{i}]")
            effect = p.get("effect", "")
            if effect not in self._VALID_EFFECTS:
                logger.warning("Policy %r has unknown effect %r — skipping", name, effect)
                continue
            if "match" not in p:
                logger.warning("Policy %r has no 'match' block — will match all calls", name)
            for j, cond in enumerate(p.get("conditions", [])):
                if "field" not in cond:
                    logger.warning("Policy %r condition[%d] missing 'field' — condition ignored", name, j)
                if "op" not in cond:
                    logger.warning("Policy %r condition[%d] missing 'op' — condition ignored", name, j)
                if cond.get("op") not in OPS and "op" in cond:
                    logger.warning("Policy %r condition[%d] unknown op %r — condition ignored", name, j, cond["op"])
            valid.append(p)
        return valid

    def reload(self) -> None:
        self._load()
        logger.info("Policies reloaded from %s (%d rules)", self.path, len(self._policies))

    def save(self) -> None:
        """Atomically write current in-memory policies back to the YAML file. No-op for :memory: loaders."""
        if str(self.path) == ":memory:":
            return
        tmp = self.path.with_suffix(".yaml.tmp")
        try:
            with open(tmp, "w") as f:
                yaml.dump({"policies": self._policies}, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
            tmp.replace(self.path)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        logger.info("Policies saved to %s (%d rules)", self.path, len(self._policies))

    def start_watching(self) -> None:
        """
        Watch the policy YAML file for changes and reload automatically.
        Uses watchdog if available; logs a warning and skips if not installed.
        Does nothing if already watching.
        """
        if self._observer is not None:
            return
        try:
            from watchdog.events import FileSystemEventHandler  # type: ignore[import]
            from watchdog.observers import Observer  # type: ignore[import]
        except ImportError:
            logger.warning(
                "watchdog not installed — policy hot-reload disabled. "
                "Install with: pip install watchdog"
            )
            return

        loader = self

        class _Handler(FileSystemEventHandler):
            def on_modified(self, event: Any) -> None:
                if Path(event.src_path).resolve() == loader.path.resolve():
                    try:
                        loader.reload()
                    except Exception as exc:
                        logger.error("Policy reload failed: %s", exc)

        observer = Observer()
        observer.schedule(_Handler(), str(self.path.parent), recursive=False)
        observer.daemon = True
        observer.start()
        self._observer = observer
        logger.info("Watching %s for policy changes", self.path)

    def stop_watching(self) -> None:
        """Stop the file watcher if running."""
        if self._observer is not None:
            self._observer.stop()
            self._observer = None

    @property
    def policies(self) -> list[dict]:
        return self._policies


class PolicyEvaluator:
    def __init__(self, loader: PolicyLoader):
        self._loader = loader

    def evaluate(self, tool_call: ToolCall) -> PolicyResult:
        # Fail-closed: if the policy file failed to load, deny everything.
        if getattr(self._loader, "_load_failed", False):
            return PolicyResult(
                effect=Effect.BLOCK,
                policy_name=None,
                reason="Policy file failed to load — denying all requests for safety.",
            )

        for policy in self._loader.policies:
            if self._matches(policy, tool_call):
                effect = Effect(policy.get("effect", "allow"))
                return PolicyResult(
                    effect=effect,
                    policy_name=policy.get("name"),
                    reason=policy.get("reason", f"Matched policy: {policy.get('name')}"),
                )
        # No policy matched — default DENY (fail-closed).
        return PolicyResult(
            effect=Effect.BLOCK,
            policy_name=None,
            reason="No policy matched — defaulting to deny.",
        )

    def _matches(self, policy: dict, tool_call: ToolCall) -> bool:
        match = policy.get("match", {})
        # Check tool name
        if "tool" in match and match["tool"] != tool_call.tool_name:
            return False

        # Check conditions
        for condition in policy.get("conditions", []):
            value = self._resolve_field(condition["field"], tool_call)
            if value is None:
                # Field not present on this tool call — treat condition as
                # not matched rather than crashing on a None comparison.
                return False
            op_fn = OPS.get(condition["op"])
            if op_fn is None:
                continue
            target = (
                condition["value"]
                if "value" in condition
                else condition.get("values")
            )
            if not op_fn(value, target):
                return False

        return True

    def _resolve_field(self, field: str, tool_call: ToolCall) -> Any:
        """Resolve dot-notation field like 'args.amount' or 'context.user_role'"""
        parts = field.split(".")
        obj: Any = tool_call
        for part in parts:
            if isinstance(obj, dict):
                obj = obj.get(part)
            else:
                obj = getattr(obj, part, None)
        return obj
