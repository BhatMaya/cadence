import { describe, expect, it } from 'vitest';
import { extractFeatures } from '../src/features.js';
import type { Sample, SampleKeyEvent } from '../src/types.js';

function sample(events: readonly SampleKeyEvent[]): Sample {
  return {
    session_id: 'session-1',
    field_id: 'password',
    session_start_ms: 1_700_000_000_000,
    events,
    flags: [],
    untrusted_events: 0,
    env: {
      user_agent: 'test',
      timing_resolution_ms: 0.1
    },
    quality_score: 1,
    library_version: '0.1.0'
  };
}

describe('extractFeatures', () => {
  it('extracts hold and transition timings for a normal sample', () => {
    const features = extractFeatures(
      sample([
        { type: 'down', code: 'KeyA', t: 0 },
        { type: 'up', code: 'KeyA', t: 50 },
        { type: 'down', code: 'KeyB', t: 120 },
        { type: 'up', code: 'KeyB', t: 190 },
        { type: 'down', code: 'KeyC', t: 250 },
        { type: 'up', code: 'KeyC', t: 310 },
        { type: 'down', code: 'KeyD', t: 390 },
        { type: 'up', code: 'KeyD', t: 470 },
        { type: 'down', code: 'KeyE', t: 560 },
        { type: 'up', code: 'KeyE', t: 650 }
      ])
    );

    expect(features.keystrokes).toEqual([
      {
        code: 'KeyA',
        hold_time: 50,
        flight_time: null,
        down_down: null,
        up_up: null
      },
      {
        code: 'KeyB',
        hold_time: 70,
        flight_time: 70,
        down_down: 120,
        up_up: 140
      },
      {
        code: 'KeyC',
        hold_time: 60,
        flight_time: 60,
        down_down: 130,
        up_up: 120
      },
      {
        code: 'KeyD',
        hold_time: 80,
        flight_time: 80,
        down_down: 140,
        up_up: 160
      },
      {
        code: 'KeyE',
        hold_time: 90,
        flight_time: 90,
        down_down: 170,
        up_up: 180
      }
    ]);
  });

  it('drops orphan keyups', () => {
    const features = extractFeatures(
      sample([
        { type: 'up', code: 'KeyA', t: 10 },
        { type: 'down', code: 'KeyB', t: 20 },
        { type: 'up', code: 'KeyB', t: 80 }
      ])
    );

    expect(features.keystrokes.map((key) => key.code)).toEqual(['KeyB']);
  });

  it('drops unmatched keydowns', () => {
    const features = extractFeatures(
      sample([
        { type: 'down', code: 'KeyA', t: 0 },
        { type: 'down', code: 'KeyB', t: 20 },
        { type: 'up', code: 'KeyB', t: 80 }
      ])
    );

    expect(features.keystrokes.map((key) => key.code)).toEqual(['KeyB']);
  });

  it('sets first keystroke transition timings to null', () => {
    const features = extractFeatures(
      sample([
        { type: 'down', code: 'KeyA', t: 0 },
        { type: 'up', code: 'KeyA', t: 50 }
      ])
    );

    expect(features.keystrokes[0]).toMatchObject({
      flight_time: null,
      down_down: null,
      up_up: null
    });
  });

  it('preserves negative flight times', () => {
    const features = extractFeatures(
      sample([
        { type: 'down', code: 'KeyA', t: 0 },
        { type: 'down', code: 'KeyB', t: 40 },
        { type: 'up', code: 'KeyA', t: 100 },
        { type: 'up', code: 'KeyB', t: 130 }
      ])
    );

    expect(features.keystrokes[1]!.flight_time).toBe(-60);
  });

  it('returns a valid zeroed FeatureVector for an empty sample', () => {
    const features = extractFeatures(sample([]));

    expect(features.keystrokes).toEqual([]);
    expect(features.aggregates).toEqual({
      mean_hold: 0,
      std_hold: 0,
      mean_flight: 0,
      std_flight: 0,
      mean_down_down: 0,
      std_down_down: 0,
      total_duration: 0,
      typing_speed: 0,
      keystroke_count: 0
    });
    expect(features.meta.password_length).toBe(0);
  });

  it('pairs interleaved holds and orders output by keydown time', () => {
    const features = extractFeatures(
      sample([
        { type: 'down', code: 'KeyA', t: 0 },
        { type: 'down', code: 'KeyB', t: 20 },
        { type: 'up', code: 'KeyA', t: 80 },
        { type: 'up', code: 'KeyB', t: 130 }
      ])
    );

    expect(features.keystrokes).toMatchObject([
      { code: 'KeyA', hold_time: 80 },
      { code: 'KeyB', hold_time: 110 }
    ]);
  });

  it('computes aggregates for a known input', () => {
    const features = extractFeatures(
      sample([
        { type: 'down', code: 'KeyA', t: 0 },
        { type: 'up', code: 'KeyA', t: 50 },
        { type: 'down', code: 'KeyB', t: 100 },
        { type: 'up', code: 'KeyB', t: 170 },
        { type: 'down', code: 'KeyC', t: 250 },
        { type: 'up', code: 'KeyC', t: 340 }
      ])
    );

    expect(features.aggregates.mean_hold).toBe(70);
    expect(features.aggregates.std_hold).toBeCloseTo(16.3299, 4);
    expect(features.aggregates.mean_flight).toBe(65);
    expect(features.aggregates.std_flight).toBe(15);
    expect(features.aggregates.mean_down_down).toBe(125);
    expect(features.aggregates.std_down_down).toBe(25);
    expect(features.aggregates.total_duration).toBe(340);
    expect(features.aggregates.typing_speed).toBeCloseTo(8.8235, 4);
    expect(features.aggregates.keystroke_count).toBe(3);
  });
});
