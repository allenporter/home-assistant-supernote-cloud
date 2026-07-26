"""Tests for the config flow."""

from unittest.mock import AsyncMock, MagicMock, patch

from freezegun import freeze_time
from homeassistant import config_entries
from homeassistant.const import (
    CONF_ACCESS_TOKEN,
    CONF_PASSWORD,
    CONF_UNIQUE_ID,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
)
from supernote.client.exceptions import ApiException, SmsVerificationRequired

from custom_components.supernote_cloud.const import (
    CONF_API_USERNAME,
    CONF_HOST,
    CONF_TOKEN_TIMESTAMP,
    DOMAIN,
)

from .conftest import CONFIG_ENTRY_ID


@freeze_time("2021-01-01 12:00:00")
async def test_full_flow(
    hass: HomeAssistant,
    mock_supernote: MagicMock,
) -> None:
    """Test selecting a device in the configuration flow."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result.get("type") is FlowResultType.FORM
    assert result.get("errors") is None

    # Setup mock return values
    mock_supernote.token = "access-token-1"
    mock_supernote.web.query_user.return_value.user_id = "654321"
    mock_supernote.web.query_user.return_value.user_name = "some-user-name"

    with patch(
        f"custom_components.{DOMAIN}.async_setup_entry", return_value=True
    ) as mock_setup:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_USERNAME: "username",
                CONF_PASSWORD: "password",
                CONF_HOST: "https://supernote.local",
            },
        )
        await hass.async_block_till_done()

    assert result.get("type") is FlowResultType.CREATE_ENTRY
    assert result.get("title") == "username"
    assert result.get("data") == {}
    assert result.get("options") == {
        CONF_USERNAME: "username",
        CONF_PASSWORD: "password",
        CONF_ACCESS_TOKEN: "access-token-1",
        CONF_UNIQUE_ID: "username",
        CONF_API_USERNAME: "username",
        CONF_TOKEN_TIMESTAMP: 1609502400,
        CONF_HOST: "https://supernote.local",
    }
    config_entry = result.get("result")
    assert config_entry
    assert config_entry.unique_id == "username"

    assert len(mock_setup.mock_calls) == 1


async def test_login_failed(
    hass: HomeAssistant,
    mock_supernote: MagicMock,
) -> None:
    """Test selecting a device in the configuration flow."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result.get("type") is FlowResultType.FORM
    assert result.get("errors") is None

    # Simulate login failure
    with patch(
        "supernote.client.api.Supernote.login", side_effect=ApiException("Login failed")
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_USERNAME: "username",
                CONF_PASSWORD: "password",
                CONF_HOST: "https://supernote.local",
            },
        )
        await hass.async_block_till_done()

    assert result.get("type") is FlowResultType.FORM
    assert result.get("errors") == {"base": "api_error"}


