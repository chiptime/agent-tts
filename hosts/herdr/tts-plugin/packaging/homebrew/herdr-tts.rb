# Homebrew formula for herdr-tts — Neural TTS voice notifications for Herdr agents.
#
# Honest shape: Homebrew clones the agent-tts MONOREPO at the pinned tag
# (git url + revision), only the plugin subdirectory (hosts/herdr/tts-plugin)
# is staged into the keg, and the plugin's OWN scripts/bootstrap.sh builds the
# Python venv inside the prefix (bootstrap derives the venv location from
# XDG_DATA_HOME and installs the immutable-pinned agent-tts engine). A shim
# exposes bin/herdr-tts with HERDR_PLUGIN_ROOT pointed at the keg.
#
# Known tradeoff: runtime state that lives under XDG_DATA_HOME (audio history,
# podcast feed) also lands in the keg and is wiped by `brew upgrade` /
# `brew cleanup`. Machines that want user-space state should prefer the
# one-line installer instead (see packaging/homebrew/README.md).
#
# First-run onboarding is NOT available on this route: the shared wizard is a
# monorepo-root module, and the keg deliberately stages only the plugin
# subdirectory, so it can never reach monorepo-root tooling. Keg users
# configure the plugin through the CLI (see the project README).
class HerdrTts < Formula
  desc "Neural TTS voice notifications for the Herdr ADE"
  homepage "https://github.com/chiptime/agent-tts"
  url "https://github.com/chiptime/agent-tts.git",
      tag:      "v0.16.0",
      revision: "516db8f72ed834f0c09f9570ebbaf7c4d2a0d530"
  license "MIT"

  depends_on "python@3.12" => [:build, :test]
  depends_on "jq"

  def install
    # Stage ONLY the plugin subdirectory as the keg contents (the launcher
    # resolves lib/, scripts/ and the engine relative to its plugin root);
    # the rest of the monorepo stays out of the keg.
    prefix.install Dir["hosts/herdr/tts-plugin/*"]

    # Build the venv INSIDE the prefix: bootstrap.sh derives the venv path
    # from XDG_DATA_HOME and installs the pinned agent-tts engine. HERDR_TTS_DEV
    # is refused on public installs by bootstrap itself; keep it unset.
    ENV["XDG_DATA_HOME"] = prefix
    system "bash", "#{prefix}/scripts/bootstrap.sh"

    # Shim: pin the plugin root to the keg and keep XDG_DATA_HOME pointed at
    # it so the launcher finds the venv it was built with.
    (bin/"herdr-tts").write_env_script(
      "#{prefix}/bin/herdr-tts",
      HERDR_PLUGIN_ROOT: prefix.to_s,
      XDG_DATA_HOME:     prefix.to_s
    )
  end

  test do
    # Surface contract v1: prints the surface version without touching the
    # engine — no audio, no daemon, no network.
    assert_equal "1", shell_output("#{bin}/herdr-tts --contract-version").strip
  end
end
