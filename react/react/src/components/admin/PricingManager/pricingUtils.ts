import type { PricingEntry, PivotedModel } from './types';

// ============================================
// Unit constants + labels
// ============================================

export const TEXT_UNITS = [
  'input_tokens_1m',
  'output_tokens_1m',
  'cached_input_tokens_1m',
  'reasoning_tokens_1m',
] as const;

export const IMAGE_UNITS = [
  'image_512x512',
  'image_1024x1024',
  'image_1024x1792',
  'image_1792x1024',
  'image_512x512_hd',
  'image_1024x1024_hd',
  'image_1024x1792_hd',
  'image_1792x1024_hd',
] as const;

export type TextUnit = (typeof TEXT_UNITS)[number];
export type ImageUnit = (typeof IMAGE_UNITS)[number];

export const TEXT_UNIT_LABELS: Record<TextUnit, string> = {
  input_tokens_1m: 'Input/1M',
  output_tokens_1m: 'Output/1M',
  cached_input_tokens_1m: 'Cached/1M',
  reasoning_tokens_1m: 'Reasoning/1M',
};

export const IMAGE_UNIT_LABELS: Record<ImageUnit, string> = {
  image_512x512: '512x512',
  image_1024x1024: '1024x1024',
  image_1024x1792: '1024x1792',
  image_1792x1024: '1792x1024',
  image_512x512_hd: '512 HD',
  image_1024x1024_hd: '1024 HD',
  image_1024x1792_hd: '1024x1792 HD',
  image_1792x1024_hd: '1792x1024 HD',
};

// ============================================
// Helpers
// ============================================

export function isTextUnit(unit: string): unit is TextUnit {
  return (TEXT_UNITS as readonly string[]).includes(unit);
}

export function isImageUnit(unit: string): unit is ImageUnit {
  return (IMAGE_UNITS as readonly string[]).includes(unit);
}

export function pivotPricingData(entries: PricingEntry[]): {
  textModels: PivotedModel[];
  imageModels: PivotedModel[];
} {
  const textMap = new Map<string, PivotedModel>();
  const imageMap = new Map<string, PivotedModel>();

  for (const entry of entries) {
    const key = `${entry.provider}::${entry.model}`;
    const isText = isTextUnit(entry.unit);
    const isImage = isImageUnit(entry.unit);

    if (!isText && !isImage) continue;

    const map = isText ? textMap : imageMap;

    if (!map.has(key)) {
      map.set(key, {
        provider: entry.provider,
        model: entry.model,
        costs: {},
        originalEntries: {},
      });
    }

    const pivoted = map.get(key)!;
    pivoted.costs[entry.unit] = entry.cost;
    pivoted.originalEntries[entry.unit] = entry;
  }

  return {
    textModels: Array.from(textMap.values()),
    imageModels: Array.from(imageMap.values()),
  };
}

export function formatCostValue(cost: number | null | undefined): string {
  if (cost === null || cost === undefined) return '-';
  return `$${cost.toFixed(2)}`;
}

export function getTodayISO(): string {
  return new Date().toISOString().split('T')[0];
}
