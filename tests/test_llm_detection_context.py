import pytest

from backend.ai.control.executor import DryRunGateway
from backend.ai.control.router import DetectionContextGateway
from backend.models import DetectionAlert


@pytest.mark.asyncio
async def test_llm_gateway_reads_live_detection_provider():
    alert = DetectionAlert(object="person", confidence=.92, zone="Robot camera", timestamp="now")
    gateway = DetectionContextGateway(DryRunGateway(), lambda: [alert])
    assert await gateway.detections() == [alert]
    result = await gateway.stop(source="llm")
    assert result["dry_run"] is True
