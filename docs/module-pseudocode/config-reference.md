# Config Reference (Maintained Modules)

This is the practical config map used by the maintained Python runtime.

## Root

- `platform`: backend selector (`telegram_private`, `telegram`, `discord`, `local`).

## `audio`

- `sample_rate` (int): PCM sample rate, default `16000`.
- `channels` (int): channels, default `1`.
- `chunk_size` (int): callback frame size, default `960`.
- `meter_enabled` (bool): runtime meter logs.
- `meter_interval_s` (float): meter interval.
- `meter_level` (string): `debug`/`info` log level hint.

## `contacts`

List behavior:
- `order`: `online_first` | `alphabetical_only`.
- `max_contacts`: runtime list cap.
- `cache_ttl_s`: cache freshness window.
- `fetch_timeout_s`: refresh timeout.
- `refresh_retries`: refresh retry count.
- `refresh_backoff_s`: retry backoff base.
- `background_refresh_s`: periodic refresh interval.

Selection/filtering:
- `selected_contacts`: list of IDs/handles/names.
- `selected_only`: show only selected matches.
- `selected_prioritize`: selected first, others after.
- `force_selected_only`: disables safety downgrade.

Protocol/reliability knobs:
- `compat_resend`: optional extra compatibility resend path.
- `compat_page_size`: resend page size.
- `emergency_output_ack`: fast contact ack for timeout-prone paths.

Phone-only privacy/shim:
- `phone_id_shim` (bool): mask numeric IDs on handset views.
- `phone_id_shim_prefix` (string digit): fallback visual prefix.
- `phone_id_shim_value` (string): fixed visible fake number.

Detail-page mapping overrides:
- `detail_overrides`: array of override objects.
  - `match`: `{ id|handle|name }` (single value or list)
  - page 0 fields: `language`, `birthday`, `gender`
  - page 1 fields: `phone_home`, `phone_office`, `phone_mobile`
  - page 2+ fields: `bio`, `city`, `province`, `country`, `timezone`

Diagnostics:
- `diagnostics`: enable verbose contacts diagnostics.
- `diagnostics_sample`: sample size in previews.

## `recording`

- `enabled`: allow runtime call recording.
- `auto_record_calls`: auto start/stop per call lifecycle.
- `directory`: output folder.

## `hid`

- `transport_mode`: `feature_only` | `output_only` | `dual`.
- `contacts_transport_mode`: contacts-specific mode override.
- `contacts_frame_delay_s`: inter-frame delay for contact bursts.
- `contacts_contact_delay_s`: per-record delay for contact bursts.

## `telegram`

- `api_id`, `api_hash`: Telegram app credentials.
- `phone`: account phone (if needed by auth flow).
- `session_name`: Telethon session file stem.

Environment overrides are supported for Telegram creds and may supersede config values.

## `local`

- `call_me_on_start`: trigger test incoming call after startup.
- `call_me_delay_s`: delay before startup incoming call.
- `auto_incoming_after_s`: delayed incoming simulation.
- `echo_gain`: local echo level.
- `contacts`: synthetic contacts array for local backend.

## `tray`

- `debug`: adds `--debug` when tray launches service subprocess.

## `dial_privacy`

- Legacy placeholder section retained for compatibility.
- Runtime behavior currently uses phone-only ID shim in `contacts.*` for privacy masking.
