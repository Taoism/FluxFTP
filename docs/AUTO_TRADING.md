# Auto trading

## Scope

Auto trading discovers folders on saved FTP/FTPS/SFTP sites and transfers
eligible source folders to saved destination sites. A durable ledger records
manual, polling and IRC submissions through one state machine. The global
trading switch is off by default; configuration is previewable before it is
enabled. All transfers use FluxFTP's queue and saved-site credential vault.

The user-defined terms are:

- **Cowboy population:** manually submit an explicit source folder to a route.
- **Rational population:** discover folders from configured source rules.
- **“Same as previous”:** no additional behavior is required.

IRC event sources are FluxFTP's built-in IRC client, HexChat and AdiIRC. The
three sources share matching, validation, de-duplication and ledger processing.
HexChat and AdiIRC integrations forward events to FluxFTP over a loopback-only,
authenticated bridge. The external clients remain optional.

## Configuration

`.fluxftprc` is a UTF-8 TOML file in the FluxFTP data folder. It contains
bookmarks by saved-site name, remote paths, glob filters and policy only; it
never contains site passwords, proxy passwords, IRC passwords, or bearer
tokens. Unknown keys, missing references, invalid paths, invalid regular
expressions and unsupported policies reject the configuration without
replacing the last valid configuration.

Each route explicitly pairs one configured source root with one destination
root. Each root has its own filters; overlapping roots are evaluated as
separate routes. Paths must be absolute remote paths without `..`, control
characters, or empty components. Within a rule, a blacklist always wins. If
the whitelist is empty, all names are eligible; otherwise a name must match at
least one whitelist glob and no blacklist glob. Globs match one path component;
`*` and `?` do not cross `/`.

The proposed schema is:

```toml
[trading]
enabled = false
poll_seconds = 10
stable_polls = 3
max_depth = 64
max_entries = 100000
max_active_trades = 2
max_retries = 5
retry_backoff_seconds = [30, 120, 600, 1800, 3600]
terminal_retention_days = 90
auto_wipe = false

[[sources]]
id = "siteA-incoming"
bookmark = "siteA"
path = "/incoming"
whitelist = ["*"]
blacklist = ["*.partial", ".*", "incomplete"]
completion_marker = ".fluxftp-complete" # optional; if set, required in each candidate folder

[[destinations]]
id = "siteB-archive"
bookmark = "siteB"
path = "/archive"
whitelist = ["*"]
blacklist = [".*"]
conflict = "fail" # fail | skip | rename
verify = "size-and-mtime" # size-and-mtime | size

[[routes]]
id = "A-to-B"
source = "siteA-incoming"
destination = "siteB-archive"
enabled = true

[[irc.networks]]
id = "trade-net"
host = "irc.example.net"
port = 6697
tls = true
nickname = "fluxftp-trader"
username = "fluxftp-trader"
channels = ["#trades"]
credential_id = "irc-trade-net" # optional vault reference for IRC server password

[[irc.triggers]]
id = "release-message"
route = "A-to-B"
channels = ["#trades"]
senders = ["releasebot"]
pattern = "^!trade (?P<folder>[A-Za-z0-9][A-Za-z0-9._ -]{0,119})$"
```

Polling and IRC rules create the same kind of candidate. IRC patterns use
Python regular expressions with named capture `folder`; patterns are bounded
to 512 characters, run against messages limited to 2048 characters, and may
not use nested/unbounded repetition constructs. Network, channel, sender and
route are allowlisted. Private messages and notices are ignored by default.

## Detection and identity

Each poll lists only the configured source directory first. Candidate folder
names are validated as one path component and filtered before a bounded
recursive manifest scan. Symbolic links, unsupported entry types, excessive
depth, and entry-count overflows make a candidate ineligible and produce a
reason in the preview/log. The manifest contains relative path, type, size and
server modification value; its canonical digest identifies that exact source
revision. When configured, a completion marker must be present in the folder.
Otherwise the same manifest must be observed on `stable_polls` consecutive
successful polls. This is a stability heuristic, not proof that a remote
uploader has finished; sites that support a completion marker should use it.

A trade revision is unique by route, canonical source bookmark identity, full
source path and manifest digest. Repeated polls and duplicate IRC events resolve
to the existing record. A source change before transfer replaces the pending
manifest and restarts its stability count; a change after a terminal result
creates a new revision. A rename is a new path and a new trade. A source folder
that disappears before transfer is cancelled with a recorded reason. Deletion
after completion never deletes the destination or ledger history.

## Ledger and lifecycle

`trade.DB` is a SQLite database in the FluxFTP data folder, separate from
`sites.json`. It stores random stable trade IDs, route/source/destination
bookmark names and paths, trigger metadata, manifest digest, lifecycle state,
timestamps, retry counters, queue job ID, last error, and state-transition
history. It never stores credentials or raw IRC message text. IRC event IDs are
hashed; an IRC message without a server message ID uses a bounded deduplication
key over network, channel, sender, timestamp and message digest.

