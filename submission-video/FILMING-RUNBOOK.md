# FILMING RUNBOOK: Covenant live demo (Oct 3)

Every command is already finished and filled in. You run ONE line per shot.
You never read the narration script (it is a recorded robot voice), you never
edit anything, you just paste-and-press-enter. Total: about 10 minutes of
footage, most of it waiting.

## Before you hit record

1. Terminal: fresh tab, font zoomed (cmd +).
2. Browser tabs, in this order:
   - http://localhost:3000  (leaderboard; "vouch-sol-demo" is already listed)
   - https://sepolia.arbiscan.io/address/0x92613e84e3473f3886172947ecdff3627978fd18
   - http://localhost:3000/verify
3. Screen recorder: 1920x1080, fullscreen, hit record BEFORE shot 1 and just
   leave it running through everything, including the wait.

## The shots

### SHOT 1 - register (bond leaves the wallet on camera)

```bash
bash submission-video/demo/shot1-register.sh
```

Watch for: a JSON response with "strategyId":"2". That JSON leaving the
screen is fine; what I need is you scrolling the output, then opening the
transaction on Arbiscan (copy the "hash" value, paste in arbiscan.sepolia.io
search, show the page).

### SHOT 2 - commit epoch 0 (the Merkle root lands on-chain)

```bash
bash submission-video/demo/shot2-commit.sh
```

Watch for: a JSON response with "hash". Copy that hash into Arbiscan search
and show the transaction page (it decodes the commit). This is the money
shot; give it a few extra seconds.

### SHOT 3 - the live leaderboard

Switch to the localhost:3000 tab. Refresh. The new strategy (id 2) appears
with epoch 0 pending and a challenge countdown. Do NOT frame the old row
(id 1, the one with big raw numbers) - film the id 2 row, or click it and
show its detail page. Then let the countdown run. Keep recording; browse
other tabs for 5 minutes. The wait is real and I compress it in the edit.

### SHOT 4 - the browser receipt verifier

Go to localhost:3000/verify. Open
`submission-video/demo/verify-paste.txt` and copy its four values into the
four fields (the three proof hashes go in the big box, one per line).
Click "Verify onchain" -> green VERIFIED stamp. Linger 3 seconds.
Then delete one character of any hash and click again -> REJECTED. Linger.

### SHOT 5 - finalize (only after the countdown hits zero)

```bash
bash submission-video/demo/shot5-finalize.sh
bash submission-video/demo/shot5b-performance.sh
```

The second command prints the final numbers. For the camera, prefer the
website: refresh the strategy's detail page on localhost:3000 and show
EQUITY 10.5, HIGH-WATER MARK 10.5, CUMULATIVE PNL +5.5 - exactly what the
narration speaks.

### SHOT 6 - Arbiscan close-ups (30 seconds)

- The verified main contract: 0x92613e84e3473f3886172947ecdff3627978fd18
  (show the verified checkmark area).
- The commit tx page from shot 2 (scroll the decoded event).
- https://sepolia.arbiscan.io/token/0x8428da91ee8d2963182aa440816d7349882e64ce
  (the mock USDG token; it must look obviously like a test token on screen -
  the honesty rule wants "mock" visible or implied).

## If anything errors

Stop recording that shot only, paste me the error, I fix it, you re-run that
one script. Nothing else breaks.

## After filming

Drop the raw file in submission-video/ (any format, .mov is fine) and tell
me. I trim the 12-20s slot, fix the one stale scene number, re-render, hand
you the final mp4 for upload.
