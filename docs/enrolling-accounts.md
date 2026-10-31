# Enrolling more Team seats with plus addresses

This is the runbook for adding a batch of new Devin Team seats to Devin Switch using
Gmail plus addresses (`you+1@gmail.com`, `you+2@gmail.com`, …). All mail for every
plus address arrives in the one `you@gmail.com` inbox, so no new Google accounts or
signed-in Chrome profiles are needed.

`scripts/enroll_accounts.py` automates the browser part. You still buy the seats and
send the invites yourself, and you approve Chrome's remote-debugging prompt.

> Check Devin's terms before buying seats this way. Seats that break the terms can be
> suspended, along with the money paid for them.

## Current state (October 2026)

| Base inbox | Plus addresses | Switch aliases | Chrome profile folders | Next batch |
| --- | --- | --- | --- | --- |
| `ansuman0026@gmail.com` | `+1` … `+100` | `ansuman-27` … `ansuman-126` | `Plus 1` … `Plus 100` (folders removed later; re-login with `ds login ALIAS --default-browser`) | `+101` → `ansuman-127` |
| `bishal.space@gmail.com` | `+1` … `+40` | `space-plus-1` … `space-plus-40` | `Plus 1` … `Plus 40` | `+41` → `space-plus-41` |

Check with `ds list` first.

## Fast path for the next batch

