"""Loop and non-progress detection.

The discriminator between a runaway loop and legitimate iterative work is
progress, not repetition. Each observed tool step contributes three signatures:

  action_signature       hash(tool + normalized arguments)
  observation_signature  hash(tool + status + result data)
  state_signature         hash of the outcome-relevant state slice (supplied
                          by the runner from the authoritative snapshot)

and a progress value: (committed side effects, distinct informative
observations). Informative means the tool executed and returned an observation
signature not seen before. Repeating an identical call, or re-observing the
same result, adds nothing to progress; a genuinely new account lookup or a new
committed side effect advances it.

Rules, in priority order:
  loop_exact         N consecutive identical action signatures
  loop_oscillation   a real 2-cycle (A,B,A,B) in composite signatures with no
                     progress across the cycle
  stall              K consecutive steps with no progress increase
  amplification      many executed calls per distinct informative outcome

Legitimate fan-out (distinct accounts, pagination) advances progress every
step, so none of these fire.
"""
from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel


def _h(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, default=str)


def action_signature(tool: str, arguments: dict) -> str:
    return _h(tool, _canon(arguments))


def observation_signature(tool: str, status: str, data: dict) -> str:
    return _h(tool, status, _canon(data))


class LoopConfig(BaseModel):
    exact_repeat: int = 3
    stall: int = 3
    amplification_ratio: float = 3.0
    amplification_min_calls: int = 6


class LoopDetector:
    def __init__(self, config: LoopConfig | None = None) -> None:
        self.cfg = config or LoopConfig()
        self._actions: list[str] = []
        self._composites: list[str] = []
        self._progress: list[tuple[int, int]] = []
        self._seen_obs: set[str] = set()
        self._distinct_obs = 0
        self._executed = 0
        self.evidence: dict = {}

    def observe(
        self, *, tool: str, arguments: dict, status: str, data: dict,
        side_effect_count: int, state_signature: str,
    ) -> str | None:
        a = action_signature(tool, arguments)
        o = observation_signature(tool, status, data)
        composite = _h(state_signature, a, o)

        if status == "executed":
            self._executed += 1
            if o not in self._seen_obs:
                self._seen_obs.add(o)
                self._distinct_obs += 1

        self._actions.append(a)
        self._composites.append(composite)
        self._progress.append((side_effect_count, self._distinct_obs))

        return (
            self._check_exact()
            or self._check_oscillation()
            or self._check_stall()
            or self._check_amplification()
        )

    def _check_exact(self) -> str | None:
        n = self.cfg.exact_repeat
        if len(self._actions) >= n and len(set(self._actions[-n:])) == 1:
            self.evidence = {"rule": "loop_exact", "action": self._actions[-1], "consecutive": n}
            return "loop_exact"
        return None

    def _check_oscillation(self) -> str | None:
        c = self._composites
        p = self._progress
        if len(c) >= 4 and c[-1] == c[-3] and c[-2] == c[-4] and c[-1] != c[-2] and p[-1] == p[-3]:
            self.evidence = {"rule": "loop_oscillation", "cycle": [c[-2], c[-1]]}
            return "loop_oscillation"
        return None

    def _check_stall(self) -> str | None:
        k = self.cfg.stall
        if len(self._progress) >= k and len(set(self._progress[-k:])) == 1:
            self.evidence = {"rule": "stall", "progress": self._progress[-1], "consecutive": k}
            return "stall"
        return None

    def _check_amplification(self) -> str | None:
        if self._executed < self.cfg.amplification_min_calls:
            return None
        distinct_useful = self._distinct_obs + self._progress[-1][0]  # obs + side effects
        ratio = self._executed / max(1, distinct_useful)
        if ratio > self.cfg.amplification_ratio:
            self.evidence = {"rule": "amplification", "executed": self._executed,
                             "distinct_useful": distinct_useful, "ratio": round(ratio, 2)}
            return "amplification"
        return None
