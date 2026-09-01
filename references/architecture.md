# Independent engine architecture

Read this reference when debugging precision, ranking, cache identity, or image-budget accounting.

## Pipeline boundaries

1. `transcript.py` acquires text and metadata before video.
2. `candidates.py` converts transcript-linked targets into bounded source-time windows.
3. `media_backend.py` acquires only required media ranges for long URL sources and preserves source-time mapping.
4. The candidate engine performs low-resolution scans, ranks evidence states, extracts 512px candidates, deduplicates, applies the budget, and audits coverage.
5. `grab.py` resolves selected IDs back to source media and verifies the second decode against candidate pixels.
6. `render.py` joins transcript provenance, prose, assets, and chapters into a validated manifest and deterministic HTML.

No stage uses a model-generated timestamp or model-generated asset path.

## Bounded grayscale scan

The engine does not run a global scene detector. For each visual target it asks FFmpeg for a small stream of 64×36 grayscale frames at 2 fps in light mode or 5 fps in advanced mode. A five-second light window therefore produces roughly 23 KB of raw pixels before Python object overhead.

For adjacent samples it computes:

- mean absolute luma change;
- edge-plane change;
- changed-pixel ratio;
- active-tile ratio, which prevents a small menu, button, code line, or status badge from disappearing in a global average.

Local motion thresholds use the window median and median absolute deviation. This makes a mostly static slide and a fast UI demo use different transition gates without a global magic threshold.

## Action-result state machine

For `action_result` targets:

1. clamp samples to the period after the action anchor;
2. identify the strongest local transition;
3. reject blank/extreme frames;
4. find the first sample whose incoming and outgoing motion fall below the local stability gate;
5. rank stable alternatives by quality and delay from the transition.

This selects the completed state rather than the click, loading frame, fade, or first incomplete paint. A recovered post-transition state is labeled `recovered` in provenance.

## Quality and semantic deduplication

The compact signature stores grayscale pixels, an edge plane, mean luma, contrast, sharpness, blank status, and SHA-256 digest. Near-duplicate clustering is scoped to one chapter and one target set; visually similar frames proving different transcript targets cannot remove one another.

An additional exact-pixel pass coalesces hard duplicates inside the same chapter and unions their target/segment provenance. This lets one asset satisfy multiple targets when the pixels are truly identical, while near-but-not-identical states remain separate. Hard duplicates across chapters are retained for fail-closed review because one timestamp cannot truthfully belong to two chapters.

Within a cluster, representation is selected by evidence protection, recovery status, non-blank quality, sharpness/contrast, and post-action delay. The earliest frame has no automatic preference.

Candidate IDs are derived from chapter, sorted target IDs, six-decimal decoded time, and fingerprint. Identical inputs and pixels produce identical IDs across reruns.

## Fail-closed budget allocation

The global cap is applied after extraction and deduplication:

1. reserve the strongest representative for every target;
2. reserve chapter coverage where no explicit target exists;
3. spend remaining capacity on the highest-utility distinct alternatives;
4. recompute chapter and target coverage.

Missing evidence remains `unresolved`. The renderer additionally requires every covered target to be represented in `selections.json`; candidate coverage cannot silently become HTML omission.

## Time truth

Every media part declares `source_start`, `media_start`, duration, and source frame duration. Candidate extraction requests source time, maps it to media time, decodes through FFmpeg with timestamps preserved, parses the decoded PTS, maps it back to source time, and rejects drift beyond the larger of 100 ms or 2.5 source frames.

Selected assets are decoded again at manifest `actual_t`. Their low-resolution signature must match the candidate before crop and scaling. This converts a timestamp claim into a pixel-verified claim.

## Cache truth

The media cache key hashes schema version, source URL or resolved path, local file size and nanosecond mtime, requested ranges, and exact-cut setting. Downstream code accepts only schema version 3 part manifests. Unknown historical shapes fail instead of being interpreted heuristically.

## Image-read accounting

The metric denominator is the former 60 individual 512×288 frame baseline. The numerator includes:

- actual strip pixel area; and
- one 512×288 verification read for every evidence group, including singleton groups.

It is a conservative, provider-neutral comparison. It is not a token or price quote.

On the cached 18:15 validation video, light mode produced 24 candidates, seven multi-frame strips, complete chapter/target coverage, and a projected 34.2% image-read area. The preceding v2 implementation produced 36 candidates; the original baseline was 60.

## Runtime dependencies versus code provenance

FFmpeg/FFprobe decode media, yt-dlp requests public metadata/media, and Python's standard library implements orchestration, parsing, ranking, HTTP fallback, manifests, and HTML. These are runtime tools and protocols, not imported video-skill code. See [provenance.md](provenance.md) for the source audit boundary.
