# Local checkpoints and run artifacts (gitignored)

Model checkpoints, acceptance verdicts, and run logs for the laya/vouch
calibration arc live here - inside the project, not scattered across the
home directory. Too big and too reproducible to commit.

| Item | What it is |
| --- | --- |
| `laya-vouch-v3/` | The gold-fixed v3 fine-tune (Kaggle T4, 7mo data). Honest calibration (ECE 0.094), no edge - the verdict file has the numbers. |
| `acceptance-verdict*.txt` | Pre-registered acceptance results per attempt (v1/v2/v3-real). |
| `*.log` | Recording campaign, deploy-watcher, replay and serve logs. |

Old references in session notes to `~/colibri/checkpoints/...` now resolve
here. TWO directories under `~/colibri/checkpoints/` are NOT ours and must
never be moved or touched:

- `kg-v03` - the parallel session's multilingual run (user directive).
- `clef-flash` - an active workspace of another session (downloaded and
  used Oct 3, has its own pycache). If v5 needs a local clef-flash copy,
  download a fresh one into this directory under a different name; never
  move or reuse that one.
