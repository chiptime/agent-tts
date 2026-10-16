/* Structural wiring tests for the cockpit view tabs:
 * "vista del agente" (transcript turns) and "pantalla" (terminal preview)
 * are two tabs of ONE cockpit body, so exactly one is shown at a time.
 *
 * LIMITS, stated plainly: app.js is a single browser-only IIFE (DOM,
 * EventSource, live audio element) that node --test cannot load, so the
 * toggle itself cannot be EXECUTED here. These tests pin the wiring SHAPE
 * by reading the shipped sources as text — exactly the discipline of
 * pending-wiring.test.js and announce-wiring.test.js. The real swap
 * (clicking a tab shows its panel and hides the other) is device/Chromium
 * territory and lives in tests/browser/test_agent_tabs.py.
 *
 * The regression this file guards: the terminal preview used to be stacked
 * under the transcript as a permanent 110px strip, so it was always visible
 * but never readable, and it pushed the turns up. Splitting the body into
 * tabs is what gives each surface its own height.
 */

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const appSrc = fs.readFileSync(
  path.join(__dirname, "../../src/herdr_brain/static/app.js"), "utf8");
const htmlSrc = fs.readFileSync(
  path.join(__dirname, "../../src/herdr_brain/static/index.html"), "utf8");

/* The agent cockpit slice: the tablist must live INSIDE #agent-view, not
 * floating elsewhere in the document. */
const viewStart = htmlSrc.indexOf('id="agent-view"');
const viewSlice = htmlSrc.slice(viewStart, htmlSrc.indexOf("</section>", viewStart));

test("the cockpit carries one tablist with two tabs and two panels", () => {
  assert.notEqual(viewStart, -1, "the cockpit (#agent-view) must exist");
  assert.ok(viewSlice.includes('id="agent-tabs"'), "the tablist must exist");
  assert.ok(viewSlice.includes('role="tablist"'), "it must be a tablist");

  for (const tab of ["tab-agent", "tab-screen"]) {
    assert.ok(viewSlice.includes(`id="${tab}"`), `missing #${tab}`);
  }
  for (const panel of ["agent-panel", "screen-panel"]) {
    assert.ok(viewSlice.includes(`id="${panel}"`), `missing #${panel}`);
  }
  // One tab per panel: agent -> agent-panel, screen -> screen-panel.
  assert.ok(viewSlice.includes('aria-controls="agent-panel"'));
  assert.ok(viewSlice.includes('aria-controls="screen-panel"'));

  // Only one panel is shown at rest: the screen panel starts hidden and
  // its tab starts unselected, while the agent tab is the default.
  assert.ok(/aria-selected="false"/.test(viewSlice), "one tab starts unselected");
  assert.ok(/id="screen-panel"[^>]*class="agent-panel hidden"|class="agent-panel hidden"[^>]*id="screen-panel"/.test(viewSlice),
    "the screen panel must start hidden");
});

test("each surface lives inside its own panel, never stacked", () => {
  const agentIdx = viewSlice.indexOf('id="agent-panel"');
  const turnsIdx = viewSlice.indexOf('id="glance-turns"');
  const screenIdx = viewSlice.indexOf('id="screen-panel"');
  const terminalIdx = viewSlice.indexOf('id="view-screen"');
  assert.ok(agentIdx < turnsIdx && turnsIdx < screenIdx && screenIdx < terminalIdx,
    "turns must sit inside #agent-panel and the terminal inside #screen-panel");
});

test("app.js wires both tabs and swaps the panels", () => {
  assert.ok(appSrc.includes('$("tab-agent")'), "must resolve #tab-agent");
  assert.ok(appSrc.includes('$("tab-screen")'), "must resolve #tab-screen");
  assert.ok(appSrc.includes('$("agent-panel")'), "must resolve #agent-panel");
  assert.ok(appSrc.includes('$("screen-panel")'), "must resolve #screen-panel");

  // Selection is expressed on the ARIA contract, not only on a CSS class.
  assert.ok(appSrc.includes('setAttribute("aria-selected"'),
    "the toggle must drive aria-selected");
  assert.ok(appSrc.includes('classList.toggle("hidden"'),
    "the toggle must show/hide the panels");

  // Both tabs are clickable and the tablist responds to arrow keys.
  assert.ok(appSrc.includes('tabAgent.addEventListener("click"'));
  assert.ok(appSrc.includes('tabScreen.addEventListener("click"'));
  assert.ok(appSrc.includes('"ArrowRight"') && appSrc.includes('"ArrowLeft"'),
    "arrow keys must move between the tabs");
});

test("the chosen tab is remembered across reloads", () => {
  assert.ok(appSrc.includes("TAB_KEY"),
    "the selection must be persisted under a stable key");
  assert.ok(appSrc.includes("localStorage.setItem(TAB_KEY"),
    "the toggle must write the chosen tab");
  assert.ok(appSrc.includes("localStorage.getItem(TAB_KEY"),
    "startup must read the stored tab");
});
