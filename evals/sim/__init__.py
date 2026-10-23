"""A simulated session: a desktop of simulated apps (PowerShell in Windows Terminal, Teams,
Outlook, Edge, a lock screen) that react to clicks, keys and the wheel, drawn like the
generated eval screens, behind the Source protocol. Every name, message and file is
generated from a seed; nothing comes from a real session."""

from evals.sim.desktop import SAFETY, Desktop, Event
from evals.sim.source import SimSource

__all__ = ["SAFETY", "Desktop", "Event", "SimSource"]
