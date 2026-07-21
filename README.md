# Ilmy — Home Assistant integration

Prayer times (salah), Hijri date, qibla, hadith of the day and verse of the
day for Home Assistant, backed by the Ilmy platform services:

- **hijri** — prayer-time calculation and Hijri calendar
- **hadith** — hadith collections
- **quran** — quran text and translations

## Authentication — sign in with your ilmy account

The integration uses **OAuth2 SSO**: during setup you are redirected to the
Ilmy sign-in page, you log in with your ilmy account, and Home Assistant
stores and refreshes the tokens automatically. There is no API key to copy.

Because every user signs in individually, the content follows **your
account's role** — e.g. authenticated tiers that include English hadith text
will see it in the hadith-of-the-day attributes.

> **My Home Assistant redirect:** the OAuth callback goes through
> `https://my.home-assistant.io/redirect/oauth` (the standard redirect for
> custom integrations). Make sure the [My Home Assistant](https://www.home-assistant.io/integrations/my/)
> integration is enabled (it is by default) and that your instance URL is
> reachable from it. If your instance cannot use My Home Assistant, ask the
> platform admin to register your local callback URL
> (`http://<your-ha>:8123/auth/external/callback`) in IAM's `oauth_clients`
> table.

## Features

- Prayer times as **timestamp sensors**, usable in `time` automation triggers
  exactly like the built-in `sun` integration — with `offset` support.
- An **`ilmy_prayer_time` event** fired at every prayer, usable as an
  `event` automation trigger.
- A ready-made **notification blueprint** for prayer-time alerts.
- Daily **hadith of the day** and **verse of the day** sensors (Arabic +
  translation in your chosen language).
- Hijri date and qibla direction sensors.
- Fully configured through the UI — no YAML.

## Installation

1. Copy `custom_components/ilmy` into your Home Assistant
   `config/custom_components/` directory (or add this repo as a HACS custom
   repository).
2. Restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration → Ilmy**.
4. Step 1 — service URLs (defaults `https://{hijri,hadith,quran}.ilmy.org`
   work for most users) and location, prefilled from your HA settings.
5. You are redirected to the **Ilmy sign-in page** — log in with your ilmy
   account; the browser returns to Home Assistant automatically.
6. Step 2 — calculation method (fetched live from the hijri service), Asr
   juristic school, content language (en/fr/ar) and the hadith collection
   used for the hadith of the day.

These settings can be changed later via **Configure** on the integration
card. If your tokens are revoked (e.g. password change), Home Assistant
prompts you to sign in again (re-authentication flow).

## Entities

| Entity | State | Notes |
| --- | --- | --- |
| `sensor.ilmy_fajr` … `sensor.ilmy_midnight` | timestamp | fajr, sunrise, dhuhr, asr, maghrib, isha, midnight |
| `sensor.ilmy_next_prayer` | prayer name | attribute: `time` |
| `sensor.ilmy_hijri_date` | e.g. `2 Dhul-Qa'dah 1446` | attributes: day/month/year, `is_ramadan`, … |
| `sensor.ilmy_qibla` | degrees from north | attribute: compass direction |
| `sensor.ilmy_hadith_of_the_day` | e.g. `Sahih al-Bukhari 756` | attributes: arabic, translation, book, chapter |
| `sensor.ilmy_verse_of_the_day` | e.g. `2:255` | attributes: uthmani_text, translation, surah, juz, page |

Long hadith/verse text lives in the attributes (HA states are limited to
255 characters).

## Automations

### Trigger on a prayer event

The integration fires `ilmy_prayer_time` with `prayer`, `time` and
`hijri_date` in the event data:

```yaml
triggers:
  - trigger: event
    event_type: ilmy_prayer_time
    event_data:
      prayer: maghrib
actions:
  - action: notify.notify
    data:
      message: "Maghrib time — {{ trigger.event.data.hijri_date }}"
```

### Trigger at a prayer time (like `sun` / `time`)

```yaml
triggers:
  - trigger: time
    at: sensor.ilmy_fajr
```

### Trigger with an offset (e.g. 10 minutes before Maghrib)

```yaml
triggers:
  - trigger: time
    at: sensor.ilmy_maghrib
    offset: "-00:10:00"
```

### Notification blueprint

Copy `blueprints/automation/ilmy/prayer_notification.yaml` from this
directory into your HA `config/blueprints/automation/ilmy/` folder, then go
to **Settings → Automations → Create automation → Ilmy prayer time
notification**. Pick the prayer, the notify target (e.g.
`mobile_app_my_phone`) and the message.

## Daily content

The hadith and verse of the day rotate deterministically with the calendar
day (no repeats within a collection's cycle) and refresh at local midnight.
The hadith collection and translation language are selectable in the
integration options.

## Development / smoke test

```bash
cd homeassistant
docker compose -f docker-compose.dev.yml up -d
# open http://localhost:8123, complete onboarding, then add the Ilmy integration
```

For a full local OAuth2 test you need a reachable IAM (dev stack in
`../iam`) and your HA dev instance's callback URL
(`http://localhost:8123/auth/external/callback`) registered in IAM's
`oauth_clients` table:

```sql
UPDATE oauth_clients
SET redirect_uris = redirect_uris || 'http://localhost:8123/auth/external/callback'
WHERE client_id = 'homeassistant';
```

Checklist:

1. Add the integration via the UI, sign in on the IAM page, and confirm all
   entities appear under **Settings → Devices & services → Ilmy**.
2. Check the states in **Developer tools → States** (prayer timestamps in
   your local time zone, hijri date, hadith/verse references).
3. Listen to `ilmy_prayer_time` in **Developer tools → Events** and wait for
   the next prayer (or check the logs for `Fired ilmy_prayer_time`).

## Structure

```
homeassistant/
├── custom_components/ilmy/   # the integration
├── blueprints/automation/ilmy/prayer_notification.yaml
└── docker-compose.dev.yml    # optional dev container
```
