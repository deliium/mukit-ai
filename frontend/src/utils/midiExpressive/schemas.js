/**
 * JSDoc document shapes for expressive MIDI (no runtime Zod).
 * Backend Pydantic for note_performances lands in Task 5.
 */

/**
 * @typedef {'available' | 'unsupported' | 'insecure_context' | 'permission_denied'} MidiWebMidiStatus
 *
 * @typedef {'midi1_bytes' | 'ump_experimental' | 'none'} MidiTransportId
 *
 * @typedef {'default' | 'user' | 'device_hint'} MidiMpeZoneSource
 *
 * @typedef {{
 *   master_channel: number,
 *   member_channel_low: number,
 *   member_channel_high: number,
 * }} MidiMpeZone
 *
 * @typedef {{
 *   schema?: 'midi.capability.v1',
 *   web_midi: MidiWebMidiStatus,
 *   transport: MidiTransportId,
 *   mpe: {
 *     eligible: boolean,
 *     zone?: MidiMpeZone,
 *     source: MidiMpeZoneSource,
 *   },
 *   high_res_velocity: boolean,
 *   reason_codes: string[],
 * }} MidiCapabilityV1
 *
 * @typedef {{
 *   kind: 'note_on' | 'note_off',
 *   note: number,
 *   velocity_u16: number,
 *   channel: number,
 *   group?: number,
 *   at_ms?: number,
 * }} MidiExpressiveNoteEvent
 *
 * @typedef {{
 *   kind: 'pitch_bend',
 *   bend: number,
 *   channel: number,
 *   note?: number,
 *   at_ms?: number,
 * }} MidiExpressivePitchBendEvent
 *
 * @typedef {{
 *   kind: 'pressure',
 *   scope: 'poly' | 'channel',
 *   value: number,
 *   channel: number,
 *   note?: number,
 *   at_ms?: number,
 * }} MidiExpressivePressureEvent
 *
 * @typedef {{
 *   kind: 'control_change',
 *   controller: number,
 *   value_u32: number,
 *   channel: number,
 *   sustain?: boolean,
 *   at_ms?: number,
 * }} MidiExpressiveControlChangeEvent
 *
 * @typedef {{
 *   kind: 'ignored',
 *   reason?: string,
 *   status?: number,
 *   at_ms?: number,
 * }} MidiExpressiveIgnoredEvent
 *
 * @typedef {
 *   | MidiExpressiveNoteEvent
 *   | MidiExpressivePitchBendEvent
 *   | MidiExpressivePressureEvent
 *   | MidiExpressiveControlChangeEvent
 *   | MidiExpressiveIgnoredEvent
 * } MidiExpressiveEventV1
 *
 * @typedef {{
 *   tick_offset: number,
 *   cents: number,
 * }} MidiPitchCentsPoint
 *
 * @typedef {{
 *   tick_offset: number,
 *   value: number,
 * }} MidiPressurePoint
 *
 * @typedef {{
 *   tick_offset: number,
 *   controller: number,
 *   value: number,
 * }} MidiControllerPoint
 *
 * @typedef {{
 *   pitch: string,
 *   midi: number,
 *   channel: number,
 *   start_tick: number,
 *   duration_ticks: number,
 *   velocity: number,
 *   velocity_u16?: number,
 *   pitch_cents?: MidiPitchCentsPoint[],
 *   pressure?: MidiPressurePoint[],
 *   controllers?: MidiControllerPoint[],
 * }} MidiPerformanceTakeNote
 *
 * @typedef {{
 *   schema?: 'midi.performance.take.v1',
 *   notes: MidiPerformanceTakeNote[],
 *   sustain_pedals: Array<{ start_tick: number, duration_ticks: number }>,
 *   ignored_count: number,
 *   performance_note_count: number,
 * }} MidiPerformanceTakeV1
 *
 * @typedef {{
 *   event_id: string,
 *   velocity_u16?: number,
 *   pitch_cents?: MidiPitchCentsPoint[],
 *   pressure?: MidiPressurePoint[],
 *   controllers?: MidiControllerPoint[],
 * }} NotePerformanceV1
 */

export {};
