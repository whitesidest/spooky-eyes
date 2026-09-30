from __future__ import annotations

from ipaddress import ip_address

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from custom_components.spooky_eyes.const import DOMAIN

from .conftest import BASE, DEVICE_ID, HOST


def _zeroconf(host: str = HOST, device_id: str | None = DEVICE_ID) -> ZeroconfServiceInfo:
    props = {"model": "dualeye-1.28", "fw": "0.1.0"}
    if device_id:
        props["id"] = device_id
    return ZeroconfServiceInfo(
        ip_address=ip_address(host),
        ip_addresses=[ip_address(host)],
        hostname="spooky-eyes-d4e5f6.local.",
        name="spooky-eyes-d4e5f6._spookyeyes._tcp.local.",
        port=80,
        type="_spookyeyes._tcp.local.",
        properties=props,
    )


async def test_user_flow_creates_entry(hass, board):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": HOST, "port": 80})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Porch Eyes"
    assert result["data"] == {"host": HOST, "port": 80}
    assert result["result"].unique_id == DEVICE_ID


async def test_user_flow_cannot_connect(hass, aioclient_mock):
    aioclient_mock.get(f"{BASE}/api/info", status=500)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": HOST, "port": 80})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_zeroconf_flow_confirms_and_creates(hass, board):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=_zeroconf()
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "zeroconf_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Porch Eyes"
    assert result["result"].unique_id == DEVICE_ID


async def test_zeroconf_already_configured_updates_host(hass, configured_entry):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=_zeroconf(host="192.168.1.99")
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert configured_entry.data["host"] == "192.168.1.99"


async def test_user_flow_already_configured(hass, configured_entry):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": HOST, "port": 80})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_zeroconf_without_id_aborts(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=_zeroconf(device_id=None)
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "invalid_discovery"
