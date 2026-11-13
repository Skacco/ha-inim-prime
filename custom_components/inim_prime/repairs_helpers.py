"""Repair notice shown when the panel refuses the Native connection password."""
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN


def _issue_id(entry_id: str) -> str:
    return f"native_password_rejected_{entry_id}"


def report_password_rejected(hass: HomeAssistant, entry_id: str) -> None:
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id(entry_id),
        is_fixable = False,
        severity = ir.IssueSeverity.ERROR,
        translation_key = "native_password_rejected",
    )


def clear_password_rejected(hass: HomeAssistant, entry_id: str) -> None:
    ir.async_delete_issue(hass, DOMAIN, _issue_id(entry_id))