Schema migrations run transactionally and create a SQLite online backup before
changing the schema. SQLite uses foreign keys, WAL and `synchronous=FULL`. A
process lock prevents concurrent writers from multiple FluxFTP instances.
Terminal rows are retained for 90 days by default. Optional automatic wiping
removes only terminal rows and their event/history rows after the configured
retention; it never wipes pending or active work. The database is not encrypted
because it holds no credentials; it is created with owner-only file permissions
where supported and inherits the FluxFTP data-folder ACL. SQLite secure-delete
is enabled as a best effort, not claimed as guaranteed media erasure.

States: `detected`, `waiting_stability`, `eligible`, `queued`, `transferring`,
`verifying`, `complete`, `failed`, `paused`, `cancelled`, `rejected`.
Every transition and reason is durable. On restart, candidates return to
`waiting_stability`; queue-linked work is reconciled against the persisted
queue. An interrupted active queue transfer is not blindly duplicated: it is
paused for explicit recovery unless FluxFTP can prove the queue job is safely
resumable. Retrying a failed trade requires the prior queue job to be terminal
and respects the configured retry count/backoff.

## Destination and retry policy

The default is `fail` when the destination folder already exists. `skip` marks
the trade complete only when the destination is verified to contain the same
manifest; otherwise it fails. `rename` picks a unique suffixed destination
folder and records the resolved path. No policy silently deletes destination
content. Every file is checked after transfer using the strongest metadata
available from the server (relative path, entry type and size, with optional
modification-time comparison). FluxFTP's normal transfer checks still apply.
A partial transfer remains resumable
through FluxFTP's queue. Retries are bounded, exponentially backed off from the
configured schedule, and stop at `failed` after the limit. Site outages do not
block the UI or polling of other sources.

The worker limit defaults to two active trades, subject to FluxFTP's existing
per-site connection limits and bandwidth controls. Automatic jobs enter the
normal transfer queue below user-submitted jobs. The global queue pause also
pauses auto trades. A paused trade can be resumed or cancelled from its row.

## Controls and visibility

Tools → Auto Trading opens status, rules, preview, history, and controls. The
preview shows source folder, selected route/destination, matched filters,
readiness, stability count, and rejection reasons. Config errors prevent
enabling trading. Controls include global enable/disable, per-route pause,
one-shot scan, manual (cowboy) submit, retry, pause, cancel, and retention
settings. Logs record rule IDs and decision reasons, but omit passwords, bridge
tokens and raw IRC messages.

## IRC adapters

FluxFTP's built-in client connects with TLS by default, registers an instance
nickname, joins only configured channels, responds to PING, handles reconnect
with bounded exponential backoff, and watches allowlisted message events. An
optional credential reference in the vault supplies the IRC server password;
the password is sent only after TLS connects. SASL is not currently supported.
Disconnect does not discard accepted events.

Use **IRC adapters…** in FluxFTP's Auto Trading window to export ready-to-load
scripts containing a dedicated local bridge token. Keep those files private.
HexChat uses the supported Python interface to hook server `PRIVMSG` events.
AdiIRC uses its scripting `on TEXT` event and URL-encodes query values. Each adapter preserves network,
channel, sender and event identity and forwards one bounded event to the
authenticated loopback bridge; it never performs transfers.
Duplicate, stale, malformed or untrusted events are rejected by FluxFTP.

## Acceptance scenarios

1. A new source folder becomes stable across three polls (or has its configured
   completion marker), matches source and destination filters, previews as
   eligible, and produces one queue job when enabled.
2. A changing or partial folder remains ineligible; a link, unsafe name, scan
   limit or malformed listing is rejected with a reason.
3. Repeated polls, repeated bridge posts, duplicate IRC message IDs, and an app
   restart do not produce duplicate queue jobs.
4. FluxFTP built-in IRC, HexChat and AdiIRC events are accepted only from the
   configured network/channel/sender and only when a trigger regex maps to a
   valid route and source folder.
5. Destination conflict policies fail, safely skip verified identical content,
   or choose and record a unique renamed destination; no policy deletes files.
6. A partial transfer retries within the limit and reaches a terminal failure
   after the final unsuccessful attempt; a later manual retry is visible.
7. Restart preserves state transitions, queue linkage, paused work, retry
   schedule and deduplication records without implicitly restarting an
   uncertain active transfer.
8. Preview/dry run never submits work. Global disable and per-route pause
   prevent new automatic submissions while retaining all ledger records.

## Upstream adapter references

- [HexChat Python interface](https://hexchat.readthedocs.io/en/latest/script_python.html)
- [AdiIRC scripting and events](https://wiki.adiirc.com/projects/adiirc/wiki/Scripting)
- [AdiIRC On TEXT event](https://wiki.adiirc.com/projects/adiirc/wiki/On_TEXT)
