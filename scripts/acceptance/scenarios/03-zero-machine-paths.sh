# Scenario 3: zero-machine-paths (AT-11 slice 3; level V1+V2, activates M1).
#
# Static half: scan the versioned tree under test (CHECKOUT) for
# machine-coupling patterns across the installation surface. Authored at
# task 1.3; task 2.2 repaired the brain launcher and manifest and task 2.4
# replaced the static systemd unit with a template, which is what turns
# this scenario green. Any finding below is a truthful FAIL, never a skip.
#
# Pure bash on purpose: the sandbox PATH allowlist has no grep/sed/find.
# Test-fixture files are deliberately NOT scanned — the smoke suite's decoy
# paths are data proving the bootstrap ignores them, not install behavior.

MACHINE_PATTERNS=(
  '\.dotfiles'                                        'maintainer dotfiles path'      # hygiene-exempt: pattern-definition
  '/home/linuxbrew'                                   'literal brew prefix'           # hygiene-exempt: pattern-definition
  'tail2640fd\.ts\.net'                               'personal tailnet domain'       # hygiene-exempt: pattern-definition
  '~/Code/personal/(herdr-tts|herdr-brain|agent-tts)' 'maintainer clone path'         # hygiene-exempt: pattern-definition
  '\$\{?HOME\}?/Code/personal/'                       'maintainer clone path'         # hygiene-exempt: pattern-definition
)

# Explicit files plus directory globs; missing files are skipped so this
# scenario stays valid as the tree layout evolves (e.g. deploy/*.tmpl).
SCAN_FILES=(
  "$CHECKOUT/hosts/herdr/tts-plugin/scripts/install.sh"
  "$CHECKOUT/hosts/herdr/tts-plugin/scripts/bootstrap.sh"
  "$CHECKOUT/hosts/herdr/tts-plugin/bin/herdr-tts"
  "$CHECKOUT/hosts/herdr/brain/bin/herdr-brain"
  "$CHECKOUT/hosts/herdr/brain/herdr-plugin.toml"
  "$CHECKOUT/hosts/herdr/tts-plugin/README.md"
)
for g in \
  "$CHECKOUT"/hosts/herdr/brain/deploy/* \
  "$CHECKOUT"/hosts/herdr/tts-plugin/packaging/*/* \
  "$CHECKOUT"/hosts/herdr/tts-plugin/packaging/*/*/*
do
  [[ -f $g ]] && SCAN_FILES+=("$g")
done

scanned=0
findings=0
for f in "${SCAN_FILES[@]}"; do
  [[ -f $f ]] || continue
  (( scanned++ ))
  lineno=0
  while IFS= read -r line || [[ -n $line ]]; do
    (( lineno++ ))
    for (( p = 0; p < ${#MACHINE_PATTERNS[@]}; p += 2 )); do
      if [[ $line =~ ${MACHINE_PATTERNS[p]} ]]; then
        bad "machine coupling: ${f#"$CHECKOUT"/}:$lineno — ${MACHINE_PATTERNS[p + 1]}: ${line:0:80}"
        (( findings++ ))
        break
      fi
    done
  done < "$f"
done

if (( scanned == 0 )); then
  bad "zero-machine-paths: no files scanned — the installation surface globs are broken"
elif (( findings == 0 )); then
  ok "zero-machine-paths: ${scanned} installation-surface files carry no machine-coupling pattern"
fi
