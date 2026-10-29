import { describe, expect, it } from 'vitest';
import { anchorAt, locate } from './anchors';

describe('selection anchors', () => {
  it('keeps the correct occurrence of repeated words', () => {
    const text = 'First Redis is fast. Second Redis is persistent.';
    const start = text.lastIndexOf('Redis');
    const anchor = anchorAt(text, start, start + 5, 'sha');
    expect(locate(text, anchor)).toEqual([start, start + 5]);
  });

  it('relocates using surrounding context when rendered offsets shift', () => {
    const text = 'Context before a decision and context after.';
    const anchor = anchorAt(text, 17, 25, 'sha');
    expect(locate(`Inserted ${text}`, anchor)).toEqual([26, 34]);
  });

  it('does not silently move an annotation to an ambiguous occurrence', () => {
    const anchor = { ...anchorAt('same', 0, 4, 'sha'), start: 100, end: 104 };
    expect(locate('same same', anchor)).toBeNull();
  });

  it('counts UTF-16 offsets used by browser ranges, including emoji', () => {
    const text = '😀 Choose Redis';
    const start = text.indexOf('Redis');
    const anchor = anchorAt(text, start, text.length, 'sha');
    expect(anchor.quote).toBe('Redis');
    expect(locate(text, anchor)).toEqual([10, 15]);
  });
});

