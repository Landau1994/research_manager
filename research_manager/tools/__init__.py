"""Tool registry and built-in tool modules.

Importing this package registers all built-in tools via decorators.
"""

from research_manager.tools.registry import ToolRegistry, tool

# Side-effect imports — register all tools
from research_manager.tools import code_tools  # noqa: F401
from research_manager.tools import dynamic_tools  # noqa: F401
from research_manager.tools import env_tools  # noqa: F401
from research_manager.tools import package_tools  # noqa: F401
from research_manager.tools import project_tools  # noqa: F401
from research_manager.tools import writing_tools  # noqa: F401

__all__ = ["ToolRegistry", "tool"]
