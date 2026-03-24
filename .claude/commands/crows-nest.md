Check Crow's Nest pipeline status and perform manual operations.

{{#if ARGUMENTS}}
## Command: $ARGUMENTS

Parse the argument to determine the operation:

### "status" (default if no specific command)
Run: `cd ~/Developer/second-brain/crows-nest/pipeline && ~/Developer/second-brain/crows-nest/.venv/bin/python status.py`
Display the output formatted nicely.

### "add <url>" or just a bare URL
Run: `cd ~/Developer/second-brain/crows-nest/pipeline && ~/Developer/second-brain/crows-nest/.venv/bin/python add_link.py "<url>"`
If there's additional context after the URL, pass it with `--context`.
Report the result.

### "process"
Run: `cd ~/Developer/second-brain/crows-nest/pipeline && ~/Developer/second-brain/crows-nest/.venv/bin/python processor.py`
Report what was processed.

### "summarize"
Run: `cd ~/Developer/second-brain/crows-nest/pipeline && ~/Developer/second-brain/crows-nest/.venv/bin/python summarizer.py`
Report what notes were created.

### "archive"
Run: `cd ~/Developer/second-brain/crows-nest/pipeline && ~/Developer/second-brain/crows-nest/.venv/bin/python archiver.py`
Report what was archived.

### "retry"
Reset all failed links back to pending:
```bash
sqlite3 ~/Developer/second-brain/crows-nest/data/crows-nest.db "UPDATE links SET status = 'pending', retry_count = 0, error = NULL, updated_at = CURRENT_TIMESTAMP WHERE status = 'failed'"
```
Report how many links were reset.

### "run" or "run all"
Run the full pipeline in sequence: process, then summarize. Report results of each step.

{{else}}
## Crow's Nest Status

Run `cd ~/Developer/second-brain/crows-nest/pipeline && ~/Developer/second-brain/crows-nest/.venv/bin/python status.py` and display the pipeline dashboard.

{{/if}}
