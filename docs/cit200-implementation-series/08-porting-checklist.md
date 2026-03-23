# 08 - Porting Checklist

Use this checklist when implementing in another language.

## Phase 1 - Transport

- [ ] Open HID by VID/PID
- [ ] Non-blocking read loop works
- [ ] 7-byte payload writer wraps as `04 + payload + 68`
- [ ] Feature/output/dual mode toggle works
- [ ] Keepalive sent periodically

## Phase 2 - Parser

- [ ] Idle parser decodes core events
- [ ] State 6 contacts index parsing works
- [ ] State 8 detail parsing works
- [ ] State 18 dial assembly works
- [ ] Semantic event callbacks fire correctly

## Phase 3 - Call Control

- [ ] q9 setup sequence implemented
- [ ] q10 initiated sequence implemented
- [ ] q17 connected confirm implemented
- [ ] incoming answer/reject/hangup flows implemented
- [ ] hold/resume event mapped

## Phase 4 - Audio

- [ ] Capture + playback open on correct USB audio device
- [ ] Thread-safe async handoff for callback audio
- [ ] Start/stop lifecycle bound to call states

## Phase 5 - Contacts

- [ ] list page payloads render on handset
- [ ] details page payloads render
- [ ] phone numbers page works
- [ ] address/time page works
- [ ] gender/birthday/language fields populate

## Phase 6 - Validation

- [ ] manual Y/N script run complete
- [ ] trace confirms expected q9->q10->q17 timing
- [ ] dial tone behavior acceptable
- [ ] second cycle remains stable
