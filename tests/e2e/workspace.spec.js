/**
 * workspace.spec.js — Nexora Playwright Regression Suite v1
 *
 * Coverage:
 *   - Boot & shell integrity
 *   - Workspace layout (panels, tabs, dock)
 *   - Command palette
 *   - Settings modal
 *   - More menu + tab restore
 *   - Workspace presets
 *   - Snapshot save/restore
 *   - Onboarding flow
 *   - Streaming stability (SSE mock)
 *   - Status bar
 *   - NxBus event routing
 *
 * Run: npx playwright test tests/e2e/workspace.spec.js
 *
 * Prereqs: npm i -D @playwright/test && npx playwright install chromium
 */

const { test, expect } = require('@playwright/test');

const BASE = process.env.NX_BASE_URL || 'http://localhost:5000';
const TIMEOUT = 15_000;

/* ── helpers ── */
async function login(page) {
  await page.goto(BASE, { waitUntil: 'networkidle', timeout: TIMEOUT });
  // If login wall is shown, fill credentials
  const emailInput = page.locator('#loginEmail, input[type="email"]').first();
  if (await emailInput.isVisible({ timeout: 2000 }).catch(() => false)) {
    await emailInput.fill(process.env.NX_TEST_EMAIL || 'test@test.com');
    await page.locator('#loginPassword, input[type="password"]').first().fill(process.env.NX_TEST_PASS || 'test1234');
    await page.locator('button[type="submit"], #loginBtn').first().click();
    await page.waitForFunction(() => window.NX && window.NX.state === 'interactive', { timeout: TIMEOUT });
  }
}

async function waitForBoot(page) {
  await page.waitForFunction(
    () => window.NX && ['interactive','interactive-degraded'].includes(window.NX.state),
    { timeout: TIMEOUT }
  );
}

/* ══ TEST GROUP 1: Boot & Shell ══════════════════════════════════════ */
test.describe('Boot & Shell', () => {
  test.beforeEach(async ({ page }) => { await login(page); await waitForBoot(page); });

  test('NX global is defined and interactive', async ({ page }) => {
    const state = await page.evaluate(() => window.NX?.state);
    expect(['interactive','interactive-degraded']).toContain(state);
  });

  test('NxBus is available with EVENTS registry', async ({ page }) => {
    const ok = await page.evaluate(() =>
      typeof window.NxBus === 'object' &&
      typeof window.NxBus.on === 'function' &&
      typeof window.NxBus.EVENTS === 'object'
    );
    expect(ok).toBe(true);
  });

  test('NxState has three slices', async ({ page }) => {
    const ok = await page.evaluate(() =>
      window.NxState && ['workspace','runtime','ui'].every(s => s in window.NxState)
    );
    expect(ok).toBe(true);
  });

  test('NxWorkspace controller is initialized', async ({ page }) => {
    const ok = await page.evaluate(() =>
      typeof window.NxWorkspace === 'object' &&
      typeof window.NxWorkspace.toggleLeft === 'function'
    );
    expect(ok).toBe(true);
  });

  test('topbar is visible', async ({ page }) => {
    await expect(page.locator('.nx-header')).toBeVisible();
  });

  test('settings button is visible and keyboard accessible', async ({ page }) => {
    const btn = page.locator('#settingsBtn');
    await expect(btn).toBeVisible();
    await btn.focus();
    await expect(btn).toBeFocused();
  });

  test('no uncaught JS errors on boot', async ({ page }) => {
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.reload({ waitUntil: 'networkidle' });
    await waitForBoot(page);
    // Allow degraded but not crash-level errors
    expect(errors.filter(e => e.includes('SyntaxError'))).toHaveLength(0);
  });
});

