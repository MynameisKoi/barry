"""Pytest configuration and test isolation fixtures."""

import os

# Enforce simulation robot mode, mock LLM, and synthetic camera during automated test suites
os.environ["ROBOT_MODE"] = "simulation"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["FALLBACK_LLM_PROVIDER"] = "mock"
os.environ["CAMERA_DEVICE"] = "synthetic"

import pytest
from backend.ai.providers.mock_provider import MockProvider
from backend.robot.simulator import RobotSimulator
import backend.main


@pytest.fixture(autouse=True)
def isolate_robot_state():
    """Ensure every test runs against clean simulated robot and mock AI instances."""
    backend.main.robot = RobotSimulator()
    backend.main.robot.reset_emergency_stop()
    backend.main.ai_orchestrator.primary_provider = MockProvider(model_name="barry-mock-v1")
    backend.main.ai_orchestrator.fallback_provider = MockProvider(model_name="barry-mock-v1")
    yield
    backend.main.robot.reset_emergency_stop()
