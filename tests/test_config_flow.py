"""Tests for the Ilmy config and options flows."""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest
from aioresponses import aioresponses

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_LANGUAGE, CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ilmy.config_flow import IlmyConfigFlow
from custom_components.ilmy.const import (
    CONF_ASR,
    CONF_HADITH_BOOK,
    CONF_HADITH_URL,
    CONF_HIGH_LATITUDE,
    CONF_HIJRI_URL,
    CONF_METHOD,
    CONF_QURAN_URL,
    CONF_TIME_ZONE_NAME,
    DOMAIN,
)

HIJRI_URL = "https://hijri.example.com"
HADITH_URL = "https://hadith.example.com"
QURAN_URL = "https://quran.example.com"

HIJRI_RE = re.compile(r"^https://hijri\.example\.com/api/v1/hijri.*")
BOOKS_RE = re.compile(r"^https://hadith\.example\.com/api/v1/books.*")
SURAHS_RE = re.compile(r"^https://quran\.example\.com/api/v1/surahs.*")

HIJRI_PAYLOAD = {
    "day": 20,
    "month": 12,
    "year": 1447,
    "month_name": "Dhul-Hijjah",
    "month_name_ar": "ذو الحجة",
    "formatted": "20 Dhul-Hijjah 1447",
    "formatted_ar": "٢٠ ذو الحجة ١٤٤٧",
    "is_ramadan": False,
}
BOOKS_PAYLOAD = [
    {"slug": "bukhari", "english_title": "Sahih al-Bukhari", "total_hadiths": 7563},
    {"slug": "muslim", "english_title": "Sahih Muslim", "total_hadiths": 3033},
]
SURAHS_PAYLOAD = [
    {"id": 1, "number": 1, "english_name": "Al-Fatihah", "arabic_name": "الفاتحة"},
]

USER_INPUT = {
    CONF_HIJRI_URL: HIJRI_URL,
    CONF_HADITH_URL: HADITH_URL,
    CONF_QURAN_URL: QURAN_URL,
    CONF_LATITUDE: 33.5731,
    CONF_LONGITUDE: -7.5898,
    CONF_TIME_ZONE_NAME: "Africa/Casablanca",
    CONF_LANGUAGE: "en",
    CONF_HADITH_BOOK: "bukhari",
}


def _mock_services(m: aioresponses) -> None:
    """Mock the three endpoints used by the connectivity validation."""
    m.get(HIJRI_RE, status=200, payload=HIJRI_PAYLOAD)
    m.get(BOOKS_RE, status=200, payload=BOOKS_PAYLOAD)
    m.get(SURAHS_RE, status=200, payload=SURAHS_PAYLOAD)