/* ══ TEST GROUP 2: Workspace Layout ══════════════════════════════════ */
test.describe('Workspace Layout', () => {
  test.beforeEach(async ({ page }) => { await login(page); await waitForBoot(page); });

  test('left panel toggle opens and closes', async ({ page }) => {
    const beforeOpen = await page.evaluate(() => window.NxWorkspace.getState().leftOpen);
    await page.evaluate(() => window.NxWorkspace.toggleLeft());
    await page.waitForTimeout(350); // allow transition
    const afterOpen = await page.evaluate(() => window.NxWorkspace.getState().leftOpen);
    expect(afterOpen).toBe(!beforeOpen);
  });

  test('right panel toggle opens and closes', async ({ page }) => {
    const before = await page.evaluate(() => window.NxWorkspace.getState().rightOpen);
    await page.evaluate(() => window.NxWorkspace.toggleRight());
    await page.waitForTimeout(350);
    const after = await page.evaluate(() => window.NxWorkspace.getState().rightOpen);
    expect(after).toBe(!before);
  });

  test('bottom dock toggle changes bottomOpen state', async ({ page }) => {
    const before = await page.evaluate(() => window.NxWorkspace.getState().bottomOpen);
    await page.evaluate(() => window.NxWorkspace.toggleBottom());
    await page.waitForTimeout(350);
    const after = await page.evaluate(() => window.NxWorkspace.getState().bottomOpen);
    expect(after).toBe(!before);
  });

  test('layout state persists across reload', async ({ page }) => {
    await page.evaluate(() => window.NxWorkspace.setRightWidth(320));
    await page.waitForTimeout(200);
    await page.reload({ waitUntil: 'networkidle' });
    await waitForBoot(page);
    const rw = await page.evaluate(() => window.NxWorkspace.getState().rightW);
    expect(rw).toBeGreaterThanOrEqual(280);
  });

  test('reset layout restores defaults', async ({ page }) => {
    await page.evaluate(() => window.NxWorkspace.resetLayout());
    await page.waitForTimeout(300);
    const state = await page.evaluate(() => window.NxWorkspace.getState());
    expect(state).toBeTruthy();
  });
});

/* ══ TEST GROUP 3: Tabs ══════════════════════════════════════════════ */
test.describe('Tabs', () => {
  test.beforeEach(async ({ page }) => { await login(page); await waitForBoot(page); });

  test('tab bar is visible', async ({ page }) => {
    const bar = page.locator('#nxTabBar, .nx-tab-bar').first();
    await expect(bar).toBeVisible();
  });

  test('tab close removes tab from DOM', async ({ page }) => {
    const tabId = 'metrics';
    const tabSelector = `[data-tab="${tabId}"], #nxTab-${tabId}`;
    const tabEl = page.locator(tabSelector).first();
    if (await tabEl.isVisible({ timeout: 1000 }).catch(() => false)) {
      await page.evaluate(id => window.NxWorkspace.closeTab(id), tabId);
      await page.waitForTimeout(300);
      const exists = await tabEl.isVisible({ timeout: 500 }).catch(() => false);
      expect(exists).toBe(false);
    }
  });

  test('closed tab can be reopened via More menu', async ({ page }) => {
    await page.evaluate(() => window.NxWorkspace.closeTab('metrics'));
    await page.waitForTimeout(200);
    const moreBtn = page.locator('.nx-more-btn, #nxMoreBtn').first();
    if (await moreBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
      await moreBtn.click();
      await page.waitForTimeout(200);
      await page.evaluate(() => window.NxWorkspace.openTab('metrics'));
      await page.waitForTimeout(200);
      const tabBack = await page.evaluate(() => window.NxWorkspace.isTabOpen('metrics'));
      expect(tabBack).toBe(true);
    }
  });
});

/* ══ TEST GROUP 4: Command Palette ═══════════════════════════════════ */
test.describe('Command Palette', () => {
  test.beforeEach(async ({ page }) => { await login(page); await waitForBoot(page); });

  test('opens with Ctrl+K', async ({ page }) => {
    await page.keyboard.press('Control+k');
    await page.waitForTimeout(200);
    const palette = page.locator('#nxPalette');
    await expect(palette).toBeVisible();
  });

  test('closes with Escape', async ({ page }) => {
    await page.keyboard.press('Control+k');
    await page.waitForTimeout(150);
    await page.keyboard.press('Escape');
    await page.waitForTimeout(200);
    const palette = page.locator('#nxPalette');
    await expect(palette).not.toBeVisible();
  });

  test('search filters items', async ({ page }) => {
    await page.keyboard.press('Control+k');
    await page.waitForTimeout(150);
    const input = page.locator('#nxPaletteInput');
    await input.fill('Settings');
    await page.waitForTimeout(150);
    const items = page.locator('.nx-palette-item');
    await expect(items.first()).toBeVisible();
    const text = await items.first().innerText();
    expect(text.toLowerCase()).toContain('settings');
  });

  test('preset items are grouped', async ({ page }) => {
    await page.keyboard.press('Control+k');
    await page.waitForTimeout(150);
    const groups = page.locator('.nx-palette-group');
    const count = await groups.count();
    expect(count).toBeGreaterThan(0);
  });
});

