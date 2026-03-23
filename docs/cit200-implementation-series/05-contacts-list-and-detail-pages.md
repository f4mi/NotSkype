# 05 - Contacts List and Detail Pages

## Contacts List Request Flow

1. Handset opens contacts menu.
2. App enters state 6 and reads requested index from next frame.
3. App sends contacts list payload set.

## Contacts List Response

Start with:

- `83 32 01 43 00 00 00`

For each contact, send 6-block list item (`c6 ... 9a 4c`) with split fields for display name and handle.

## Ordering Strategy

Recommended configurable modes:

- `online_first` (legacy-like)
- `alphabetical_only`

## Detail Pages

Handset may request the same index multiple times with different `more` page values.

- `details` or `more=0`: base profile page
- `more=1`: phone numbers page
- `more>=2`: address/time page

## Base Profile Fields

You can populate:

- handle
- language
- birthday
- gender
- status

## Phone Numbers Page

Encode office/home/mobile as BCD-like byte pairs (AA fill for missing tail).

## Address/Time Page

- text payload for address (or `bio | address`)
- local hour/minute for contact timezone

## Demo Runner

`keepalive_contacts.py` in this repo is a minimal protocol harness that:

- keeps handset alive
- serves static contacts
- serves page-specific detail responses