async def test_user_query_failed(
    hass: HomeAssistant,
    mock_supernote: MagicMock,
) -> None:
    """Test selecting a device in the configuration flow."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result.get("type") is FlowResultType.FORM
    assert result.get("errors") is None

    mock_supernote.device.get_capacity.side_effect = ApiException("Query failed")

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_USERNAME: "username",
            CONF_PASSWORD: "password",
            CONF_HOST: "https://supernote.local",
        },
    )
    await hass.async_block_till_done()
    assert result.get("type") is FlowResultType.FORM
    assert result.get("errors") == {"base": "api_error"}


@freeze_time("2021-01-01 12:00:00")
async def test_reauth_flow(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_supernote: MagicMock,
) -> None:
    """Test reauth flow."""
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    old_timestamp = config_entry.options[CONF_TOKEN_TIMESTAMP]

    hass.config_entries.async_update_entry(
        config_entry,
        options={
            **config_entry.options,
            CONF_USERNAME: "reauth-user-name",
            CONF_HOST: "https://custom-host.local",
        },
    )

    config_entry.async_start_reauth(hass)
    await hass.async_block_till_done()

    mock_supernote.token = "access-token-1"
    mock_supernote.web.query_user.return_value.user_id = CONFIG_ENTRY_ID

    # Advance through the reauth flow
    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    result = flows[0]
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result.get("type") is FlowResultType.FORM
    assert result.get("step_id") == "user"

    schema = result["data_schema"].schema
    host_key = next(k for k in schema if k == CONF_HOST)
    username_key = next(k for k in schema if k == CONF_USERNAME)
    assert host_key.default() == "https://custom-host.local"
    assert username_key.default() == "reauth-user-name"

    with patch(
        f"custom_components.{DOMAIN}.async_setup_entry", return_value=True
    ) as mock_setup:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_USERNAME: CONFIG_ENTRY_ID,
                CONF_PASSWORD: "password",
                CONF_HOST: "https://supernote.local",
            },
        )
        await hass.async_block_till_done()
    assert result.get("type") is FlowResultType.ABORT
    assert result.get("reason") == "reauth_successful"

    assert config_entry.title == CONFIG_ENTRY_ID
    assert config_entry.data == {}
    assert config_entry.options[CONF_TOKEN_TIMESTAMP] != old_timestamp
    assert config_entry.options == {
        CONF_USERNAME: CONFIG_ENTRY_ID,
        CONF_PASSWORD: "password",
        CONF_ACCESS_TOKEN: "access-token-1",
        CONF_UNIQUE_ID: CONFIG_ENTRY_ID,
        CONF_API_USERNAME: CONFIG_ENTRY_ID,
        CONF_TOKEN_TIMESTAMP: 1609502400.0,
        CONF_HOST: "https://supernote.local",
    }

    assert len(mock_setup.mock_calls) == 1

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1


@freeze_time("2021-01-01 12:00:00")
async def test_reauth_config_entry_mismatch(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_supernote: MagicMock,
) -> None:
    """Test reauth flow."""
    config_entry.async_start_reauth(hass)
    await hass.async_block_till_done()

    mock_supernote.token = "access-token-1"
    mock_supernote.web.query_user.return_value.user_id = "654321"
    mock_supernote.web.query_user.return_value.user_name = "other-user-name"

    # Advance through the reauth flow
    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    result = flows[0]
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result.get("type") is FlowResultType.FORM
    assert result.get("step_id") == "user"

    with patch(
        f"custom_components.{DOMAIN}.async_setup_entry", return_value=True
    ) as mock_setup:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_USERNAME: "other-user",
                CONF_PASSWORD: "password",
                CONF_HOST: "https://supernote.local",
            },
        )
        await hass.async_block_till_done()
    assert result.get("type") is FlowResultType.ABORT
    assert result.get("reason") == "unique_id_mismatch"

    assert len(mock_setup.mock_calls) == 0


@freeze_time("2021-01-01 12:00:00")
async def test_duplicate_config_entry(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_supernote: MagicMock,
) -> None:
    """Test selecting a device in the configuration flow."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result.get("type") is FlowResultType.FORM
    assert result.get("errors") is None

    mock_supernote.token = "access-token-1"
    # Existing entry has unique_id = CONFIG_ENTRY_ID ("user-identifier-1")

    # In this test, config_entry is created with unique_id="user-identifier-1".
    # User input username="user-identifier-1".

    mock_supernote.web.query_user.return_value.user_id = "654321"
    mock_supernote.web.query_user.return_value.user_name = CONFIG_ENTRY_ID

    with patch(
        f"custom_components.{DOMAIN}.async_setup_entry", return_value=True
    ) as mock_setup:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_USERNAME: CONFIG_ENTRY_ID,
                CONF_PASSWORD: "password",
                CONF_HOST: "https://supernote.local",
            },
        )
        await hass.async_block_till_done()

    assert result.get("type") is FlowResultType.ABORT
    assert result.get("reason") == "already_configured"

    assert len(mock_setup.mock_calls) == 0


async def test_sms_flow(
    hass: HomeAssistant,
    mock_supernote: MagicMock,
) -> None:
    """Test the SMS verification flow."""

    # 1. Initial Login - Fails with SMS requirement
    # Patch LoginClient in config_flow.py used for SMS

    with (
        patch(
            "custom_components.supernote_cloud.config_flow.LoginClient"
        ) as mock_login_client_cls,
        patch(
            "supernote.client.api.Supernote.login",
            side_effect=SmsVerificationRequired("SMS required", "1234567890"),
        ),
    ):
        mock_login_client = mock_login_client_cls.return_value
        mock_login_client.sms_login = AsyncMock(return_value="access-token-sms")
        mock_login_client.request_sms_code = AsyncMock()

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_USERNAME: "sms-user",
                CONF_PASSWORD: "test-password",
                CONF_HOST: "https://supernote.local",
            },
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "sms"
        assert result["errors"] is None

        # Verify request_sms_code called
        mock_login_client.request_sms_code.assert_called_with("sms-user")

        # Mock successful SMS login which returns token
        mock_supernote.token = "access-token-sms"
        mock_supernote.web.query_user.return_value.user_id = "12345"
        mock_supernote.web.query_user.return_value.user_name = "sms-user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "code": "123456",
            },
        )

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["title"] == "sms-user"
        assert result["options"][CONF_ACCESS_TOKEN] == "access-token-sms"
        assert result["options"][CONF_USERNAME] == "sms-user"