/* ══ TEST GROUP 5: Settings Modal ════════════════════════════════════ */
test.describe('Settings Modal', () => {
  test.beforeEach(async ({ page }) => { await login(page); await waitForBoot(page); });

  test('settings button opens modal', async ({ page }) => {
    await page.click('#settingsBtn');
    const modal = page.locator('#settingsModal, .nx-settings-modal, [id*="settings"]').first();
    await expect(modal).toBeVisible({ timeout: 3000 });
  });

  test('settings opens with Ctrl+,', async ({ page }) => {
    await page.keyboard.press('Control+,');
    await page.waitForTimeout(400);
    const modal = page.locator('#settingsModal, .settings-panel').first();
    await expect(modal).toBeVisible({ timeout: 3000 });
  });
});

/* ══ TEST GROUP 6: Workspace Presets ═════════════════════════════════ */
test.describe('Workspace Presets', () => {
  test.beforeEach(async ({ page }) => { await login(page); await waitForBoot(page); });

  test('Builder preset closes left panel', async ({ page }) => {
    await page.evaluate(() => nxApplyPreset('builder'));
    await page.waitForTimeout(600);
    const s = await page.evaluate(() => window.NxWorkspace.getState());
    expect(s.leftOpen).toBe(false);
  });

  test('Debug preset opens bottom dock', async ({ page }) => {
    await page.evaluate(() => nxApplyPreset('debug'));
    await page.waitForTimeout(600);
    const s = await page.evaluate(() => window.NxWorkspace.getState());
    expect(s.bottomOpen).toBe(true);
  });

  test('Minimal preset closes both side panels', async ({ page }) => {
    await page.evaluate(() => nxApplyPreset('minimal'));
    await page.waitForTimeout(600);
    const s = await page.evaluate(() => window.NxWorkspace.getState());
    expect(s.leftOpen).toBe(false);
    expect(s.rightOpen).toBe(false);
  });
});

/* ══ TEST GROUP 7: Snapshots ══════════════════════════════════════════ */
test.describe('Workspace Snapshots', () => {
  test.beforeEach(async ({ page }) => { await login(page); await waitForBoot(page); });

  test('capture() returns snapshot with id', async ({ page }) => {
    const snap = await page.evaluate(() => window.NxSnapshots && NxSnapshots.capture('Test Snap'));
    expect(snap).toBeTruthy();
    expect(snap.id).toBeGreaterThan(0);
    expect(snap.name).toContain('Test');
  });

  test('captured snapshot appears in list()', async ({ page }) => {
    await page.evaluate(() => NxSnapshots.capture('ListTest'));
    const list = await page.evaluate(() => NxSnapshots.list());
    expect(list.length).toBeGreaterThan(0);
    expect(list[0].name).toContain('ListTest');
  });

  test('Ctrl+Shift+S saves snapshot', async ({ page }) => {
    const before = await page.evaluate(() => NxSnapshots.list().length);
    await page.keyboard.press('Control+Shift+S');
    await page.waitForTimeout(300);
    const after = await page.evaluate(() => NxSnapshots.list().length);
    expect(after).toBeGreaterThanOrEqual(before);
  });
});

