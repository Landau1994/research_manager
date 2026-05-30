"""Passive trajectory recording for future RL/MCTS data."""

from research_manager.recording.recorder import (
    TrajectoryRecorder,
    get_active_recorder,
    set_active_recorder,
)

__all__ = ["TrajectoryRecorder", "get_active_recorder", "set_active_recorder"]
