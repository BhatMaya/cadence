import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { extractFeatures, type FeatureVector, type Sample } from '../src/index.js';

interface Options {
  in: string;
  out: string;
}

const DEFAULTS: Options = {
  in: 'data/samples.json',
  out: 'data/features.json'
};

function usage(): string {
  return [
    'Usage: tsx scripts/extract-features.ts [flags]',
    '',
    'Flags:',
    '  --in <path>   Input samples JSON path (default data/samples.json)',
    '  --out <path>  Output features JSON path (default data/features.json)'
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
      case '--in':
        options.in = readValue();
        break;
      case '--out':
        options.out = readValue();
        break;
      case '--help':
      case '-h':
        console.log(usage());
        process.exit(0);
      default:
        throw new Error(`Unknown flag: ${raw}\n\n${usage()}`);
    }
  }

  return options;
}

async function main(): Promise<void> {
  const options = parseArgs(process.argv.slice(2));
  const inputPath = path.resolve(process.cwd(), options.in);
  const outputPath = path.resolve(process.cwd(), options.out);
  const raw = await readFile(inputPath, 'utf8');
  const parsed: unknown = JSON.parse(raw);

  if (!Array.isArray(parsed)) {
    throw new Error(`Expected ${options.in} to contain a JSON array of Samples`);
  }

  const samples = parsed as Array<Sample & { meta?: unknown }>;
  const features: FeatureVector[] = samples.map((sample) => extractFeatures(sample));
  const zeroKeystrokeSamples = features.filter(
    (feature) => feature.keystrokes.length === 0
  ).length;

  await mkdir(path.dirname(outputPath), { recursive: true });
  await writeFile(outputPath, `${JSON.stringify(features, null, 2)}\n`, 'utf8');

  console.log('Extracted feature vectors');
  console.log(`  input samples: ${samples.length}`);
  console.log(`  output vectors: ${features.length}`);
  console.log(`  samples with 0 keystrokes: ${zeroKeystrokeSamples}`);
  console.log(`  output: ${options.out}`);
}

main().catch((error: unknown) => {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
});
