import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import type { Sample, SampleKeyEvent } from '../src/types.js';

interface Options {
  users: number;
  samplesPerUser: number;
  password: string;
  out: string;
  seed: number;
  impostorFraction: number;
}

interface UserProfile {
  userId: string;
  holdMs: number;
  flightMs: number;
  noiseStdMs: number;
}

interface SyntheticMeta {
  user_id: string;
  sample_index: number;
  impostor_of?: string;
}

// Synthetic datasets extend the production Sample schema with meta so training
// and evaluation code can associate captures with generated identities.
type SyntheticSample = Sample & { meta: SyntheticMeta };

const FLAGS = [
  'paste_detected',
  'focus_lost',
  'inter_key_pause_exceeded',
  'ime_active',
  'autofill_suspected'
] as const;

const DEFAULTS: Options = {
  users: 60,
  samplesPerUser: 30,
  password: 'password123',
  out: 'data/samples.json',
  seed: 42,
  impostorFraction: 0
};

function usage(): string {
  return [
    'Usage: tsx scripts/generate-samples.ts [flags]',
    '',
    'Flags:',
    '  --users <n>                 Number of synthetic users (default 60)',
    '  --samples-per-user <n>      Samples per user (default 30)',
    '  --password <text>           Text each user types (default "password123")',
    '  --out <path>                Output JSON path (default data/samples.json)',
    '  --seed <n>                  RNG seed for reproducibility (default 42)',
    '  --impostor-fraction <n>     Fraction of impostor attempts (default 0)'
  ].join('\n');
}

function parseArgs(argv: readonly string[]): Options {
  const options = { ...DEFAULTS };

  for (let i = 0; i < argv.length; i++) {
    const raw = argv[i]!;
    const equalsIndex = raw.indexOf('=');
    const flag = equalsIndex === -1 ? raw : raw.slice(0, equalsIndex);
    const inlineValue = equalsIndex === -1 ? undefined : raw.slice(equalsIndex + 1);
    const readValue = (): string => {
      if (inlineValue !== undefined) return inlineValue;
      const value = argv[++i];
      if (value === undefined) {
        throw new Error(`Missing value for ${flag}`);
      }
      return value;
    };

    switch (flag) {
      case '--users':
        options.users = parseInteger(readValue(), flag);
        break;
      case '--samples-per-user':
        options.samplesPerUser = parseInteger(readValue(), flag);
        break;
      case '--password':
        options.password = readValue();
        break;
      case '--out':
        options.out = readValue();
        break;
      case '--seed':
        options.seed = parseInteger(readValue(), flag);
        break;
      case '--impostor-fraction':
        options.impostorFraction = parseNumber(readValue(), flag);
        break;
      case '--help':
      case '-h':
        console.log(usage());
        process.exit(0);
      default:
        throw new Error(`Unknown flag: ${raw}\n\n${usage()}`);
    }
  }

  if (options.users < 1) throw new Error('--users must be at least 1');
  if (options.samplesPerUser < 1) {
    throw new Error('--samples-per-user must be at least 1');
  }
  if (options.impostorFraction < 0 || options.impostorFraction > 1) {
    throw new Error('--impostor-fraction must be between 0 and 1');
  }

  return options;
}

function parseInteger(value: string, flag: string): number {
  const parsed = Number(value);
  if (!Number.isInteger(parsed)) throw new Error(`${flag} must be an integer`);
  return parsed;
}

function parseNumber(value: string, flag: string): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) throw new Error(`${flag} must be a number`);
  return parsed;
}