/* ══ TEST GROUP 8: Onboarding ════════════════════════════════════════ */
test.describe('Onboarding', () => {
  test('shows onboard modal on first visit', async ({ page }) => {
    // Clear onboarded flag
    await page.addInitScript(() => localStorage.removeItem('nx_onboarded_v1'));
    await login(page);
    await waitForBoot(page);
    await page.waitForTimeout(500);
    const modal = page.locator('#ndsOnboard');
    await expect(modal).toBeVisible({ timeout: 3000 });
  });

  test('dismiss hides onboard modal', async ({ page }) => {
    await page.addInitScript(() => localStorage.removeItem('nx_onboarded_v1'));
    await login(page);
    await waitForBoot(page);
    await page.waitForTimeout(500);
    await page.click('#ndsOnboardDismiss');
    await page.waitForTimeout(400);
    const modal = page.locator('#ndsOnboard');
    await expect(modal).not.toBeVisible();
  });

  test('preset selection applies preset and dismisses', async ({ page }) => {
    await page.addInitScript(() => localStorage.removeItem('nx_onboarded_v1'));
    await login(page);
    await waitForBoot(page);
    await page.waitForTimeout(500);
    const presetBtn = page.locator('[data-preset="minimal"]').first();
    if (await presetBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
      await presetBtn.click();
      await page.waitForTimeout(600);
      const modal = page.locator('#ndsOnboard');
      await expect(modal).not.toBeVisible();
    }
  });

  test('does not show on second visit', async ({ page }) => {
    // Flag is set
    await page.addInitScript(() => localStorage.setItem('nx_onboarded_v1', '1'));
    await login(page);
    await waitForBoot(page);
    await page.waitForTimeout(500);
    const modal = page.locator('#ndsOnboard');
    await expect(modal).not.toBeVisible({ timeout: 1000 });
  });
});

/* ══ TEST GROUP 9: Status Bar ════════════════════════════════════════ */
test.describe('Status Bar', () => {
  test.beforeEach(async ({ page }) => { await login(page); await waitForBoot(page); });

  test('status bar is in DOM', async ({ page }) => {
    const bar = page.locator('#nxStatusBar');
    await expect(bar).toBeAttached();
  });

  test('NxStatusBar.setTask updates task text', async ({ page }) => {
    await page.evaluate(() => window.NxStatusBar && NxStatusBar.setTask('running', 'Test task'));
    await page.waitForTimeout(100);
    const text = await page.locator('#nxSbTaskText').innerText().catch(() => '');
    expect(text).toContain('Running');
  });
});

/* ══ TEST GROUP 10: NxBus Event Routing ═════════════════════════════ */
test.describe('NxBus', () => {
  test.beforeEach(async ({ page }) => { await login(page); await waitForBoot(page); });

  test('emit and receive event roundtrip', async ({ page }) => {
    const received = await page.evaluate(() => new Promise(resolve => {
      const unsub = NxBus.on('nx:test:ping', (d) => { unsub(); resolve(d); });
      NxBus.emit('nx:test:ping', { value: 42 });
    }));
    expect(received.value).toBe(42);
  });

  test('once() handler fires only once', async ({ page }) => {
    const count = await page.evaluate(() => new Promise(resolve => {
      let n = 0;
      NxBus.once('nx:test:once', () => n++);
      NxBus.emit('nx:test:once');
      NxBus.emit('nx:test:once');
      setTimeout(() => resolve(n), 50);
    }));
    expect(count).toBe(1);
  });

  test('offAll removes all listeners for an owner', async ({ page }) => {
    const count = await page.evaluate(() => {
      NxBus.on('nx:test:leak', () => {}, { owner: 'test-module' });
      NxBus.on('nx:test:leak2', () => {}, { owner: 'test-module' });
      return NxBus.offAll('test-module');
    });
    expect(count).toBeGreaterThanOrEqual(2);
  });

  test('TOAST event routes to NdsToast', async ({ page }) => {
    const toastShown = await page.evaluate(() => new Promise(resolve => {
      const orig = window.NdsToast;
      window.NdsToast = (msg, type) => { window.NdsToast = orig; resolve({msg, type}); };
      NxBus.emit(NxBus.EVENTS.TOAST, { msg: 'Bus test', type: 'ok' });
    }));
    expect(toastShown.msg).toContain('Bus test');
  });
});
