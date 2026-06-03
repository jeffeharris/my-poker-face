import { describe, it, expect } from 'vitest';
import type { Player } from '../../types';
import { orderOpponentsRelativeToHuman } from '../playerOrdering';

// Minimal Player shape — only the fields the ordering reads.
const mk = (name: string, is_human = false): Player => ({ name, is_human }) as unknown as Player;

const names = (players: Player[]) => players.map((p) => p.name);

describe('orderOpponentsRelativeToHuman', () => {
  it('orders opponents clockwise starting from the seat after the human', () => {
    // Seats: A, human, C, D  →  from the human, clockwise: C, D, A.
    const seats = [mk('A'), mk('Me', true), mk('C'), mk('D')];
    expect(names(orderOpponentsRelativeToHuman(seats))).toEqual(['C', 'D', 'A']);
  });

  it('keeps original order when the human is first', () => {
    const seats = [mk('Me', true), mk('B'), mk('C'), mk('D')];
    expect(names(orderOpponentsRelativeToHuman(seats))).toEqual(['B', 'C', 'D']);
  });

  it('wraps around when the human is last', () => {
    const seats = [mk('A'), mk('B'), mk('C'), mk('Me', true)];
    expect(names(orderOpponentsRelativeToHuman(seats))).toEqual(['A', 'B', 'C']);
  });

  it('drops the human from the result', () => {
    const seats = [mk('A'), mk('Me', true), mk('C')];
    expect(names(orderOpponentsRelativeToHuman(seats))).not.toContain('Me');
  });

  it('falls back to original non-human order when there is no human', () => {
    const seats = [mk('A'), mk('B'), mk('C')];
    expect(names(orderOpponentsRelativeToHuman(seats))).toEqual(['A', 'B', 'C']);
  });
});
