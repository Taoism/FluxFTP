# FluxFTP user guide

## Connections and diagnostics

Use Tools → Site Manager to save connections. FTP is unencrypted; FTPS and SFTP
provide encrypted transports with certificate/host-key verification. Trust a server
through a normal interactive connection before unattended automation.

Tools → Connection Diagnostics tests DNS, TCP/proxy establishment, authentication
and directory/data access. Choose configured, passive or active FTP mode without
changing the saved site. Tests list folders; they do not upload files or prove write
access. Active-mode tests may create the normal temporary UPnP mapping. Exported
reports redact known credentials but can include hostnames and paths.

## Synchronization and watched folders

Tools → Synchronize Folders previews changes before queueing them. Review each
action, especially conflicts and destination-only deletions. Checksum comparison
requires a common supported native hash. Saved baselines are explicit scan
snapshots, not proof that a later transfer succeeded. Start the held queue to apply
approved actions; changed inputs can invalidate the preview.

Tools → Watch-and-upload Folders polls local files while the app is open. A file
must remain unchanged across its settle interval before upload. This does not prove
another application has closed it. Exclusions and conflict policies apply; links
and junctions are not followed. Start with a disposable destination to check a rule.

## Bandwidth management

Tools → Unified Bandwidth Management sets combined and directional limits in
KiB/s. Zero means unlimited. A daily schedule uses local time, can cross midnight,
and applies all day when its start/end are equal. Enabling shared limits overrides
legacy site limits and local-network bypass. Desktop and standalone automation
honor the settings, but separate processes do not coordinate their budgets.
Direct FXP/provider-side copies do not send their payload through this computer.

## Verification reports

Tools → Transfer History exports selected or filtered verification reports to
JSON or CSV. Each file includes its outcome and available verification evidence:
algorithm, expected/actual values, mismatches and recording problems. Size-only
checks remain distinct from checksums. Old history may have no recorded evidence.
Exports do not re-read files. Export before clearing history or its retention period
expires; report paths may be sensitive when shared.

## Deployment profiles and rollback

Tools → Deployment Profiles saves a local folder, destination site/folder,
exclusions and verification policy. Preview reads both sides without writing.
Approve a plan, then start its held Transfer Queue entry. Existing changed files
are backed up locally before overwrite; destination-only files and empty folders
are retained. Checksum readback prefers native SHA-256 and otherwise downloads the
file for verification. Size-only mode does not establish content equality.

Preview rollback selects a retained revision. It checks backup integrity and current
remote content before restoring old files or deleting files created by that
deployment. Later remote edits block rollback. Ambiguous interrupted writes require
manual review; deployment revisions are not automatically replayed. Rollback
restores content, not permissions or original timestamps. Remote folders remain.

Journals and backups live in `deployments` beside the configured `sites.json`.
They consume local space and remain until manually removed. Keep them for as long
as rollback is needed. Deployment and sync profiles are not included in the app's
configuration-backup exports. Deployments are exclusive within the app's queue,
but cannot lock out external writers and are not atomic website releases.

## Changes in 0.117.0

- Tools → Certification Checker reports TLS certificate validity, fingerprints,
  negotiated security and CA/hostname versus saved-pin trust. SSH reports verified
  host-key fingerprints; unknown keys are not saved. Reports export to JSON.
  Revocation and organizational certifications are outside this check.
- Queue and context-menu controls hold selected jobs, pause native payloads at
  progress checkpoints, resume selected jobs, or stop selected/all transfers.
  Queue pause remains between files. Active FXP, cloud and deployment operations
  cannot be paused mid-operation; queued jobs can still be held.
- SCP · SSH transfers payloads with legacy SCP over verified SSH. Browsing and
  metadata require SFTP; the server must also provide scp and a POSIX-compatible
  shell. Passwords, SSH keys and managed identities are supported. Nested folders
  use the ordinary queue. SCP does not support byte-offset resume; choose Overwrite
  or SFTP. Downloads publish only after checked completion.
