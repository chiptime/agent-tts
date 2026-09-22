# Publishing `herdr-tts` to npm — maintainer steps

Publishing is a deliberate decision, not part of the release automation. The
wrapper is version-agnostic at runtime (it delegates to the GitHub tag), but
the published `version` should still track the released tag.

`herdr-tts` was unregistered on the npm registry as of 2026-09-23 (checked via
`npm view herdr-tts` → 404). Re-check right before publishing.

## Steps

1. **Align the version with the released tag** in `packaging/npm/package.json`:

   ```bash
   # after tagging vX.Y.Z
   jq --arg v "${TAG#v}" '.version = $v' packaging/npm/package.json > tmp.json && mv tmp.json packaging/npm/package.json
   ```

2. **Verify name availability and ownership**:

   ```bash
   npm view herdr-tts          # must 404, or be owned by you
   ```

3. **Dry-run and inspect the tarball** (must contain only `bin/` and `README.md`):

   ```bash
   cd packaging/npm
   npm publish --dry-run
   npm pack && tar -tzf herdr-tts-*.tgz
   rm herdr-tts-*.tgz
   ```

4. **Smoke the wrapper locally** (no publish needed):

   ```bash
   cd packaging/npm
   npm link            # puts the herdr-tts bin on PATH
   herdr-tts           # should print the delegation banner and run the installer
   npm unlink -g       # or: npm unlink -g herdr-tts
   ```

5. **Publish** (requires an npm account with `npm login` done; 2FA recommended):

   ```bash
   cd packaging/npm
   npm publish
   ```

6. **Verify**: `npx herdr-tts@latest` on a clean machine, then delete the
   installed plugin per the uninstall steps before repeating.

## If the name is taken

Fall back to a scoped package: rename to `@chiptime/herdr-tts` in
`package.json`, and note that users must then run `npx @chiptime/herdr-tts`.
Update the README usage block accordingly.
