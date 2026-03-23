# 10 - Reference Pseudocode

This is a single, language-agnostic skeleton for implementing the full CIT200 stack.

```text
DATA TYPES
----------
Contact:
  index: int
  handle: string
  name: string
  status: int

PlatformContact:
  id: string
  handle: string
  name: string
  status: int


INTERFACES
----------
interface VoicePlatform:
  connect()
  disconnect()
  place_call(target)
  answer_call(call_id)
  end_call()
  hold_call()
  send_audio(pcm_chunk)
  get_contacts() -> list<PlatformContact>
  get_status() -> string
  set_status(status_string)

  on_incoming_call(callback(call_id, caller_name))
  on_call_answered(callback())
  on_call_ended(callback())
  on_audio_received(callback(pcm_chunk))


CIT200 TRANSPORT LAYER
----------------------
class Cit200Transport:
  state = 0
  current_status = ONLINE
  callbacks = map<event_name, list<fn>>
  callee_buffer = []
  transport_mode = dual

  open_hid(vid, pid)
  close_hid()

  on(event, callback):
    callbacks[event].append(callback)

  emit(event, args...):
    for cb in callbacks[event]: cb(args...)

  write7(payload[7]):
    report = [0x04] + payload + [0x68]
    if mode in {feature_only, dual}: send_feature_report(report)
    if mode in {output_only, dual}: send_output_report(report)

  read8() -> bytes_or_none

  send_init(status, hh, mm):
    write7([0xc1,0x33,0x00,0x43,0x07,0x9a,0x4f])
    write7([0x05,hh,mm,0x06,status,0x02,0x00])

  setup_call_from_handset_q9():
    write7([0x82,0x22,0x11,0x00,0x00,0x00,0x00])
    write7([0x85,0x33,0x11,0x68,0x01,0x01,0x00])
    write7([0x83,0x33,0x11,0x67,0x00,0x00,0x00])
    write7([0x82,0x43,0x11,0x00,0x00,0x00,0x00])

  confirm_call_initiated_q10():
    write7([0xc1,0x33,0xff,0x43,0x04,0x9a,0x51])
    write7([0x02,0x01,0x00,0x00,0x00,0x00,0x00])

  confirm_connected_q17():
    write7([0x83,0x32,0xff,0x43,0x00,0x00,0x00])

  confirm_dial_nudge():
    write7([0x83,0x32,0x11,0x35,0x00,0x00,0x00])

  ring_incoming():
    write7([0x84,0x23,0x00,0x01,0x80,0x00,0x00])

  end_from_remote():
    write7([0x84,0x53,0x11,0x01,0x00,0x00,0x00])

  answer_incoming_frames():
    write7([0x85,0x33,0x11,0x68,0x01,0x01,0x00])
    write7([0x82,0x43,0x11,0x00,0x00,0x00,0x00])

  send_contacts(contact_page):
    write7([0x83,0x32,0x01,0x43,0x00,0x00,0x00])
    if empty(contact_page):
      write7([0xc1,0x33,0x01,0x43,0x06,0x9a,0x4c])
      write7([0x04,0x00,0x00,0x00,0x00,0x00,0x00])
      return
    for each contact in contact_page:
      # c6 contact block frames (see frame catalog)
      emit_contact_item_frames(contact)

  send_contact_details_base(contact, language, birthday, gender):
    # c9 details page frames
    emit_details_base_frames(contact, language, birthday, gender)

  send_contact_details_phones(office, home, mobile):
    # c6 details page for phone numbers
    emit_details_phone_frames(office, home, mobile)

  send_contact_details_address(address_text, hh, mm):
    # c8 details page for address/time
    emit_details_address_frames(address_text, hh, mm)

  process_one_frame(frame):
    d = frame

    # end call
    if match(d, "84 51 11 01"):
      emit(END_CALL)
      state = 0
      return

    # ping
    if ping_family(d):
      emit(PING)
      return

    # stateful branches
    if state == 6:
      emit(CONTACTS_REQUEST, d[3])
      state = 0
      return

    if state == 8:
      idx = d[3]
      more = d[4]
      emit(CONTACT_DETAILS, idx, ("details" if more==0 else more))
      state = 0
      return

    if state == 5:
      new_status = d[2]
      emit(STATUS_CHANGE, new_status)
      state = 0
      return

    if state == 18:
      parse_multi_frame_callee(d)
      if finished:
        emit(DIAL, parsed_handle)
        state = 0
      return

    # stateless families
    if match(d, "c1 31 01 43 05 9a 4c"):
      state = 6
      return

    if match(d, "c1 31 01 43 05 9a 4d"):
      state = 8
      return

    if match(d, "c1 31 01 43 03 9a 43"):
      state = 5
      return

    if match(d, "c1 21 11 04 80 9a 60"):
      emit(CALL_BUTTON)
      state = 0
      return

    if dial_start_family(d):
      seed_callee_buffer(d)
      state = 18
      return

    if match_prefix(d, "82 44 11"):
      confirm_connected_q17()
      emit(CALL_ACCEPTED_REMOTE)
      state = 0
      return

    if match_prefix(d, "82 24 11"):
      answer_incoming_frames()
      emit(ANSWER_INCOMING)
      state = 0
      return

    if match_prefix(d, "86 31 11 43 02 9a"):
      emit(REJECT_INCOMING)
      state = 0
      return

    if match_prefix(d, "85 31 11 35 01 15"):
      emit(HOLD_RESUME)
      state = 0
      return


ORCHESTRATOR
------------
class PhoneApp:
  phone: Cit200Transport
  platform: VoicePlatform
  audio: AudioBridge
  loop: AsyncLoop
  contacts_cache: list<PlatformContact>
  visible_contacts: list<PlatformContact>
  current_call_id: string

  wire_events():
    phone.on(CALL_BUTTON, on_phone_call_button)
    phone.on(DIAL, on_phone_dial)
    phone.on(END_CALL, on_phone_end)
    phone.on(CONTACTS_REQUEST, on_phone_contacts)
    phone.on(CONTACT_DETAILS, on_phone_contact_details)
    phone.on(STATUS_CHANGE, on_phone_status_change)
    phone.on(ANSWER_INCOMING, on_phone_answer)
    phone.on(REJECT_INCOMING, on_phone_reject)
    phone.on(HOLD_RESUME, on_phone_hold)

    platform.on_incoming_call(on_platform_incoming)
    platform.on_call_answered(on_platform_answered)
    platform.on_call_ended(on_platform_ended)
    platform.on_audio_received(on_platform_audio)

    audio.on_capture = on_mic_audio

  on_phone_call_button():
    # no-op or answer intent; real dial comes via DIAL event
    pass

  on_phone_dial(handle):
    audio.start_if_needed()
    async_spawn(platform.place_call(handle))

  on_phone_end():
    audio.stop_if_running()
    async_spawn(platform.end_call())

  on_phone_answer():
    audio.start_if_needed()
    async_spawn(platform.answer_call(current_call_id))

  on_phone_reject():
    async_spawn(platform.end_call())

  on_phone_hold():
    async_spawn(platform.hold_call())

  on_phone_status_change(status_index):
    async_spawn(platform.set_status(map_status(status_index)))

  on_phone_contacts(start_index):
    async_spawn(fetch_and_send_contacts(start_index))

  fetch_and_send_contacts(start_index):
    contacts_cache = await platform.get_contacts()
    ordered = sort_contacts(contacts_cache, mode=config.contacts.order)
    visible_contacts = paginate_wrap(ordered, start=start_index, max=33)
    phone.send_contacts(convert_to_phone_contacts(visible_contacts))

  on_phone_contact_details(idx, more):
    c = visible_contacts[idx]
    if more == "details" or more == 0:
      phone.send_contact_details_base(...)
    else if more == 1:
      phone.send_contact_details_phones(...)
    else:
      phone.send_contact_details_address(...)

  on_platform_incoming(call_id, caller_name):
    current_call_id = call_id
    phone.ring_incoming()
    phone.send_caller_id(caller_name)

  on_platform_answered():
    audio.start_if_needed()
    phone.confirm_connected_q17()

  on_platform_ended():
    audio.stop_if_running()
    phone.end_from_remote()

  on_platform_audio(pcm):
    audio.play(pcm)

  on_mic_audio(pcm):
    # IMPORTANT: callback thread safe handoff
    if platform.in_call and loop.is_running:
      run_coroutine_threadsafe(platform.send_audio(pcm), loop)

  run_main_loop():
    open phone
    connect platform
    while running:
      phone.process_one_frame_if_available()
      every_8_ticks: phone.send_init(current_status)
      sleep(0.2)
    shutdown audio/platform/phone


TEST HARNESS MODES
------------------
Mode A: full app with platform backend
Mode B: keepalive + contacts-only protocol harness
Mode C: local echo backend for deterministic bring-up


OBSERVABILITY
-------------
Always log:
  RX hex, TX payloads, state transitions, q9/q10/q17 timeline markers,
  emitted semantic events, and trace file with timestamps.
```
