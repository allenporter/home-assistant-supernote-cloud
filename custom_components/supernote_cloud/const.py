"""Constants for supernote_cloud."""

import datetime as dt

DOMAIN = "supernote_cloud"

CONF_API_USERNAME = "api_username"
CONF_TOKEN_TIMESTAMP = "token_timestamp"
CONF_HOST = "host"

DEFAULT_HOST = "https://cloud.supernote.com"
TOKEN_LIFETIME = dt.timedelta(days=5)
