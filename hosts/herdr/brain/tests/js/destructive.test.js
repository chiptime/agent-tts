/* node --test suite for the destructive-command guard (Panel parity S6). */

const test = require("node:test");
const assert = require("node:assert");
const { isDestructiveInput, DESTRUCTIVE_PATTERNS } = require("../../src/herdr_brain/static/destructive.js");

test("each destructive pattern is detected with its own reason", () => {
  const cases = [
    ["rm -rf /tmp/x", "rm -r"],
    ["rm --recursive build", "rm -r"],
    ["git push --force origin main", "git push --force"],
    ["git push -f", "git push --force"],
    ["sudo apt update", "sudo"],
    ["cargo publish --force", "--force"],
    ["dd if=/dev/zero of=/dev/sda", "dd if="],
    ["mkfs.ext4 /dev/sdb", "mkfs"],
    ["> /etc/passwd", "redirect"],
    [":> /var/log/syslog", "redirect"],
  ];
  for (const [text, expected] of cases) {
    const reason = isDestructiveInput(text);
    assert.ok(reason && reason.includes(expected), `${text} -> ${reason}`);
  }
});

test("innocent look-alikes do not trigger", () => {
  for (const text of ["assume", "sudoku", "forced", "ls -la", "grep -r foo .", "echo hi > out.txt", "please run the tests"]) {
    assert.strictEqual(isDestructiveInput(text), null, text);
  }
});

test("the rm gap does not cross a command separator", () => {
  assert.strictEqual(isDestructiveInput("echo hi; rm file.txt"), null);
  assert.strictEqual(isDestructiveInput("rm file.txt; grep -r foo ."), null);
});

test("first matching pattern wins and results are stable across calls", () => {
  assert.ok(isDestructiveInput("sudo rm -rf /").includes("rm -r"));
  for (let i = 0; i < 3; i++) {
    assert.ok(isDestructiveInput("sudo ls").includes("sudo"));
  }
  for (const { pattern } of DESTRUCTIVE_PATTERNS) {
    assert.ok(!pattern.global, `${pattern} must not use the g flag`);
  }
});

test("non-string input is never destructive", () => {
  assert.strictEqual(isDestructiveInput(undefined), null);
  assert.strictEqual(isDestructiveInput(null), null);
  assert.strictEqual(isDestructiveInput(""), null);
});
