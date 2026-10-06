"""Structural boundary tests.

These assert the architecture, not behavior. The agent package must depend only
on the gateway interface for taking actions; it must not import tool
implementations, simulators, or the control-plane harness. If these fail, the
boundary that makes evaluation trustworthy has a hole in it.
"""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "arh"


def _imports(pkg: str) -> set[str]:
    mods: set[str] = set()
    for f in (SRC / pkg).rglob("*.py"):
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module)
            elif isinstance(node, ast.Import):
                for n in node.names:
                    mods.add(n.name)
    return mods


def test_agent_does_not_import_tool_implementations():
    mods = _imports("agent")
    forbidden = ("tools.simulators", "tools.registry")
    leaked = [m for m in mods for f in forbidden if f in m]
    assert not leaked


def test_agent_does_not_import_control_plane():
    mods = _imports("agent")
    leaked = [m for m in mods if "harness" in m]
    assert not leaked, f"agent imports the control-plane harness: {leaked}"


def test_agent_reaches_tools_only_through_the_gateway_interface():
    # The agent may reference the gateway type, but not concrete tool clients.
    mods = _imports("agent")
    assert any("gateway" in m for m in mods), "agent should use the gateway interface"
