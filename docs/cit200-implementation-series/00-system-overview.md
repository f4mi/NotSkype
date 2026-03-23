# 00 - System Overview

## Goal

Build a desktop service that turns the Linksys CIT200 handset into a usable VoIP terminal.

## High-Level Architecture

- `HID protocol layer` (CIT200 control channel)
- `Audio bridge layer` (USB audio capture/playback)
- `Platform layer` (local demo backend, SIP, Discord, etc.)
- `Orchestrator` (maps handset events to backend actions)

## Data Flow

1. Handset emits HID event frame (button/menu/call actions)
2. Protocol layer decodes frame and emits a semantic event
3. Orchestrator routes event to platform/audio actions
4. Platform events (incoming call, answered, ended) are converted back to HID command frames
5. Audio bridge streams mic to backend and backend audio to handset speaker

## Key Behavioral Constraints

- Handset state is firmware-driven; desktop app must acknowledge transitions with correct frame families.
- Keepalive/time sync must be sent periodically (not once).
- Call flows are sequence-sensitive (setup before initiated before connected).
- Audio callbacks can run on non-main threads; async handoff must be thread-safe.

## Minimum Viable Feature Set

- Keepalive/init
- Contacts list request and response
- Contact detail pages
- Outgoing call flow
- Incoming answer/reject flow
- Hangup flow
- Hold/resume event handling
- Audio loopback or backend media routing
