"""Closure rule for capped / lidded labware (touch + intent), shared by tests, chemlab hosts and Matterix.

The user's rule for robot interaction is "touch + intent, don't fight contact physics": a screw cap comes off when the gripper
holds the cap and the wrist has turned it far enough in the unscrew sense; it goes back on when the cap sits at its seat and is
turned back the same angle.  The asset data (geometry_manifest.json "labware.closure") gives the axis, the sign and the angle.

    c = Closure.from_manifest(manifest["labware"]["closure"])
    c.twist(+3.0, cap_grasped=True)   -> "loosened"
    c.twist(+5.0, cap_grasped=True)   -> "detached"       (cap is now a free rigid body: parts/cap.urdf)
    c.seat(at_seat=True); c.twist(-7.9, cap_grasped=True) -> "closed"

Hinged lids (Safe-Lock tube, desiccator cabinet door ...) use HingedClosure: open when the joint passes open_fraction of its range.
"""
from __future__ import annotations

from dataclasses import dataclass, field


class ClosureError(RuntimeError):
    pass


@dataclass
class Closure:
    detach_after_rad: float
    unscrew_sign: int = 1
    state: str = "closed"                 # closed | loosened | detached
    turned_rad: float = 0.0               # cumulative unscrew angle while on the thread (0 .. detach_after_rad)
    at_seat: bool = True
    log: list = field(default_factory=list)

    @classmethod
    def from_manifest(cls, closure: dict) -> "Closure":
        if closure.get("type") != "screw_cap":
            raise ClosureError(f"not a screw cap: {closure.get('type')}")
        return cls(detach_after_rad=float(closure["detach_after_rad"]), unscrew_sign=int(closure.get("unscrew_sign", 1)))

    def twist(self, delta_rad: float, cap_grasped: bool) -> str:
        """Wrist rotation about the cap axis while touching the cap (delta in the asset's +Z sense)."""
        if not cap_grasped:
            self.log.append(("ignored_no_grasp", delta_rad))
            return self.state                 # touching without a grasp does nothing (no contact-physics unscrewing)
        d = delta_rad * self.unscrew_sign
        if self.state == "detached":
            if not self.at_seat:
                self.log.append(("ignored_not_seated", delta_rad))
                return self.state             # a free cap turned in the air stays free
            self.turned_rad = self.detach_after_rad
            self.state = "loosened"
        self.turned_rad = min(max(self.turned_rad + d, 0.0), self.detach_after_rad)
        if self.turned_rad >= self.detach_after_rad - 1e-9 and d > 0:
            self.state, self.at_seat = "detached", False
        elif self.turned_rad <= 1e-9:
            self.state = "closed"
        else:
            self.state = "loosened"
        self.log.append(("twist", delta_rad, self.state))
        return self.state

    def seat(self, at_seat: bool) -> None:
        """The host reports that a detached cap was put back on the thread (pose within tolerance of cap_seat)."""
        if self.state == "detached":
            self.at_seat = bool(at_seat)

    @property
    def is_open(self) -> bool:
        return self.state == "detached"


@dataclass
class HingedClosure:
    open_rad: float
    open_fraction: float = 0.5
    q: float = 0.0

    def set_angle(self, q: float) -> str:
        self.q = max(0.0, min(q, self.open_rad))
        return self.state

    @property
    def state(self) -> str:
        return "open" if self.q >= self.open_fraction * self.open_rad else "closed"
