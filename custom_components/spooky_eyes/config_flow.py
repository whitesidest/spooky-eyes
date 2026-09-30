"""Config flow: zeroconf discovery (`_spookyeyes._tcp`) or a manually entered host."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import SpookyEyesApiError, SpookyEyesClient
from .const import DEFAULT_PORT, DOMAIN

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
    }
)


def _normalize_id(raw: Any) -> str:
    return str(raw).replace(":", "").lower()


class SpookyEyesConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None
        self._port: int = DEFAULT_PORT
        self._name: str | None = None

    async def _fetch_info(self, host: str, port: int) -> dict[str, Any]:
        client = SpookyEyesClient(async_get_clientsession(self.hass), host, port)
        return await client.get_info()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input.get(CONF_PORT, DEFAULT_PORT)
            try:
                info = await self._fetch_info(host, port)
            except SpookyEyesApiError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(_normalize_id(info.get("id") or info.get("mac")))
                self._abort_if_unique_id_configured(updates={CONF_HOST: host, CONF_PORT: port})
                return self.async_create_entry(
                    title=info.get("name") or host, data={CONF_HOST: host, CONF_PORT: port}
                )
        return self.async_show_form(step_id="user", data_schema=USER_SCHEMA, errors=errors)

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        device_id = discovery_info.properties.get("id")
        if not device_id:
            return self.async_abort(reason="invalid_discovery")
        self._host = discovery_info.host
        self._port = discovery_info.port or DEFAULT_PORT
        await self.async_set_unique_id(_normalize_id(device_id))
        # A board that moved to a new IP updates its existing entry and stops here.
        self._abort_if_unique_id_configured(updates={CONF_HOST: self._host, CONF_PORT: self._port})

        try:
            info = await self._fetch_info(self._host, self._port)
        except SpookyEyesApiError:
            return self.async_abort(reason="cannot_connect")
        self._name = info.get("name") or discovery_info.hostname.removesuffix(".local.")
        self.context["title_placeholders"] = {"name": self._name}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title=self._name or self._host,
                data={CONF_HOST: self._host, CONF_PORT: self._port},
            )
        self._set_confirm_only()
        return self.async_show_form(
            step_id="zeroconf_confirm",
            description_placeholders={"name": self._name, "host": self._host},
        )
