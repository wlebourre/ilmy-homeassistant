"""Constants for the Ilmy integration."""

from typing import Final

DOMAIN: Final = "ilmy"

# Config entry data keys
CONF_HIJRI_URL: Final = "hijri_url"
CONF_HADITH_URL: Final = "hadith_url"
CONF_QURAN_URL: Final = "quran_url"
CONF_TIME_ZONE_NAME: Final = "time_zone_name"

# Config entry options keys
CONF_METHOD: Final = "method"
CONF_ASR: Final = "asr"
CONF_HADITH_BOOK: Final = "hadith_book"

# OAuth2 / IAM
# Public client registered in IAM's oauth_clients table (migration 020).
OAUTH_CLIENT_ID: Final = "homeassistant"
DEFAULT_IAM_URL: Final = "https://iam.ilmy.org"
OAUTH_AUTHORIZE_URL: Final = f"{DEFAULT_IAM_URL}/authorize"
OAUTH_TOKEN_URL: Final = f"{DEFAULT_IAM_URL}/oauth/token"

# Default service base URLs (overridable in the config flow for self-hosters)
DEFAULT_HIJRI_URL: Final = "https://hijri.ilmy.org"
DEFAULT_HADITH_URL: Final = "https://hadith.ilmy.org"
DEFAULT_QURAN_URL: Final = "https://quran.ilmy.org"

# Defaults
DEFAULT_METHOD: Final = "mwl"
DEFAULT_ASR: Final = 1  # 1 = Shafi'i/Maliki/Hanbali, 2 = Hanafi
DEFAULT_LANGUAGE: Final = "en"
DEFAULT_HADITH_BOOK: Final = "bukhari"

# Bus event fired at every prayer time
EVENT_PRAYER: Final = "ilmy_prayer_time"

# Prayer time keys, in chronological order as returned by the hijri API
PRAYERS: Final = ["fajr", "sunrise", "dhuhr", "asr", "maghrib", "isha", "midnight"]

# Total number of ayat in the ayat table (contiguous global ids 1..6236)
TOTAL_AYAT: Final = 6236

# How many alternative id_in_book values to try when a by-ref lookup 404s
# (numbers that exist only as supplementary chains are not resolvable)
HADITH_404_RETRIES: Final = 10

SUPPORTED_LANGUAGES: Final = ["en", "fr", "ar"]
