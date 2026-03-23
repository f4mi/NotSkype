"""control_center – Desktop GUI and tray runner for the CIT200 phone service."""

from .desktop_gui import launch_gui
from .tray_app    import launch_tray

__all__ = ["launch_gui", "launch_tray"]
