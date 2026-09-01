# Provenance boundary

## Ownership claim

The version 2.0 snapshot on `feat/independent-visual-evidence-engine-v3` is an original implementation owned by the repository owner. Its active source was written for this repository from the product requirements and behavioral tests in this branch.

It does not copy, import, vendor, install, execute, or call source code from another video-summary skill. It contains no runtime fallback to another skill directory or another skill's configuration.

## External tools

The implementation invokes these separately installed command-line tools through documented public interfaces:

- FFmpeg and FFprobe for media decoding and metadata;
- yt-dlp for public caption, metadata, audio, and video acquisition;
- optional Groq or OpenAI speech endpoints when the user has configured a key and has not disabled the fallback.

Python orchestration uses the standard library only. Calling a general-purpose tool or public HTTP API is a runtime dependency, not a source-code derivation claim.

## Independent implementation controls

- Candidate generation uses a repository-owned bounded grayscale scan and local median/MAD motion model.
- Action-result selection uses a repository-owned transition-to-stability state machine.
- Visual signatures, active-tile comparison, content-addressed IDs, semantic clustering, budget allocation, cache manifests, source-time mapping, verification, and rendering are implemented in this snapshot.
- Speech upload uses a repository-owned streaming `http.client` multipart implementation.
- The delivery branch is published as a parentless root commit so its branch history does not inherit earlier implementations from `main`.
- Tests use generated synthetic media and behavior contracts; no third-party video or skill source is committed.

On 2026-09-01, an exact normalized-line comparison of this snapshot against the public `bradautomates/claude-video` and `Newuxtreme/watch-video-skill` trees found no matching contiguous block of eight or more non-comment lines. The longest matches were five-line generic import or metadata-mapping sequences. This is a useful copied-block audit, not a legal originality determination.

## Honest limitation

Independent implementation does not mean the underlying problem or generic techniques are unique. Timestamped decoding, grayscale comparison, transcript alignment, contact sheets, and deduplication are established engineering concepts. The claim is limited to this code and architecture: there is no reused video-skill source in the delivered snapshot and no operational dependency on another video skill.

The repository's other branches or tags may contain earlier implementations with their own notices. They are outside this parentless branch and must retain their applicable provenance and license terms.
