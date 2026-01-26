/**
 * Shared message parsing utilities for dramatic sequence support.
 * Handles AI messages that contain beats (actions and speech) separated by newlines.
 */
import React from 'react';

export interface ParsedBeat {
  type: 'action' | 'speech';
  text: string;
}

/**
 * Parse message text into an array of beats.
 * Actions are wrapped in *asterisks*, speech is plain text.
 */
export function parseBeats(text: string): ParsedBeat[] {
  const lines = text.split('\n').filter(b => b.trim());
  return lines.map(line => {
    const actionMatch = line.match(/^\*(.+)\*$/);
    if (actionMatch) {
      return { type: 'action', text: actionMatch[1] };
    }
    return { type: 'speech', text: line };
  });
}

/**
 * Parse message text into React nodes with block-level beats (divs).
 * Best for chat panels where each beat should be on its own line.
 */
export function parseMessageBlock(text: string): React.ReactNode {
  const beats = parseBeats(text);
  if (beats.length === 0) return text;

  return beats.map((beat, i) => {
    if (beat.type === 'action') {
      return <div key={i} className="beat action"><em>{beat.text}</em></div>;
    }
    return <div key={i} className="beat speech">{beat.text}</div>;
  });
}

/**
 * Parse message text into React nodes with inline beats (spans).
 * Best for compact feeds where beats flow inline.
 */
export function parseMessageInline(text: string): React.ReactNode {
  const beats = parseBeats(text);
  if (beats.length === 0) return text;

  return beats.map((beat, i) => {
    if (beat.type === 'action') {
      return <span key={i} className="beat action"><em>{beat.text}</em> </span>;
    }
    return <span key={i} className="beat speech">{beat.text} </span>;
  });
}
