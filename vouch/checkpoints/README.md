# Local checkpoints and run artifacts (gitignored)

Model checkpoints, acceptance verdicts, and run logs for the laya/vouch
calibration arc live here - inside the project, not scattered across the
home directory. Too big and too reproducible to commit.

| Item | What it is |
| --- | --- |
| `laya-vouch-v3/` | The gold-fixed v3 fine-tune (Kaggle T4, 7mo data). Honest calibration (ECE 0.094), no edge - the verdict file has the numbers. |
| `clef-flash/` | Cloudflare's 9B decision model (BF16), staged for the v5 judge/System-1 spikes. |
| `acceptance-verdict*.txt` | Pre-registered acceptance results per attempt (v1/v2/v3-real). |
| `*.log` | Recording campaign, deploy-watcher, replay and serve logs. |

Old references in session notes to `~/colibri/checkpoints/...` now resolve
here. `kg-v03` (the parallel session's multilingual run) stays at
`~/colibri/checkpoints/kg-v03` on purpose - different matter, hands off.
