# Tel2 for Home Assistant

[![HACS Default](https://img.shields.io/badge/HACS-Default-41BDF5.svg)](https://hacs.xyz)
[![Release](https://img.shields.io/github/v/release/hadavies/homeassistant-tel2?include_prereleases)](https://github.com/hadavies/homeassistant-tel2/releases)
[![CI](https://github.com/hadavies/homeassistant-tel2/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/hadavies/homeassistant-tel2/actions/workflows/ci.yml)
[![Licence](https://img.shields.io/github/license/hadavies/homeassistant-tel2)](LICENSE)

A Home Assistant integration for [Tel2](https://www.tel2.co.uk) VoIP accounts. See your balance and bundle minutes, switch do not disturb, forwarding and call recording per number, and react to calls as they happen.

[![Open the Tel2 repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=hadavies&repository=homeassistant-tel2&category=integration)
[![Add the Tel2 integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=tel2)

This is an unofficial integration. It isn't made, supported or endorsed by Tel2, so please raise problems here rather than with Tel2 support.

## What it does

- Shows your balance, available balance, plan and account status.
- Adds a device for each of your numbers, with its registration status and bundle minutes.
- Lets you switch do not disturb, call forwarding, anonymous call rejection and call recording per number.
- Sets the number calls are forwarded to.
- Resyncs a line or reboots its phone.
- Fires an event for each call as it rings, is answered, ends or is missed, so automations can react within seconds.

Voicemail isn't covered yet. SMS, call history and billing changes are out of scope.

It's been tested on one live account so far, please raise an issue if anything doesn't behave.

## Before you start

- A Tel2 account with at least one number.
- A Tel2 portal login with Full access, see [Setting up a Tel2 login](#setting-up-a-tel2-login).
- Home Assistant 2026.10 or newer.
- For call events, Home Assistant reachable from the internet, through Home Assistant Cloud or an https external URL. Everything else works without it.

## Setting up a Tel2 login

The integration signs in to Tel2 with a portal login. Make one just for Home Assistant rather than using your own, it keeps your main password out of Home Assistant and you can remove it later without touching anything else.

It needs Full access because it reads your balance and changes line settings, and Tel2 doesn't have a read-only level.

1. Sign in to the Tel2 portal with your main login.
2. Go to Accounts > Logins and add a new login.
3. Give it a name you'll recognise, e.g. `homeassistant`, and a strong password.
4. Set the access level to Full and save it.

## Installing

### HACS

It's in the HACS default store, so there's no repository to add. The Open in HACS button above takes you straight to it.

1. Search HACS for Tel2 and open it.
2. Download it.
3. Restart Home Assistant.

### Manually

1. Download `tel2.zip` from the latest [release](https://github.com/hadavies/homeassistant-tel2/releases).
2. Unzip it into `config/custom_components/tel2`, so you end up with `config/custom_components/tel2/manifest.json`.
3. Restart Home Assistant.

## Setting it up

1. Go to Settings > Devices & services > Add integration and pick Tel2, or use the Add integration button above.
2. Enter the portal login and password you made above.

You get an account device named after your Tel2 account, and a device for each number. Number devices are named after the line name in Tel2, or the number itself if the line has no name or shares the account's name.

Numbers added to or removed from your Tel2 account are picked up at the next account refresh, no restart needed.

### Options

Open the integration under Settings > Devices & services and press Configure.

- Line refresh interval is how often line status and settings are checked, from 2 to 60 minutes, 5 by default.
- Account details refresh every 15 minutes whatever the line refresh is set to.
- Call events is on by default, turning it off removes the call event registration from Tel2.

Changes apply straight away, no restart needed.

## What you get

<!-- screenshot: number device page, added before the first stable release -->

| Entity | Device | Type |
| --- | --- | --- |
| Balance | Account | Sensor |
| Available balance | Account | Sensor |
| Plan | Account | Sensor |
| Account status | Account | Sensor |
| API connected | Account | Binary sensor |
| Registered | Number | Binary sensor |
| Bundle minutes used | Number | Sensor |
| Bundle minutes included | Number | Sensor |
| Do not disturb | Number | Switch |
| Forward all calls | Number | Switch |
| Reject anonymous calls | Number | Switch |
| Call recording | Number | Switch |
| Forward destination | Number | Text |
| Resync line | Number | Button |
| Reboot phone | Number | Button |
| Call | Number | Event |

- Balances are in GBP.
- API connected is a diagnostic sensor, it turns off when the last refresh couldn't reach Tel2.
- Forward destination and the two buttons are under Configuration on the device page.
- Registered is on while a phone or app is registered to the number.
- The bundle sensors only appear for numbers with a call bundle, a bundle added later gets them on the next account refresh.
- Settings changed in the Tel2 portal, the app or by star code show up within one line refresh.
- Forward all calls won't turn on until a forward destination is set.
- The forward destination has to start with 0 or +, e.g. `07700 900123` or `+1 202 555 0147`.
- Resync line pushes the current settings to the phone, and both buttons need a phone registered on the line.
- Call is unavailable while call events are off, Tel2 can't reach Home Assistant, or Tel2 hasn't confirmed the registration yet, see [Call events](#call-events).
- If Tel2 can't be reached, the entities go unavailable and come back by themselves once it can. API connected turns off instead, and Call keeps receiving events.

### Call event types

Calls fire `ringing`, `answered`, `ended` and `missed`. Incoming calls can also be `blocked`, e.g. by Reject anonymous calls, so a missed call automation won't see those. Outgoing calls can also end with `busy`, `no_answer`, `rejected`, `invalid` or `not_available`.

Each event carries these attributes:

| Attribute | Holds |
| --- | --- |
| `from` | Calling number |
| `to` | Called number |
| `direction` | `inbound` or `outbound` |
| `call_id` | Tel2 call ID |
| `start_time` | Start time, when Tel2 sends it |
| `duration` | Seconds, when Tel2 sends it |

Numbers are in international format, e.g. `+447700900123`. A withheld caller comes through as whatever Tel2 sends, or `unknown`.

### Set line setting action

`tel2.set_line_setting` sets any Tel2 line setting on a number, for settings that don't have their own entity. It takes the number device, Tel2's name for the setting and the value in the form Tel2 expects. Most on/off settings take `1` or `0`. Only admin users can run it.

```yaml
action: tel2.set_line_setting
data:
  device_id: your_number_device_id
  pref: do_not_disturb_status
  value: "1"
```

The action editor gives you a device picker, which is easier than finding the device ID. PINs and security answers can't be set this way, change those in the Tel2 portal.

## Call events

Tel2 sends call events straight to Home Assistant, so Tel2 has to be able to reach it from the internet. The integration works out the address itself, in this order:

- With Home Assistant Cloud it sets up a cloudhook, there's nothing to configure and no ports to open.
- Without Cloud it uses your external URL from Settings > System > Network, which has to start with https.
- With neither, a repair issue appears saying call events need Home Assistant on the internet, and the Call entities stay unavailable. Everything else still works.

Once Home Assistant is reachable the repair issue clears by itself within about 15 minutes, or reload the integration to pick it up straight away.

The integration registers itself with Tel2 under the name `homeassistant-tel2-` followed by a long ID. You can see it in the webhooks list in the API section of the Tel2 portal. If it goes missing, it's added again at the next account refresh.

NOTE: call events rely on Tel2's webhook API, which isn't answering properly at the moment. Until it does, Call stays unavailable and a warning in the log says the registration couldn't be made. It picks up by itself once Tel2's side is working again, there's nothing to update.

Each event carries a secret only your Home Assistant knows, anything without it is dropped. Events for numbers that aren't on your account are dropped too.

## Examples

Paste these into a new automation using Edit in YAML. The entity IDs are examples, yours will be named after your line, so a line called Line 1 gives `event.line_1_call` and `switch.line_1_do_not_disturb`.

### Missed call notification

Swap `notify.mobile_app_your_phone` for your own phone's notify action.

```yaml
alias: Missed call on Line 1
triggers:
  - trigger: event.received
    target:
      entity_id: event.line_1_call
    options:
      event_type:
        - missed
actions:
  - action: notify.mobile_app_your_phone
    data:
      title: Missed call
      message: "Missed call from {{ trigger.to_state.attributes['from'] }}"
```

### Do not disturb overnight

Turns do not disturb on at 22:00 and off at 07:00.

```yaml
alias: Line 1 do not disturb overnight
triggers:
  - trigger: time
    at: "22:00:00"
    id: night
  - trigger: time
    at: "07:00:00"
    id: morning
actions:
  - choose:
      - conditions:
          - condition: trigger
            id: night
        sequence:
          - action: switch.turn_on
            target:
              entity_id: switch.line_1_do_not_disturb
      - conditions:
          - condition: trigger
            id: morning
        sequence:
          - action: switch.turn_off
            target:
              entity_id: switch.line_1_do_not_disturb
```

## Troubleshooting

### Tel2 didn't accept that login, password or access level

- Check you can sign in to the Tel2 portal with the same login and password.
- Tel2 can give the same answer when the login's access level is too low, so check it's set to Full under Accounts > Logins in the Tel2 portal.
- If it worked before and has stopped, Home Assistant asks you to sign in again under Settings > Devices & services, enter the password there.
- Signing in again has to use a login for the same Tel2 account, a login for a different account is refused.

### This login can't use account, voice or API

The login works but its access level is too low. The message names the parts it can't use. Set the login's access level to Full under Accounts > Logins in the Tel2 portal, then try again.

### Call events aren't arriving

- Check Settings > System > Repairs for "Call events need Home Assistant on the internet".
- Check Call events is turned on in the integration's options.
- Check the log for "Could not register Tel2 call events", that's Tel2's webhook API not answering, see the note under [Call events](#call-events).
- If you use an external URL, check it starts with https and loads from outside your network, e.g. from your phone on mobile data.
- If you're behind a reverse proxy, it needs to let POST requests to `/api/webhook/` through.
- In the Tel2 portal, check the webhooks list in the API section has a registration called `homeassistant-tel2-...`.
- If it's missing, reload the integration and check again.
- Turn on debug logging and make a test call, dropped events are logged with the reason.

### Debug logging

The quickest way is from the integration page under Settings > Devices & services. Press Enable debug logging, reproduce the problem, then press Disable debug logging and the log downloads.

To keep it on across restarts, add this to `configuration.yaml` and restart:

```yaml
logger:
  default: warning
  logs:
    custom_components.tel2: debug
```

### Diagnostics

Open the integration under Settings > Devices & services, then Download diagnostics from the three-dot menu.

Passwords, tokens, the portal login, your account code, the call event address and its secret are removed. Your own numbers are replaced with `line_1`, `line_2` and so on, and names and other people's numbers are redacted. Read the file before you attach it to an issue anyway, issues here are public.

Bugs go in the [issue tracker](https://github.com/hadavies/homeassistant-tel2/issues), the bug form asks for diagnostics and a debug log.

## Removing it

1. Go to Settings > Devices & services, open Tel2 and pick Delete from the three-dot menu.
2. Remove it in HACS, or delete `config/custom_components/tel2` if you installed it manually.
3. Restart Home Assistant.

Deleting the integration removes its call event registration from Tel2. If Tel2 can't be reached at that point, a warning in the log says the registration may still be there, delete it from the webhooks list in the portal.

Line settings stay as they are on Tel2, so if do not disturb was on it stays on. The portal login isn't touched either, delete it under Accounts > Logins if you don't need it.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setting up, running the checks and how releases work.

## Licence

MIT, see [LICENSE](LICENSE).
