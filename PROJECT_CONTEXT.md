# AI Clipper — Project Context

> This file is the handoff/source-of-context for coding models and reviewers working on this repository.
> Read this file together with `AGENTS.md` and the actual current `main` branch. The code is always the final source of truth if this document becomes stale.

## 1. Project goal

**AI Clipper** is a Windows-first, local-first application for processing very long streams and videos.

Long-term pipeline:

`long local video/stream recording -> local transcription -> candidate moment discovery -> ranking -> human review -> later clip creation/export`

Typical source material can be multi-hour gaming or chatting streams. Source files may be very large (potentially 100–400+ GB), so the architecture must avoid copying or loading entire videos into memory.

The core privacy rule is strict:

- Full local videos must stay local.
- Full local audio must stay local.
- External LLMs must never receive the full video or full audio.
- Cloud/LLM use, if added later, should be optional and text-only, using only a small shortlist of transcript excerpts.
- Never expose API keys, credentials, `.env` files, private paths, or user media.

The project should remain lightweight. Do **not** rewrite it into React/Node or add Redis, a vector database, a queue service, or other infrastructure unless there is a demonstrated need.

---

## 2. Current local environment

Primary development/runtime environment:

- OS: Windows
- Project path: `D:\\AI-Clipper`
- Python: 3.12.x
- Backend: FastAPI
- Frontend: plain HTML/CSS/JavaScript
- FFmpeg: 9.x full build
- ffprobe: available
- yt-dlp: available
- whisper.cpp: local Windows binary
- Whisper backend currently used: CPU
- Whisper threads: 8
- Current Whisper model: multilingual `ggml-base.bin`
- GPU: AMD Radeon RX 6700 XT
- CUDA/NVIDIA is not available and must not be assumed
- Vulkan/GPU acceleration is **not implemented yet** and is a separate future optimization

Local-only assets that are intentionally not committed to Git include environments, models, binaries, temporary media, caches, and generated analysis. The repository `.gitignore` should continue excluding those categories.

Known local Whisper paths:

- executable: `D:\\AI-Clipper\\tools\\whisper\\whisper-cli.exe`
- default model: `D:\\AI-Clipper\\models\\whisper\\ggml-base.bin`

These local binary/model paths may not exist in a cloud checkout and must not be treated as repository files.

---

## 3. Current application architecture

### Backend

`app.py` contains the FastAPI application and the local transcription workflow.

The backend currently provides:

- localhost web application/API
- local file inspection
- URL metadata inspection
- Whisper model discovery
- transcription start/status/cancel endpoints
- transcript cache/recovery logic
- browser/security middleware

### Native Windows picker

`picker.py` opens a native Windows file-selection dialog in a separate process and returns the selected local path.

The application uses an in-memory allowlist of paths selected through the picker before permitting local inspection/transcription.

### Frontend

The browser UI is intentionally simple:

- `static/index.html`
- `static/app.js`
- `static/style.css`

It uses plain JavaScript and polls transcription status periodically.

### Launchers / support files

The project also includes launch/support files such as:

- `run.py`
- `start.bat`
- `requirements.txt`
- `AGENTS.md`
- `test_phase2.py`

Do not replace the current launch or framework structure without a strong reason.

---

## 4. Phase 1 — complete

Phase 1 is implemented and working.

Capabilities:

- native Windows local-video selection
- local media path returned without uploading or copying the video
- ffprobe metadata inspection for local files
- yt-dlp metadata-only inspection for supported YouTube/Twitch/TikTok URLs
- URL metadata inspection without downloading the full source video
- local FastAPI UI
- dark browser interface
- system/tool status

The application must continue to avoid copying huge local source videos.

---

## 5. Phase 2 — complete and working

Phase 2 implements local Whisper transcription.

Current pipeline:

`source video -> FFmpeg time-range extraction -> temporary mono 16 kHz WAV -> whisper.cpp -> JSON -> original-timeline transcript segments -> local cache -> temp cleanup`

### Chunking

- Long videos are processed sequentially.
- Current chunk size: 20 minutes (`1200` seconds).
- FFmpeg reads only the current time range.
- The application does not create one giant WAV for the entire source.
- Normal processing needs only one chunk-sized temporary WAV at a time.

### Whisper

Current configuration:

- `whisper-cli.exe`
- multilingual `ggml-base.bin`
- CPU backend
- 8 threads
- selectable language: auto / Russian / English / German
- model selection is exposed in the UI

### Transcript timestamps

Whisper returns chunk-relative timestamps.

The application adds the chunk start offset exactly once so each stored transcript segment uses the original source-video timeline.

### Cache / resume

Transcript progress is stored under `analysis/`.

Cache identity is based on:

- source fingerprint
- Whisper model
- language

The source fingerprint currently derives from:

- absolute source path
- file size
- modification time

Typical cache naming is similar to:

