import { test, expect } from '@playwright/test';

test('a running research job does not block a new conversation', async ({ page }) => {
  await page.route('**/api/documents/fixture', async route => {
    const response = await route.fetch();
    const state = await response.json();
    state.jobs.unshift({
      id: 'research-in-progress', kind: 'research', thread_id: null,
      status: 'running', activity: 'Reading documentation', error: null,
      answer: '', created_at: new Date().toISOString(),
    });
    await route.fulfill({ response, json: state });
  });
  await page.goto('/');
  await page.locator('article h1').waitFor();
  await page.locator('article h1').evaluate(element => {
    const range = document.createRange();
    range.selectNodeContents(element);
    const selection = window.getSelection()!;
    selection.removeAllRanges();
    selection.addRange(range);
    element.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
  });
  await page.getByRole('button', { name: 'Comment', exact: true }).click();
  await page.getByRole('textbox', { name: 'Your comment' }).fill('Can we discuss while research runs?');
  await expect(page.getByRole('button', { name: 'Send to Copilot' })).toBeEnabled();
});

test('select formatted text, converse, persist, resolve, discuss a source, and export', async ({ page }) => {
  const browserErrors: string[] = [];
  page.on('pageerror', error => browserErrors.push(error.message));
  await page.goto('/');
  await expect(page.locator('article h1')).toHaveText('A test decision');
  await expect(page.getByRole('heading', { name: 'What freshness guarantee do readers get?' })).toBeVisible();
  await expect(page.locator('.provocation-card').first()).toContainText('92.5/100');
  await expect(page.locator('.provocation-card').last()).toContainText('50/100');
  await page.locator('.provocation-card').first().getByText('Why this rank?').click();
  await expect(page.locator('.ranking-details').first()).toContainText('Decision impact');
  await expect(page.locator('.sources-footer')).toContainText('No vault access');

  // Select across text nodes, including a formatted word and its surrounding sentence.
  await page.locator('article p').first().evaluate(element => {
    const range = document.createRange();
    range.setStart(element.firstChild!, 'First Redis is a cache. '.length);
    range.setEnd(element.lastChild!, ' is persistent.'.length);
    const selection = window.getSelection()!;
    selection.removeAllRanges();
    selection.addRange(range);
    element.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
  });
  await page.getByRole('button', { name: 'Comment', exact: true }).click();
  await expect(page.locator('.thread-anchor')).toContainText('Second Redis is persistent.');
  await page.getByRole('textbox', { name: 'Your comment' }).fill('Why does the second Redis persist?');
  await page.getByRole('button', { name: 'Send to Copilot' }).click();
  await expect(page.locator('.message.assistant')).toContainText('explicit freshness policy');
  await page.getByRole('textbox', { name: 'Your comment' }).fill('Keep this as a note.');
  await page.getByLabel('Ask Copilot').uncheck();
  await page.getByRole('button', { name: 'Save comment' }).click();
  await expect(page.locator('.message.user')).toHaveCount(2);
  await page.reload();
  await page.locator('.thread-preview').first().click();
  await expect(page.locator('.message.user')).toHaveCount(2);
  await expect(page.locator('.thread-anchor')).toContainText('Second Redis is persistent.');
  expect(await page.evaluate(() => {
    const registry = (CSS as typeof CSS & { highlights: Map<string, { size: number }> }).highlights;
    return registry.get('active-comment')?.size;
  })).toBe(1);
  await page.getByRole('button', { name: 'Resolve', exact: true }).click();
  await page.getByRole('button', { name: 'All threads' }).click();
  await expect(page.locator('.thread-preview')).toHaveCount(0);
  await page.getByRole('tab', { name: 'Resolved', exact: true }).click();
  await expect(page.locator('.thread-preview')).toHaveCount(1);

  await page.getByTitle('Save provocation').first().click();
  await page.getByRole('button', { name: 'Saved', exact: true }).click();
  await expect(page.locator('.provocation-card')).toHaveCount(1);
  await page.locator('.source-list summary').click();
  await expect(page.locator('.source-list pre')).toContainText('Document freshness.');
  await page.getByRole('button', { name: 'Discuss', exact: true }).click();
  await expect(page.getByRole('textbox', { name: 'Your comment' })).toHaveValue(/maximum staleness/);
  const download = page.waitForEvent('download');
  await page.getByRole('link', { name: 'Export review' }).click();
  expect((await download).suggestedFilename()).toBe('adr-review.md');
  expect(browserErrors).toEqual([]);
});

test('promote a conversation, edit feedback and combine only saved included items', async ({ page }) => {
  await page.request.post('/api/documents/fixture/threads', {
    data: {
      anchor: { quote: 'bounded caching', start: 0, end: 15, prefix: '', suffix: '', sha: 'a'.repeat(40) },
      message: 'Please clarify maximum staleness for this review.', ask_ai: false,
    },
  });
  await page.goto('/');
  await page.locator('.thread-preview').filter({ hasText: 'Please clarify maximum staleness' }).click();
  await page.getByRole('button', { name: 'Add feedback', exact: true }).click();
  await expect(page.getByRole('tab', { name: /Feedback/ })).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('.feedback-suggestion')).toContainText('maximum acceptable cache staleness');
  await expect(page.getByRole('button', { name: 'Combine review', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Use suggestion in editor' }).click();
  await page.getByRole('textbox', { name: 'Feedback text', exact: true }).fill('My final request: document a maximum staleness of 60 seconds.');
  await page.getByRole('button', { name: 'Save feedback', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Combine review', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Draft with AI', exact: true }).click();
  await expect(page.getByRole('textbox', { name: 'Feedback text', exact: true })).toHaveValue(/My final request/);
  await page.getByRole('button', { name: 'Combine review', exact: true }).click();
  await expect(page.locator('.combined-review')).toContainText('My final request');
  await expect(page.locator('.combined-review')).not.toContainText('maximum acceptable cache staleness');
  const download = page.waitForEvent('download');
  await page.getByRole('link', { name: 'Export feedback', exact: true }).click();
  expect((await download).suggestedFilename()).toBe('adr-feedback.md');
  await page.reload();
  await page.getByRole('tab', { name: /Feedback/ }).click();
  await expect(page.getByRole('textbox', { name: 'Feedback text', exact: true })).toHaveValue(/My final request/);
  await page.getByLabel('Include saved feedback in review').uncheck();
  await expect(page.getByRole('button', { name: 'Combine review', exact: true })).toBeDisabled();
});
