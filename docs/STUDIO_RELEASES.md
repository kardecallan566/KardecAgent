# Studio release process

## Versioned Windows installer

1. Update the same version in the Tauri config, Cargo package/lockfile, and npm package/lockfile.
2. Merge the release-ready change to the release branch.
3. Create and push a tag matching the Tauri version, for example `studio-v0.1.1`.
4. The **Publish Studio release** workflow validates the tag/version and publishes the Windows NSIS and MSI installers to GitHub Releases.

## Installing over an existing copy

The Windows NSIS installer is configured for the current user, matching Tauri's default installation scope, and downgrades are disabled. Re-run the newer installer with the same Windows account and installation scope. The app keeps the same Tauri identifier, so Windows treats it as the same application. Project folders are external to the app installation and are not removed by an app upgrade.

Do not switch an existing installation between per-user and per-machine scope as part of a routine update. That is a separate migration.

## In-app automatic updates

Tauri's updater requires a signing key pair. The public key belongs in the app configuration and the private key must be stored as a protected GitHub Actions secret. Never commit or upload the private key. The updater is deliberately not enabled until a permanent signing key is provisioned; unsigned update downloads would weaken the application's update security.
