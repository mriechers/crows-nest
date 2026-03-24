Save a URL's content to the Obsidian vault inbox via the Crow's Nest pipeline — handles web pages, YouTube, podcasts, social video, audio files, and images.

{{#if ARGUMENTS}}
## Capturing Content

You've been asked to save content to the Obsidian inbox with: `$ARGUMENTS`

### Parse the Arguments

The argument should be a URL. Extract it and validate it starts with `http://` or `https://`.

If the argument doesn't look like a URL, ask the user to provide a valid URL.

If there's additional context after the URL (speaker names, topic hints, notes), capture it for the `--context` flag.

### Add to Crow's Nest Pipeline

Run the following commands in sequence. All scripts are in `~/Developer/second-brain/crows-nest/pipeline/` and use the venv at `~/Developer/second-brain/crows-nest/.venv/bin/python`.

**Step 1: Add the URL**

```bash
cd ~/Developer/second-brain/crows-nest/pipeline && ~/Developer/second-brain/crows-nest/.venv/bin/python add_link.py "<URL>" --context "<any context>"
```

If the URL is already queued, report that to the user and stop.

**Step 2: Process the content**

```bash
cd ~/Developer/second-brain/crows-nest/pipeline && ~/Developer/second-brain/crows-nest/.venv/bin/python -B processor.py
```

This downloads media, transcribes audio/video, or scrapes web pages. May take a few minutes for video content.

**Step 3: Summarize and create the Obsidian note**

```bash
cd ~/Developer/second-brain/crows-nest/pipeline && ~/Developer/second-brain/crows-nest/.venv/bin/python -B summarizer.py
```

This calls Haiku via OpenRouter for structured summarization and creates the note in `0 - INBOX/Clippings/`.

**Step 4: Report the result**

Run the status command to confirm:

```bash
cd ~/Developer/second-brain/crows-nest/pipeline && ~/Developer/second-brain/crows-nest/.venv/bin/python -B status.py
```

Tell the user:
- The note title and location in Obsidian
- The content type detected (web page, YouTube, podcast, social video, audio)
- Any errors encountered

### Error Handling

- If the processor fails, check the error in the status output. Common issues:
  - Network timeout: suggest trying again later
  - yt-dlp failure: the platform may block downloads
  - Whisper failure: check RAM availability
- If the summarizer fails (OpenRouter API), the note will still be created with a basic fallback summary

{{else}}
## Add to Inbox

No URL provided. Ask the user:

"What URL would you like to save? I can handle web pages, YouTube videos, podcast episodes, social video (TikTok, Instagram, X, Vimeo), and audio files. I'll process it through Crow's Nest and create a structured note in your Obsidian Clippings."

Wait for the user to provide a URL, then process it as described above.

{{/if}}