`transcript-<fingerprint12>-<model-name>-<language>.json`

Partial completed-chunk progress is intended to be resumable.

Completed caches should be reused instead of retranscribing the source.

### Safe temporary names

A Windows bug was fixed where temporary filenames inherited the source filename, including hidden/non-ASCII Unicode characters.

Temporary transcription files now use ASCII-safe fingerprint-derived names, for example:

`transcribe-8ce05460c2c8-chunk-0000.wav`

Do not reintroduce the source filename/stem into internal temporary filenames.

### Finished job retention

Another bug was fixed where the background worker finished or failed and `_active_job` was immediately discarded before the UI could read the final error/state.

The application now retains the most recent finished job (`_last_job`) so the UI can still observe success/failure/cancel state.

### Current UI features

Phase 2 currently includes:

- Transcribe
- Cancel
- Resume
- Re-transcribe
- progress/status display
- transcript rendering
- transcript search
- language selection
- model selection
- local cache reuse

Progress is currently mostly chunk-granular. A long chunk may remain at the same percentage until the chunk finishes. Do not confuse that limitation with a worker failure.

---

## 6. Verified real-world Phase 2 result

A real ~30.67 second Russian local MP4 was tested end-to-end.

Observed local result:

- FFmpeg audio extraction: about `0.03 s`
- whisper.cpp `base`, CPU, 8 threads: about `3.5–3.6 s`
- output: 7 transcript segments
- Russian text displayed correctly
- timestamps displayed correctly
- JSON saved correctly
- browser UI reached 100% successfully

The same source also succeeded through a manual PowerShell pipeline:

`FFmpeg -> ASCII temp WAV -> whisper-cli -> JSON`

This established that FFmpeg, the local Whisper binary, the model, Russian transcription, and the basic CPU pipeline work on the current Windows machine.

Existing Phase 2 verification suite result at the time of this handoff:

`8/8 passed`

This is not yet equivalent to a full unattended 4–8 hour integration test.

---

## 7. Known review findings that are NOT yet considered resolved

A later code review found several concrete Phase 2 hardening issues. Treat these as the priority before implementing Phase 3 unless the current code has already changed and you verify they are fixed.

### Critical / highest priority

1. **Single-job admission race**
   - The active-job check and active-job assignment were observed in separate lock acquisitions.
   - Two nearly simultaneous transcription requests may both start.
   - Admission/reservation should be atomic under one lock.
   - A finishing worker should clear `_active_job` only if it still owns that slot.
   - Unique per-job temp isolation is desirable as extra protection.

2. **Non-atomic transcript checkpoint writes**
   - Checkpoints were written directly with `"w"`, truncating the existing file before the replacement was safely complete.
   - A crash, disk-full error, or interrupted write can destroy the previous valid resume point.
   - Write a complete sibling temporary JSON file, flush it, then atomically replace the destination (for example using `os.replace` on Windows).
   - A re-transcription should not destroy a previous completed transcript merely because the new attempt has only completed its first chunk.

3. **UI state-transition problems**
   - Re-transcribe may disappear after successful completion.
   - A rejected start may leave action buttons hidden.
   - A failed job with partial progress may appear only as resumable and hide the real error.
   - Selecting another source may leave the previous transcript/progress visible.
   - A stale polling response from an old source/job must not overwrite the newly selected source UI.

4. **Cache variant / Resume identity**
   - Status/Resume must unambiguously target the exact source + model + language variant.
   - The UI must not display one saved cache variant and resume a different model/language combination.
   - A completed-cache hit must report the actual model/language rather than defaults.

### Important hardening after the critical fixes

- Use one coherent job-state snapshot protected by the same lock for both readers and writers.
- Distinguish processed progress from durably persisted progress.
- Keep chunk-boundary cancellation semantics clear.
- Check cancellation between FFmpeg and Whisper stages.
- Avoid suppressing real exceptions during cleanup/cancellation.
- Make subprocess timeouts configurable rather than relying on one fixed value.
- Add controlled shutdown/orphan-temp cleanup.
- Validate cached counters/duration/segment structure more strictly.
- Version cache provenance so future engine/model/pipeline/chunking changes can invalidate incompatible caches.
- Consider including model-content identity rather than only model filename.
- Handle missing/moved source files without unexpected 500s.
- Apply local-protocol restrictions during local extraction if practical.
- Make health/preflight checks verify Whisper executable and model availability.
- Avoid returning the entire accumulated transcript every ~1.2 seconds forever on very large transcripts.
- Debounce or otherwise optimize transcript search for very large segment counts.
- Consider speech-boundary overlap/context for adjacent chunks only after measuring the quality impact.
- Prefer finalized recordings; the current fingerprint is not a full content hash and is not designed for files still being recorded.

Do not claim any item above is still broken without checking the current code first.

---

## 8. Phase 3 — intended direction, not yet implemented

