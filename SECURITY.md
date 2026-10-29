# Security and privacy

DubSync Studio processes media locally and does not require an online account or upload media. The application invokes FFmpeg/FFprobe with argument arrays instead of interpolating media paths into a shell command.

## Video sample cache

The optional `video-samples.sqlite3` cache contains only perceptual video hashes
and timestamps (no decoded video frames or audio). It is stored in the local
XDG cache directory with owner-only permissions where supported and bounded
by count and disk size. A fresh sample of eleven distributed byte windows and
file metadata is checked before reuse, including a stability check across
decoding. This identity check is intentionally cheaper than reading and hashing
the complete media file; it is not a cryptographic guarantee against deliberate
same-metadata manipulation of unsampled bytes. Disable **Bildfingerprints
wiederverwenden** in performance settings if this threat is relevant.

Cached frames never make an episode eligible for export: video correspondence,
temporal coverage, identity checks and quality gates are recomputed per job.
The older alignment-result cache remains disabled for batch decisions.

## Media files and projects

- Source media is read-only from DubSync Studio's perspective; final exports are written to a separate output path.
- Existing exports are not overwritten unless a workflow explicitly allows replacement.
- Project files contain local source/output paths, language labels, and settings. They do not contain media payloads, but paths can reveal information about the user's filesystem. Keep project files private when sharing support materials.
- The local job history and logs may contain file paths and FFmpeg diagnostics. They are stored under the current user's state directory with restrictive permissions where supported.
- Project files are parsed with a size limit and schema validation. Do not open project files from untrusted sources if you do not trust their path references; project opening does not execute project contents.

## NAS and network source handling

- NAS paths are consumed as ordinary mounted filesystem paths. SMB/SFTP/WebDAV addresses are delegated to the desktop file manager so the desktop's authentication flow owns credentials.
- DubSync rejects credentials in the URI user-info portion and query/fragment fields; do not put passwords, access tokens, or keys into a share URI.
- Mount names, file paths, and server/share names may appear in the local job history and logs. Review them before sharing diagnostic exports.
- Network sources use conservative probe parallelism. This reduces load but cannot guarantee that an unavailable hard-mounted NFS/CIFS filesystem will never block at the operating-system layer.

## Reporting issues

Include the app version, `./run.sh --doctor --json` output and a concise reproduction. Review logs and reports for private paths before sharing. Never send original media unless you explicitly intend to share it.
