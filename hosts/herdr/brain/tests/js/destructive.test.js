/* node --test suite for the destructive-command guard.

 The guard is an analyzer: the input is split into shell segments,
 tokenized, and named rules vote over the tokens. The suite pins the
 detected set, the innocent look-alikes, and the analyzer's structural
 guarantees (separator scoping, quote handling, rule priority). */

const test = require("node:test");
const assert = require("node:assert");
const {
  isDestructiveInput,
  DESTRUCTIVE_RULES,
} = require("../../src/herdr_brain/static/destructive.js");

/* ---- the detected set: each rule fires with its own reason ---- */

test("each destructive rule is detected with its own reason", () => {
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

/* ---- innocent look-alikes stay clean ---- */

test("innocent look-alikes do not trigger", () => {
  for (const text of [
    "assume",
    "sudoku",
    "forced",
    "ls -la",
    "grep -r foo .",
    "rm file.txt",
    "echo hi > out.txt",
    "please run the tests",
  ]) {
    assert.strictEqual(isDestructiveInput(text), null, text);
  }
});

/* ---- segmentation: flags never leak across a separator ---- */

test("a rule never crosses a command separator", () => {
  // An -r belonging to a later command is not rm's flag…
  assert.strictEqual(isDestructiveInput("echo hi; rm file.txt"), null);
  assert.strictEqual(isDestructiveInput("rm file.txt; grep -r foo ."), null);
  assert.strictEqual(isDestructiveInput("rm file.txt && ls -r"), null);
  // …and an if= after a separator is not dd's argument.
  assert.strictEqual(isDestructiveInput("dd of=img.bin; xxd if=rom.bin"), null);
  assert.strictEqual(isDestructiveInput("cat if=x && dd of=y"), null);
});

test("a separator inside quotes does not split the segment, but the words stay visible", () => {
  // The quoted ";" is text, so this is one echo segment — and the words
  // inside the quotes are still tokens the rules see (sudo fires).
  assert.ok(isDestructiveInput('echo "hi; sudo reboot"').includes("sudo"));
  // A quoted command line handed to a shell is inspected like any other.
  assert.ok(isDestructiveInput('bash -c "sudo rm -rf /"').includes("rm -r"));
});

/* ---- rules evaluate tokens, not substrings ---- */

test("flag and keyword rules match whole tokens", () => {
  // --force is a token, not a substring of a longer word.
  assert.strictEqual(isDestructiveInput("echo not--force"), null);
  assert.strictEqual(isDestructiveInput("grep --force-overrides ."), null);
  // rm and sudo are words, not fragments (assume/sudoku stay clean).
  assert.strictEqual(isDestructiveInput("xmkfs"), null);
});

test("flags are found wherever they stand after the program word", () => {
  assert.ok(isDestructiveInput("rm -r -f build").includes("rm -r"));
  assert.ok(isDestructiveInput("rm -fr build").includes("rm -r"));
  assert.ok(isDestructiveInput("rm -rf \"my build dir\"").includes("rm -r"));
  assert.ok(isDestructiveInput("git push origin main -f").includes("git push --force"));
});

test("matching is case-insensitive and force-with-lease is still a push rewrite", () => {
  assert.ok(isDestructiveInput("RM -RF build").includes("rm -r"));
  assert.ok(isDestructiveInput("SUDO reboot").includes("sudo"));
  assert.ok(isDestructiveInput("MKFS /dev/sdb").includes("mkfs"));
  assert.ok(isDestructiveInput("git push --force-with-lease").includes("git push --force"));
});

test("the short -f belongs to the git push rule alone", () => {
  // Half the CLI world has a harmless -f; only a rule that knows its
  // program may read it as force.
  assert.strictEqual(isDestructiveInput("rm -f file.txt"), null);
  assert.strictEqual(isDestructiveInput("docker rm -f web"), null);
  assert.strictEqual(isDestructiveInput("git status push -f"), null);
  assert.strictEqual(isDestructiveInput("git -C /r push -f"), null);
});

test("program words are seen through shell wrappers", () => {
  assert.ok(isDestructiveInput("env sudo apt update").includes("sudo"));
  assert.ok(isDestructiveInput("xargs rm -r logs").includes("rm -r"));
  assert.ok(isDestructiveInput("sudo -u admin rm -rf /tmp/x").includes("rm -r"));
  assert.ok(isDestructiveInput("$(sudo rm -rf /)").includes("rm -r"));
});

/* ---- redirection targets ---- */

test("redirection onto an absolute system path is flagged, relative targets are not", () => {
  assert.ok(isDestructiveInput("echo x > /etc/motd").includes("redirect"));
  assert.ok(isDestructiveInput("echo x>/etc/passwd").includes("redirect"));
  assert.ok(isDestructiveInput(">/dev/sda").includes("redirect"));
  assert.ok(isDestructiveInput("append >> /etc/hosts").includes("redirect"));
  assert.ok(isDestructiveInput("echo x > /home/user/notes").includes("redirect"));
  assert.strictEqual(isDestructiveInput("echo hi > out.txt"), null);
  assert.strictEqual(isDestructiveInput("echo hi >> log/out.txt"), null);
});

test("/dev/null is the one exempt absolute target", () => {
  // Silencing a command is routine and harmless: the guard must not
  // make every 2> /dev/null wait for a confirmation tap.
  assert.strictEqual(isDestructiveInput("make test 2> /dev/null"), null);
  assert.strictEqual(isDestructiveInput("./run.sh 2>/dev/null"), null);
  assert.strictEqual(isDestructiveInput("cmd >/dev/null"), null);
  // …but every other /dev target keeps its teeth.
  assert.ok(isDestructiveInput("dd if=x of=/dev/sda").includes("dd if="));
});

/* ---- priority and stability ---- */

test("the most specific rule wins regardless of text position, and results are stable", () => {
  // rm -r outranks sudo even when sudo comes first in the text.
  assert.ok(isDestructiveInput("sudo rm -rf /").includes("rm -r"));
  // …and across segments, the rule order decides, not the position.
  assert.ok(isDestructiveInput("sudo ls; git push -f").includes("git push --force"));
  for (let i = 0; i < 3; i++) {
    assert.ok(isDestructiveInput("sudo ls").includes("sudo"));
    assert.strictEqual(isDestructiveInput("make test 2> /dev/null"), null);
  }
});

test("every rule is named, has a reason, and is stateless across calls", () => {
  assert.ok(Array.isArray(DESTRUCTIVE_RULES) && DESTRUCTIVE_RULES.length >= 7);
  for (const rule of DESTRUCTIVE_RULES) {
    assert.ok(rule.name, "each rule carries a name");
    assert.ok(typeof rule.reason === "string" && rule.reason, "each rule carries a reason");
    assert.strictEqual(typeof rule.test, "function");
  }
  // The old regex pile needed a "no g flag" guard (.test with g is
  // stateful); rules are plain functions, and statelessness is probed
  // directly: the same words must get the same verdict on a second run.
  const firing = "sudo rm -rf /".split(/\s+/);
  for (const rule of DESTRUCTIVE_RULES) {
    assert.strictEqual(rule.test(firing), rule.test(firing), rule.name);
  }
});

test("non-string input is never destructive", () => {
  assert.strictEqual(isDestructiveInput(undefined), null);
  assert.strictEqual(isDestructiveInput(null), null);
  assert.strictEqual(isDestructiveInput(""), null);
});
