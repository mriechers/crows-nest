# Video Backfill Analysis & Storage Cost Estimate

## Current State

- **63 items** processed through the pipeline
- **46 archived**, 10 summarized, 7 failed
- **~325MB** total in `~/Media/crows-nest/` (audio-only + metadata + transcripts)
- **Growth rate:** ~20 new items/month

## Content Type Breakdown

Based on the pipeline's content_type classification:

| Content Type | Typical Sources | Estimated Count | Avg Video Size | Avg Audio Size |
|-------------|----------------|:-:|:-:|:-:|
| `social_video` | TikTok, Instagram, X | ~30 | 20-80MB | 2-5MB |
| `youtube` | YouTube | ~15 | 200MB-2GB | 5-20MB |
| `podcast` | Apple Podcasts, Spotify | ~8 | N/A (audio-only) | 20-100MB |
| `web_page` | Articles, blogs | ~5 | N/A | N/A |
| `image` | Signal photos | ~5 | N/A | N/A |

Podcasts and web pages don't have video to backfill. Images are already preserved.

## Backfill Storage Estimate

| Category | Items | Est. Size Each | Total |
|----------|:-----:|:-:|:-:|
| Short-form video (TikTok, Reels, X) | ~30 | ~50MB | ~1.5GB |
| Long-form video (YouTube) | ~15 | ~500MB | ~7.5GB |
| **Backfill total** | **~45** | | **~9GB** |
| Existing audio + metadata | 63 | | ~325MB |
| **Post-backfill total** | | | **~9.3GB** |

## Ongoing Growth (with video preservation)

| Month | New Items | New Storage | Cumulative |
|:-----:|:---------:|:-----------:|:----------:|
| 1 | 20 | ~2.5GB | ~12GB |
| 3 | 60 | ~7.5GB | ~17GB |
| 6 | 120 | ~15GB | ~24GB |
| 12 | 240 | ~30GB | ~39GB |

Conservative estimate assumes mix of short-form (~50MB) and long-form (~500MB) at roughly 3:1 ratio.

## Cloud Storage Cost Comparison

### Cloudflare R2 (recommended)

| | Free Tier | Paid Rate |
|---|---|---|
| Storage | 10GB/month free | $0.015/GB/month |
| Class A ops (writes) | 1M/month free | $4.50/million |
| Class B ops (reads) | 10M/month free | $0.36/million |
| Egress | **Always free** | **Always free** |

**Your projected costs:**

| Timeframe | Storage | Monthly Cost | Notes |
|-----------|:-------:|:------------:|-------|
| Post-backfill (month 0) | ~9GB | **$0.00** | Under 10GB free tier |
| Month 3 | ~17GB | **$0.11** | 7GB over free tier |
| Month 6 | ~24GB | **$0.21** | |
| Month 12 | ~39GB | **$0.44** | |
| Year 1 total | | **~$2.50** | |

Operations cost is negligible at this volume (<100 writes/month, <50 reads/month).

### Alternatives Comparison (Year 1 TCO)

| Provider | Storage/GB/mo | Egress | Year 1 Cost | Retrieval Friction |
|----------|:------------:|:------:|:-----------:|:------------------:|
| **Cloudflare R2** | $0.015 | Free | **~$2.50** | None (instant) |
| **Backblaze B2** | $0.006 | Free (3x cap) | **~$1.50** | None (instant) |
| **AWS S3 Glacier Flexible** | $0.0036 | $0.09/GB | **~$5-8** | 3-5 hour thaw |
| **AWS S3 Glacier Deep** | $0.00099 | $0.09/GB | **~$5-7** | 12 hour thaw |
| **Wasabi** | $0.0069 | Free | **~$3.25** | 90-day minimum |

### Recommendation: Cloudflare R2

- **Cost:** Effectively free for the first few months, under $0.50/month at year's end
- **No egress fees:** Disaster recovery downloads are free, always
- **Instant access:** No thaw delays like Glacier
- **Already wired:** The archiver code already targets R2 (just needs credentials)
- **Ecosystem:** If you ever want to build a web UI or API on top of the archive, Cloudflare Workers are right there

Backblaze B2 is marginally cheaper but the ~$1/year difference isn't worth switching when R2 is already the target in the codebase.

## Backfill Execution Plan

### Pre-flight
1. Run `python pipeline/backfill_video.py --dry-run` to see candidates and storage estimate
2. Check available local disk space (`df -h`)
3. Ensure `yt-dlp` is up to date (`yt-dlp -U`)

### Phased Execution
1. **Test batch:** `--limit 3` — verify downloads work, check file sizes
2. **Short-form batch:** Filter to social_video first (smaller, faster)
3. **Long-form batch:** YouTube videos last (largest files, longest downloads)

### Failure Handling
- Some URLs may be dead (deleted TikToks, removed YouTube videos) — the backfill script logs failures and continues
- Podcasts typically don't have video versions — skip gracefully
- Rate limiting: yt-dlp handles this, but consider `--limit 10` batches with breaks between

### Post-Backfill
1. Verify downloads: `find media/ -name "*.mp4" | wc -l`
2. Run archiver to push new video files to R2: `python pipeline/archiver.py`
3. Reindex for semantic search: `reindex_media` MCP tool
