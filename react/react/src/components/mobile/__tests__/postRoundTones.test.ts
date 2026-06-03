import { describe, it, expect } from 'vitest';
import { buildToneOptions } from '../postRoundTones';

const ids = (opts: Parameters<typeof buildToneOptions>[0]) =>
  buildToneOptions(opts).map((t) => t.id);

describe('buildToneOptions — situational post-round tones', () => {
  it('won at showdown: gloat/humble/gracious + props', () => {
    expect(
      ids({ playerWon: true, isShowdown: true, humanAtShowdown: true, hasFellowLoser: true })
    ).toEqual(['gloat', 'humble', 'gracious', 'props']);
  });

  it('won uncontested: no props (no hand was shown)', () => {
    expect(
      ids({ playerWon: true, isShowdown: false, humanAtShowdown: false, hasFellowLoser: false })
    ).toEqual(['gloat', 'humble', 'gracious']);
  });

  it('folded / watched: salty + props, no cry_luck/vow (no beat to avenge)', () => {
    const out = ids({
      playerWon: false,
      isShowdown: true,
      humanAtShowdown: false,
      hasFellowLoser: false,
    });
    expect(out).toEqual(['salty', 'props']);
    expect(out).not.toContain('cry_luck');
    expect(out).not.toContain('vow');
    expect(out).not.toContain('commiserate');
  });

  it('lost at showdown heads-up: + cry_luck/vow, no commiserate (no fellow loser)', () => {
    expect(
      ids({ playerWon: false, isShowdown: true, humanAtShowdown: true, hasFellowLoser: false })
    ).toEqual(['salty', 'props', 'cry_luck', 'vow']);
  });

  it('lost at showdown multiway: + commiserate', () => {
    expect(
      ids({ playerWon: false, isShowdown: true, humanAtShowdown: true, hasFellowLoser: true })
    ).toEqual(['salty', 'props', 'cry_luck', 'vow', 'commiserate']);
  });

  it('folded with a fellow loser: salty/props + commiserate, still no needles', () => {
    expect(
      ids({ playerWon: false, isShowdown: true, humanAtShowdown: false, hasFellowLoser: true })
    ).toEqual(['salty', 'props', 'commiserate']);
  });
});