Phase 3 should be a **separate analysis layer over durable transcripts**.

Do not start by sending hours of transcript/audio/video to a cloud model.

Recommended architecture:

`durable local transcript -> local segmentation -> local candidate windows -> local scoring/filtering -> overlap/deduplication -> optional small text-only LLM rerank -> ranked candidates for human review`

### Local candidate discovery

Candidate windows should be built from transcript segments using original-video timestamps.

Prefer sentence/topic-aware overlapping windows with enough context for setup and payoff rather than arbitrary fixed clips.

Potential local signals may include:

- strong opening/hook
- self-contained idea/story
- surprise
- disagreement
- humor
- emotional language
- clear payoff
- speech density
- low repetition
- adequate transcript quality

Start with transparent, explainable heuristics before adding more models.

Later audio features such as energy changes, pauses, speech density, or laughter detection may help, but loudness alone is not a definition of “interesting”.

### Candidate records

Keep candidate analysis separate from the transcript cache and version it.

A useful candidate record may contain:

- stable candidate ID
- source/transcript revision identity
- start/end timestamps
- source segment IDs
- local score components
- reasons/features
- analysis configuration/version
- optional LLM evaluation
- user accepted/rejected state

Atomic JSON is sufficient initially. SQLite is optional later if searchable history/variants justify it. Redis/vector databases are unnecessary at this stage.

### Optional cloud LLM use

Cloud LLM use should be **off by default**.

If enabled, send only a bounded shortlist of text excerpts and minimal surrounding context.

Never send:

- full video
- full audio
- full transcript
- local filenames/paths unless explicitly necessary
- secrets/credentials

Use anonymous candidate IDs and structured bounded responses.

Cache LLM evaluations by excerpt hash + prompt version + model + scoring settings.

Use explicit user-visible budget/token limits.

“Viral” is only a ranking hypothesis; the system should rank clip-worthiness, not promise virality.

---

## 9. Coding constraints / invariants

When modifying this repository:

- Read the actual current code first.
- Treat this document as context, not as a substitute for code inspection.
- Preserve FastAPI + plain HTML/CSS/JavaScript unless there is a proven reason not to.
- Keep source media local.
- Never upload or copy full local videos.
- Never create a full-video WAV for multi-hour sources.
- Keep temporary media bounded and clean it up.
- Preserve resume/recovery behavior.
- Preserve original-video timestamp correctness.
- Keep temp filenames ASCII-safe.
- Never assume NVIDIA/CUDA.
- Do not implement Vulkan unless it is the explicit task.
- Do not implement Phase 3 while working on Phase 2 hardening.
- Do not mix unrelated refactors into a bug fix.
- Keep cloud AI optional and text-only.
- Never hard-code secrets or tokens.
- Do not commit/push unless explicitly requested.
- Prefer small, testable changes over large rewrites.

---

## 10. Testing expectations

For normal code changes:

- run Python syntax/import checks
- run the Phase 2 regression suite
- add focused regression tests for bugs being fixed
- avoid expensive long-video transcription unless explicitly requested
- do not modify user media
- keep private test media outside Git

Useful future tests include:

- competing/simultaneous transcription starts
- failed/interrupted atomic checkpoint replacement
- source change while a poll is in flight
- failed + resumable UI state
- exact cache-variant Resume behavior
- completed-cache reuse
- synthetic chunk-boundary extraction
- actual opt-in Windows integration test
- representative full 20-minute speech benchmark
- later, a 4–8 hour unattended resume/recovery test

---

## 11. Current development priority

The intended order is:

**A. Harden Phase 2 correctness and recovery**

Fix the known critical concurrency, atomic-write, UI-state, and cache-variant issues first.

**B. Validate long-run behavior**

Benchmark a representative 20-minute speech chunk and perform targeted recovery/integration tests.

**C. Build Phase 3**

Add local transcript-based candidate generation, scoring, deduplication, candidate review UI, and only then optional budget-capped text-only LLM reranking.

**D. Later optimizations**

GPU/Vulkan acceleration, advanced audio/visual features, and rendering/export should remain separately scoped work.

---

## 12. Guidance for external reviewers / coding models

When reviewing this repository:

1. Start with `PROJECT_CONTEXT.md`, `AGENTS.md`, the repo tree, `app.py`, `static/app.js`, `static/index.html`, `static/style.css`, and `test_phase2.py`.
2. Verify claims against the actual current `main` branch.
3. If you identify a bug, point to the exact file/function/logic involved.
4. Do not invent missing problems or claim a review item is unresolved without checking current code.
5. Prefer precise patches and regression tests.
6. Keep local-first privacy and multi-hour scalability as non-negotiable requirements.

The goal is not to make the application architecturally fashionable. The goal is to make a small Windows local tool reliable, resumable, efficient, private, and eventually good at finding clip-worthy moments in very long recordings.
