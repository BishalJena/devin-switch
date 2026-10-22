import sys


def command_prefix() -> list[str]:
    """argv that runs this `ds`, in both the frozen app and a Python install."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "devin_switch.cli"]