This is the short version of the October 2026 run (16 seats, `bishal.space+25` … `+40`),
in order, with the parts that cost time already solved. It uses the `bishal.space` row
above. For another inbox, change `--email`, `--prefix` and the numbers. The sections
under [Steps](#steps) explain each step in more detail.

1. **Invite the seats.** In Devin, go to **Settings → Members**, add the seats, and paste:

   ```sh
   python3 -c 'print(", ".join(f"bishal.space+{n}@gmail.com" for n in range(41, 51)))'
   ```

   The script stops at the first account that still reports `Free`.
2. **Update `ds` and the Chrome access helper.** From the repository root, run
   `env -u XDG_DATA_HOME -u XDG_CONFIG_HOME -u XDG_CACHE_HOME -u XDG_STATE_HOME make app`.
   Then give **Devin Switch Chrome Access** Full Disk Access (System Settings → Privacy &
   Security). `ds --help` must list `chrome`. An older `ds` stops `ds login` with
   `macOS lets only Chrome and apps with Full Disk Access read Chrome's profiles`.
3. **Prepare Chrome.** Use the Chrome profile that is signed in to `bishal.space@gmail.com`.
   Open Gmail in a tab (if no tab matches, the script opens one). Turn on
   `chrome://inspect/#remote-debugging` and make sure it shows
   `Server running at: 127.0.0.1:9222`.
4. **Stop old logins.** Run `ps -ax | grep "auth login"` and stop any `ds login` that is
   left over. Otherwise the script fails with `This profile is in use` or
   `did not print a sign-in URL`. To see which process holds an account, run
   `lsof ~/.local/share/devin-switch/account-<alias>.lock`.
5. **Do a dry run, then the real run.** The `env -u …` prefix is necessary only inside a
   Switch-managed chat, but it does no harm elsewhere:

   ```sh
   env -u XDG_DATA_HOME -u XDG_CONFIG_HOME -u XDG_CACHE_HOME -u XDG_STATE_HOME \
     uv run --locked --with playwright python scripts/enroll_accounts.py \
     --email bishal.space@gmail.com --first 41 --last 50 --first-alias 41 \
     --prefix space-plus --passes 2 --dry-run
   ```

   Make sure that no alias is already in `ds list`. Then run the same command without
   `--dry-run`.
6. **Click Allow on each prompt.** Prompts can open behind other windows.
   - **"A Devin agent wants to read your Chrome data"**: the helper reads
     `DevToolsActivePort`. If nobody answers in 2 minutes, it denies.
   - **Allow remote debugging?**: Chrome's own prompt.
   - On the first run only, a macOS prompt asks to let your terminal control Google Chrome.
     The script uses this to close old Devin sign-in windows.
7. **Wait.** Each account takes 1–2 minutes, then the script waits 45 s. The messages
   `rate limited; waiting 5 minutes` and
   `ds login offered a non-Devin sign-in page; retrying` are normal and need no action.
   `--passes 2` tries failed seats again after 10 minutes.
8. **If the script stops with `Unexpected page: …`,** Devin changed its sign-in pages. In
   October 2026 the change was a Terms of Service checkbox, which `request_code` now ticks.
   Do that alias by hand ([below](#doing-one-account-by-hand)), then update
   `request_code` or `cli_code` and add a test in `tests/test_enroll_script.py`.
9. **Finish.** Make sure `ds list` shows `credentials saved` for each new alias. Turn
   remote debugging **off**. Delete the "Devin Login Code" emails (also in Spam). Update
   the Current state table.

**If the Chrome access popup is not available** (for example, the helper is not installed,
or the shell is an agent sandbox and you get `macOS blocks reading …`), point the script
at a scratch folder. The script and `ds` both read `DS_CHROME_DATA`:

```sh
SCRATCH=$(mktemp -d)
# A profile index that lists the Plus folders, so nobody reads Chrome's real index.
# Chrome creates each folder the first time `ds login` opens that profile.
python3 -c 'import json; print(json.dumps({"profile": {"info_cache": {"Default": {}, **{f"Plus {n}": {} for n in range(1, 100)}}}}))' \
  > "$SCRATCH/Local State"
echo "$SCRATCH"
```

From a terminal that can read Chrome's folder (an ordinary Terminal window, not the
agent's shell), copy the port file into that folder:

```sh
cat ~/Library/Application\ Support/Google/Chrome/DevToolsActivePort > /path/to/scratch/DevToolsActivePort
```

Then put `DS_CHROME_DATA=$SCRATCH` before `uv run` in step 5. Chrome writes a new port
file each time remote debugging starts, so copy the file again for each run.

## What the script does

For each number N it:

1. **Creates the Chrome profile folder** `Plus N` (no Google account) and registers
   the Switch alias bound to it with `ds add`. Existing matching aliases are reused.
2. **Starts `ds login <alias>`** on a private terminal (PTY) and reads the one-time
   sign-in URL the CLI prints.
3. **Opens that URL in a fresh, isolated, incognito-style browser context** in your
   Chrome, so Devin cookies from different accounts never mix. It signs up as
   `you+N@gmail.com`, or logs in if the account already exists.
4. **Finds Devin's verification code in your Gmail tab** (Inbox or Spam). It only opens
   emails with the subject "Devin Login Code" and checks the recipient is exactly
   `you+N@gmail.com`.
5. **Selects the organization** with the requested plan (`Teams` by default). Accounts
   that only belong to one organization skip this step.
6. **Types the CLI login code straight into the waiting `ds login` prompt.** The code is
   never printed, logged, or shown in any terminal.
7. **Verifies the result** through Devin's usage endpoint: the alias must report
   `you+N@gmail.com` on the expected plan, or the script stops.
8. **Closes leftover Devin sign-in windows**, which `ds login` and the native CLI open
   on their own. It only closes windows whose tabs are all `app.devin.ai/auth/…` pages.

It never clicks Purchase. On Devin's upgrade page it just navigates away. It stops at
the first unexpected page and is safe to rerun: aliases that already have a working
login are verified and skipped.

## Steps

### 1. Buy the seats and send the invites (you)

1. Decide the range, e.g. `+27` to `+40`.
2. Generate the list for Devin's **Add coworker by email** box:

   ```sh
   python3 -c 'print(", ".join(f"ansuman0026+{n}@gmail.com" for n in range(27, 41)))'
   ```

3. In Devin, go to **Settings → Members**, add the seats, and paste the list.

The plus accounts don't need to exist yet. The script creates them on first sign-in,
and the invite attaches them to the team.

### 2. Prepare Chrome (you)

1. Open Chrome in the profile signed into `ansuman0026@gmail.com`.
2. Open **Gmail** for that account in a tab and leave it open. The script finds this tab
   by its title, so it must be the right account.
3. Go to `chrome://inspect/#remote-debugging` and tick **Allow remote debugging for
   this browser instance**. It should say `Server running at: 127.0.0.1:9222`.
4. **macOS 27 and later:** the script has to read Chrome's `DevToolsActivePort` file, and
   macOS blocks that with `Operation not permitted`. Set up the Chrome access helper once
   (see "Agent browser" in the README): `make chrome-access`, then give
   “Devin Switch Chrome Access” Full Disk Access. After that, the script shows a
   **"A Devin agent wants to read your Chrome data"** popup when it starts. Click **Allow**,
   then click **Allow** again on Chrome's remote debugging prompt.

### 3. Preview the mapping

From the repository root:

```sh
uv run --locked --with playwright python scripts/enroll_accounts.py \
  --email ansuman0026@gmail.com --first 27 --last 40 --first-alias 53 --dry-run
```

This prints `alias  profile  email` for every account and changes nothing. Make sure
the aliases don't collide with `ds list`.

If you're running this from inside a Switch-managed chat (`ds run`), prefix commands with
`env -u XDG_DATA_HOME -u XDG_CONFIG_HOME -u XDG_CACHE_HOME -u XDG_STATE_HOME` so uv
and Playwright caches don't land inside that account's private directory.

### 4. Run it

Run the same command without `--dry-run`:

```sh
uv run --locked --with playwright python scripts/enroll_accounts.py \
  --email ansuman0026@gmail.com --first 27 --last 40 --first-alias 53
```

- Chrome shows an **Allow remote debugging?** prompt when the script connects. Click
  **Allow**. It may be behind other windows.
- Blank Chrome windows appear while the `Plus N` profiles are created. You can close
  them afterwards.
- Expect roughly 1–2 minutes per account, plus pauses.
- **Rate limits are normal.** Devin limits how quickly new accounts can sign up. The
  script waits 5 minutes and retries (up to 8 times by default), then continues.

Example output:

```text
ansuman-53 <- ansuman0026+27@gmail.com
  ok: ansuman0026+27@gmail.com on Teams
ansuman-54 <- ansuman0026+28@gmail.com
  rate limited; waiting 5 minutes
  ok: ansuman0026+28@gmail.com on Teams
```

### 5. Check and tidy up

1. Run `ds list` and confirm every new alias says **credentials saved**.
2. Turn **off** remote debugging in `chrome://inspect/#remote-debugging`. While it's on,
   any local app can control your browser, including its cookies.
3. Optionally delete the expired "Your Devin Login Code" emails in Gmail (check Spam too).
4. Open the Mac app. New accounts appear after its next refresh, and global quota
   capacity rises by 100 credits per account.
5. Update the **Current state** table above for next time.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--email` | required | Base address; must not already contain `+` |
| `--first` / `--last` | required | Inclusive range of plus numbers |
| `--first-alias` | required | Alias number for `--first`; the rest follow in order |
| `--prefix` | `ansuman` | Alias prefix (`ansuman-53`) |
| `--profile-prefix` | `Plus` | Chrome profile folder prefix (`Plus 27`) |
| `--plan` | `Teams` | Organization plan to select and verify |
| `--pause` | `45` | Seconds between accounts |
| `--rate-limit-retries` | `8` | 5-minute waits allowed per account |
| `--passes` | `1` | Passes over the seats that failed in the previous pass |
| `--pass-wait` | `600` | Seconds between passes |
| `--ds` | `ds` | Path to the `ds` command |
| `--dry-run` | off | Print the mapping only |

## Troubleshooting

| Message | Fix |
| --- | --- |
| Connection hangs on "Connecting to Chrome" | Click **Allow** on Chrome's prompt; confirm remote debugging is on. |
| `PermissionError … Local State` or `DevToolsActivePort` / `macOS blocks reading Chrome's data` | Set up the Chrome access helper (Prepare Chrome, step 4) and rerun. |
| `denied in the popup` / `Nobody answered the Chrome access popup` | Rerun and click **Allow** within 2 minutes. |
| `ds: macOS lets only Chrome and apps with Full Disk Access read Chrome's profiles` | The installed `ds` is older than this repository. Reinstall it with `make app` (Fast path, step 2), or use the scratch folder. |
| `Open Gmail signed in as …` | Open the right Gmail tab in the profile with remote debugging enabled. |
| `No Devin code arrived` | Check Spam manually and rerun. Persistent failures usually mean rate limiting. |
| `This profile is in use` | A previous `ds login` is still running. Find it with `ps -ax \| grep "auth login"`, stop it, and rerun. |
| `<alias> exists but uses Chrome profile …` | Pick a `--first-alias` past your existing aliases. |
| `reports … on 'Free'` | The account hasn't joined the team. Check the invite went to that exact address and that a seat is free, then rerun. |
| `Unexpected page` | Devin changed its sign-in pages. Rerun that alias with `ds login <alias>` by hand, then update the script. |

## Doing one account by hand

Run `ds login <alias>`. If the Devin page shows an empty email box, choose **Sign up**
(the first time) or **Log in**, enter `you+N@gmail.com`, then enter the code from
Gmail. If asked, choose the organization marked **Teams**, copy the code it shows, and
paste it into the Terminal prompt. Paste it nowhere else.