function mulberry32(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state += 0x6d2b79f5;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function normal(rng: () => number, mean: number, std: number): number {
  const u1 = Math.max(rng(), Number.EPSILON);
  const u2 = rng();
  const z0 = Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
  return mean + z0 * std;
}

function clip(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function randomInt(rng: () => number, minInclusive: number, maxInclusive: number): number {
  return Math.floor(rng() * (maxInclusive - minInclusive + 1)) + minInclusive;
}

function randomChoice<T>(rng: () => number, values: readonly T[]): T {
  return values[Math.floor(rng() * values.length)]!;
}

function randomUuidLike(rng: () => number): string {
  const hex = (length: number): string =>
    Array.from({ length }, () => Math.floor(rng() * 16).toString(16)).join('');
  return `${hex(8)}-${hex(4)}-${hex(4)}-${hex(4)}-${hex(12)}`;
}

function roundTiming(value: number): number {
  return Math.round(value * 10) / 10;
}

function buildProfiles(options: Options, rng: () => number): UserProfile[] {
  return Array.from({ length: options.users }, (_, index) => ({
    userId: `user_${String(index + 1).padStart(3, '0')}`,
    holdMs: clip(normal(rng, 80, 20), 30, 200),
    flightMs: clip(normal(rng, 100, 40), -50, 400),
    noiseStdMs: clip(normal(rng, 15, 5), 5, 40)
  }));
}

function codesForPassword(password: string): string[] {
  return Array.from(password).flatMap(codeForCharacter);
}

function codeForCharacter(character: string): string[] {
  if (character === ' ') return ['Space'];
  if (/^[a-z]$/.test(character)) return [`Key${character.toUpperCase()}`];
  if (/^[A-Z]$/.test(character)) return ['ShiftLeft', `Key${character}`];
  if (/^[0-9]$/.test(character)) return [`Digit${character}`];

  const unshifted: Record<string, string> = {
    '-': 'Minus',
    '=': 'Equal',
    '[': 'BracketLeft',
    ']': 'BracketRight',
    '\\': 'Backslash',
    ';': 'Semicolon',
    "'": 'Quote',
    '`': 'Backquote',
    ',': 'Comma',
    '.': 'Period',
    '/': 'Slash'
  };
  const shifted: Record<string, string> = {
    '!': 'Digit1',
    '@': 'Digit2',
    '#': 'Digit3',
    '$': 'Digit4',
    '%': 'Digit5',
    '^': 'Digit6',
    '&': 'Digit7',
    '*': 'Digit8',
    '(': 'Digit9',
    ')': 'Digit0',
    '_': 'Minus',
    '+': 'Equal',
    '{': 'BracketLeft',
    '}': 'BracketRight',
    '|': 'Backslash',
    ':': 'Semicolon',
    '"': 'Quote',
    '~': 'Backquote',
    '<': 'Comma',
    '>': 'Period',
    '?': 'Slash'
  };

  if (character in unshifted) return [unshifted[character]!];
  if (character in shifted) return ['ShiftLeft', shifted[character]!];

  const codePoint = character.codePointAt(0)?.toString(16).toUpperCase() ?? 'UNKNOWN';
  return [`Intl${codePoint}`];
}

function generateEvents(
  codes: readonly string[],
  profile: UserProfile,
  rng: () => number
): SampleKeyEvent[] {
  const offDayMultiplier = rng() < 0.1 ? 1.5 : 1;
  const noiseStd = profile.noiseStdMs * offDayMultiplier;
  const rawEvents: SampleKeyEvent[] = [];
  let nextDown = 0;

  for (let index = 0; index < codes.length; index++) {
    const code = codes[index]!;
    const down = nextDown;
    const hold = clip(normal(rng, profile.holdMs, noiseStd), 10, 300);
    const up = down + hold;

    rawEvents.push(
      { type: 'down', code, t: roundTiming(down) },
      { type: 'up', code, t: roundTiming(up) }
    );

    if (index < codes.length - 1) {
      const flight = normal(rng, profile.flightMs, noiseStd);
      nextDown = Math.max(down + 1, up + flight);
    }
  }

  return rawEvents.sort((a, b) => a.t - b.t || eventOrder(a.type) - eventOrder(b.type));
}

function eventOrder(type: SampleKeyEvent['type']): number {
  return type === 'down' ? 0 : 1;
}

function chooseImpostorProfile(
  claimedIndex: number,
  profiles: readonly UserProfile[],
  rng: () => number
): UserProfile {
  if (profiles.length < 2) return profiles[claimedIndex]!;

  let impostorIndex = randomInt(rng, 0, profiles.length - 2);
  if (impostorIndex >= claimedIndex) impostorIndex += 1;
  return profiles[impostorIndex]!;
}

async function main(): Promise<void> {
  const options = parseArgs(process.argv.slice(2));
  const rng = mulberry32(options.seed);
  const profiles = buildProfiles(options, rng);
  const codes = codesForPassword(options.password);
  const samples: SyntheticSample[] = [];
  const baseWallClock = Date.UTC(2026, 0, 12, 9, 0, 0);
  let impostorCount = 0;

  for (let userIndex = 0; userIndex < profiles.length; userIndex++) {
    const claimedProfile = profiles[userIndex]!;

    for (let sampleIndex = 0; sampleIndex < options.samplesPerUser; sampleIndex++) {
      const useImpostor =
        profiles.length > 1 && rng() < options.impostorFraction;
      const typingProfile = useImpostor
        ? chooseImpostorProfile(userIndex, profiles, rng)
        : claimedProfile;
      if (useImpostor) impostorCount++;

      const flags = rng() < 0.05 ? [randomChoice(rng, FLAGS)] : [];
      const sampleOrdinal = userIndex * options.samplesPerUser + sampleIndex;
      const sessionStartMs =
        baseWallClock + sampleOrdinal * 90_000 + randomInt(rng, 0, 20_000);
      const untrustedEvents = rng() < 0.03 ? randomInt(rng, 1, 2) : 0;
      const meta: SyntheticMeta = {
        user_id: typingProfile.userId,
        sample_index: sampleIndex
      };
      if (useImpostor) meta.impostor_of = claimedProfile.userId;

      samples.push({
        session_id: randomUuidLike(rng),
        field_id: 'password',
        session_start_ms: sessionStartMs,
        events: generateEvents(codes, typingProfile, rng),
        flags,
        untrusted_events: untrustedEvents,
        env: {
          user_agent: 'Mozilla/5.0 (synthetic)',
          timing_resolution_ms: 0.1
        },
        quality_score: flags.length === 0 ? 1.0 : 0.7,
        library_version: '0.1.0',
        meta
      });
    }
  }

  const outputPath = path.resolve(process.cwd(), options.out);
  await mkdir(path.dirname(outputPath), { recursive: true });
  await writeFile(outputPath, `${JSON.stringify(samples, null, 2)}\n`, 'utf8');

  console.log('Generated synthetic samples');
  console.log(`  total samples: ${samples.length}`);
  console.log(`  users: ${options.users}`);
  console.log(`  samples per user: ${options.samplesPerUser}`);
  console.log(`  impostor fraction: ${options.impostorFraction}`);
  console.log(`  impostor samples: ${impostorCount}`);
  console.log(`  output: ${options.out}`);
}

main().catch((error: unknown) => {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
});