def _mock_book_options(m: aioresponses) -> None:
    """Mock the book-list fetch used to build the form dropdowns."""
    m.get(BOOKS_RE, status=200, payload=BOOKS_PAYLOAD, repeat=True)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load the custom integration for every test in this module."""
    yield


async def test_form_has_single_user_step_and_no_auth(hass: HomeAssistant) -> None:
    """The flow is one anonymous user step — no OAuth/external/auth steps."""
    steps = {
        name for name in vars(IlmyConfigFlow) if name.startswith("async_step_")
    }
    assert steps == {"async_step_user"}
    # No authentication of any kind: no OAuth/external/reauth steps anywhere
    # in the hierarchy.
    all_steps = {
        name
        for cls in IlmyConfigFlow.__mro__
        for name in vars(cls)
        if name.startswith("async_step_")
    }
    assert not [s for s in all_steps if "oauth" in s or "external" in s]
    assert "async_step_reauth" not in all_steps
    assert "async_step_reconfigure" not in vars(IlmyConfigFlow)

    with aioresponses() as m:
        _mock_book_options(m)
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}


async def test_form_prefilled_from_hass_config(hass: HomeAssistant) -> None:
    """Latitude/longitude/time zone default to the HA-configured values."""
    hass.config.latitude = 33.5731
    hass.config.longitude = -7.5898
    hass.config.time_zone = "Africa/Casablanca"

    with aioresponses() as m:
        _mock_book_options(m)
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

    defaults = result["data_schema"]({})
    assert defaults[CONF_LATITUDE] == pytest.approx(33.5731)
    assert defaults[CONF_LONGITUDE] == pytest.approx(-7.5898)
    assert defaults[CONF_TIME_ZONE_NAME] == "Africa/Casablanca"


@pytest.mark.parametrize(
    ("country", "expected_method"),
    [("MA", "morocco"), (None, "mwl"), ("XX", "mwl")],
)
async def test_happy_path_creates_entry(
    hass: HomeAssistant, country: str | None, expected_method: str
) -> None:
    """Submitting valid input creates the entry (unique_id 'ilmy'), with the
    calculation method derived from the HA-configured country."""
    hass.config.country = country

    with aioresponses() as m:
        _mock_book_options(m)
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        assert result["type"] == FlowResultType.FORM

        _mock_services(m)
        with patch(
            "custom_components.ilmy.async_setup_entry", return_value=True
        ) as mock_setup:
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], dict(USER_INPUT)
            )
            await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Ilmy"
    assert result["data"] == {
        CONF_HIJRI_URL: HIJRI_URL,
        CONF_HADITH_URL: HADITH_URL,
        CONF_QURAN_URL: QURAN_URL,
        CONF_LATITUDE: 33.5731,
        CONF_LONGITUDE: -7.5898,
        CONF_TIME_ZONE_NAME: "Africa/Casablanca",
        CONF_METHOD: expected_method,
    }
    assert result["options"] == {
        CONF_LANGUAGE: "en",
        CONF_HADITH_BOOK: "bukhari",
    }
    assert result["result"].unique_id == "ilmy"
    assert len(mock_setup.mock_calls) == 1


async def test_connectivity_failure(hass: HomeAssistant) -> None:
    """An unreachable service produces a cannot_connect error on the form."""
    with aioresponses() as m:
        _mock_book_options(m)
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        m.get(HIJRI_RE, status=500)
        m.get(BOOKS_RE, status=200, payload=BOOKS_PAYLOAD)
        m.get(SURAHS_RE, status=200, payload=SURAHS_PAYLOAD)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], dict(USER_INPUT)
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_invalid_and_non_https_urls(hass: HomeAssistant) -> None:
    """Malformed URLs and plain-http public hosts are rejected per field."""
    bad_input = dict(
        USER_INPUT,
        **{
            CONF_HIJRI_URL: "not-a-url",
            CONF_HADITH_URL: "http://hadith.example.com",  # public host: https required
            CONF_QURAN_URL: "https://user:pass@quran.example.com",
        }
    )
    with aioresponses() as m:
        _mock_book_options(m)
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        _mock_book_options(m)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], bad_input
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {
        CONF_HIJRI_URL: "invalid_url",
        CONF_HADITH_URL: "https_required",
        CONF_QURAN_URL: "invalid_url",
    }


async def test_duplicate_aborts(hass: HomeAssistant) -> None:
    """Only one Ilmy entry can exist (unique_id 'ilmy')."""
    MockConfigEntry(domain=DOMAIN, unique_id="ilmy").add_to_hass(hass)

    with aioresponses() as m:
        _mock_book_options(m)
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], dict(USER_INPUT)
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow_persists_and_reloads(hass: HomeAssistant) -> None:
    """Changing method/asr/high-latitude/language/book/location updates the
    options and triggers a reload of the config entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="ilmy",
        data={
            CONF_HIJRI_URL: HIJRI_URL,
            CONF_HADITH_URL: HADITH_URL,
            CONF_QURAN_URL: QURAN_URL,
            CONF_LATITUDE: 33.5731,
            CONF_LONGITUDE: -7.5898,
            CONF_TIME_ZONE_NAME: "Africa/Casablanca",
            CONF_METHOD: "morocco",
        },
        options={CONF_LANGUAGE: "en", CONF_HADITH_BOOK: "bukhari"},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state.name == "LOADED"

    with aioresponses() as m:
        _mock_book_options(m)
        result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"

    new_options = {
        CONF_METHOD: "mwl",
        CONF_ASR: "2",
        CONF_HIGH_LATITUDE: "1",
        CONF_LANGUAGE: "fr",
        CONF_HADITH_BOOK: "muslim",
        CONF_LATITUDE: 48.8566,
        CONF_LONGITUDE: 2.3522,
        CONF_TIME_ZONE_NAME: "Europe/Paris",
    }
    with patch.object(
        hass.config_entries,
        "async_reload",
        wraps=hass.config_entries.async_reload,
    ) as mock_reload:
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], new_options
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options == {
        CONF_METHOD: "mwl",
        CONF_ASR: 2,
        CONF_HIGH_LATITUDE: 1,
        CONF_LANGUAGE: "fr",
        CONF_HADITH_BOOK: "muslim",
        CONF_LATITUDE: 48.8566,
        CONF_LONGITUDE: 2.3522,
        CONF_TIME_ZONE_NAME: "Europe/Paris",
    }
    mock_reload.assert_called_once_with(entry.entry_id)
    assert entry.state.name == "LOADED"

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
