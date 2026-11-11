"""Adapt the shared CommandGateway to the tool runner's async protocol."""

from __future__ import annotations

from typing import Any, Callable

from backend.gateway.gateway import CommandGateway, GatewayResult
from backend.robot.interface import MotionCommand

_gateway_provider: Callable[[], CommandGateway] | None = None


def configure_gateway_provider(provider: Callable[[], CommandGateway]) -> None:
    global _gateway_provider
    _gateway_provider = provider


def _shared_gateway() -> CommandGateway:
    if _gateway_provider is None:
        raise RuntimeError("shared CommandGateway provider is not configured")
    return _gateway_provider()


class CommandGatewayAdapter:
    def __init__(self, gateway: CommandGateway | Callable[[], CommandGateway], *, source: str = "llm") -> None:
        self._gateway_source = gateway
        self.source = source
        self._preempt_callbacks: list[Callable[[str], Any]] = []
        self._registered_gateway: CommandGateway | None = None

    @property
    def gateway(self) -> CommandGateway:
        gateway = self._gateway_source() if callable(self._gateway_source) else self._gateway_source
        if gateway is None:
            raise RuntimeError("CommandGateway is not initialized")
        if gateway is not self._registered_gateway:
            for callback in self._preempt_callbacks:
                gateway.on_preempt(callback)
            self._registered_gateway = gateway
        return gateway

    def on_preempt(self, callback: Callable[[str], Any]) -> None:
        self._preempt_callbacks.append(callback)
        if self._registered_gateway is not None:
            self._registered_gateway.on_preempt(callback)

    @staticmethod
    def _dict(result: GatewayResult) -> dict[str, Any]:
        return result.to_dict()

    async def run_bounded(self, direction: str, seconds: float, speed: float, *, source: str) -> Any:
        return self._dict(await self.gateway.run_bounded(direction, seconds, speed, source=self.source))

    async def turn(self, degrees: float, *, source: str) -> Any:
        return self._dict(await self.gateway.run_turn(degrees, source=self.source))

    async def stop(self, *, source: str) -> Any:
        return self._dict(await self.gateway.stop(source=self.source))

    async def set_style(self, intensity: str, gait: str, *, source: str) -> Any:
        return self._dict(await self.gateway.set_style(intensity, gait, source=self.source))

    async def run_action(self, action_id: int, *, source: str) -> Any:
        return self._dict(await self.gateway.run_action(action_id, source=self.source))

    async def status(self) -> dict[str, Any]:
        return await self.gateway.status(source=self.source)

    async def detections(self) -> list[Any]:
        return await self.gateway.detections()


class VoiceGatewayAdapter(CommandGatewayAdapter):
    """Voice entry points use the same CommandGateway with source=voice."""

    def __init__(self, gateway: CommandGateway | Callable[[], CommandGateway]) -> None:
        super().__init__(gateway, source="voice")

    async def motion(self, command: MotionCommand) -> dict[str, Any]:
        return self._dict(await self.gateway.motion(command, source="voice"))

    async def emergency_stop(self) -> dict[str, Any]:
        return self._dict(await self.gateway.emergency_stop(source="voice"))

