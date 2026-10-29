import type { Anchor } from './types';

export function anchorAt(text: string, start: number, end: number, sha: string): Anchor {
  return {
    quote: text.slice(start, end), start, end, sha,
    prefix: text.slice(Math.max(0, start - 48), start),
    suffix: text.slice(end, end + 48),
  };
}

export function locate(text: string, anchor: Anchor): [number, number] | null {
  const contextMatches = (start: number, end: number) =>
    text.slice(Math.max(0, start - anchor.prefix.length), start) === anchor.prefix &&
    text.slice(end, end + anchor.suffix.length) === anchor.suffix;
  if (
    text.slice(anchor.start, anchor.end) === anchor.quote &&
    contextMatches(anchor.start, anchor.end)
  ) return [anchor.start, anchor.end];
  const matches: [number, number][] = [];
  for (let start = text.indexOf(anchor.quote); start !== -1; start = text.indexOf(anchor.quote, start + 1)) {
    const end = start + anchor.quote.length;
    if (contextMatches(start, end)) matches.push([start, end]);
  }
  // Never silently attach a comment to the wrong duplicate sentence.
  return matches.length === 1 ? matches[0] : null;
}

export function rangeAt(root: HTMLElement, start: number, end: number): Range | null {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const range = document.createRange();
  let offset = 0;
  let started = false;
  let node = walker.nextNode();
  while (node) {
    const size = node.textContent?.length ?? 0;
    if (!started && start >= offset && start < offset + size) {
      range.setStart(node, start - offset);
      started = true;
    }
    if (started && end <= offset + size) {
      range.setEnd(node, end - offset);
      return range;
    }
    offset += size;
    node = walker.nextNode();
  }
  return null;
}

export function currentSelection(root: HTMLElement, sha: string): Anchor | null {
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || !selection.rangeCount) return null;
  const range = selection.getRangeAt(0);
  if (!root.contains(range.startContainer) || !root.contains(range.endContainer)) return null;
  const before = document.createRange();
  before.selectNodeContents(root);
  before.setEnd(range.startContainer, range.startOffset);
  const start = before.toString().length;
  const end = start + range.toString().length;
  if (!range.toString().trim()) return null;
  return anchorAt(root.textContent ?? '', start, end, sha);
}

export function quoteAnchor(root: HTMLElement, quote: string, sha: string): Anchor | null {
  const text = root.textContent ?? '';
  const plain = quote.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1').replace(/[*_`]/g, '');
  for (const candidate of [quote, plain]) {
    const start = text.indexOf(candidate);
    if (start !== -1 && text.indexOf(candidate, start + 1) === -1) {
      return anchorAt(text, start, start + candidate.length, sha);
    }
  }
  return null;
}

export function paintHighlights(root: HTMLElement, anchors: Anchor[], active?: Anchor) {
  const registry = CSS.highlights;
  if (!registry || typeof Highlight === 'undefined') return;
  const ranges = anchors.flatMap(anchor => {
    const position = locate(root.textContent ?? '', anchor);
    const range = position && rangeAt(root, ...position);
    return range ? [range] : [];
  });
  registry.set('comments', new Highlight(...ranges));
  const position = active && locate(root.textContent ?? '', active);
  const range = position && rangeAt(root, ...position);
  registry.set('active-comment', new Highlight(...(range ? [range] : [])));
}
