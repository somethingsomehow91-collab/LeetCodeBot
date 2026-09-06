# LeetCode Telegram Bot

Telegram bot for a group challenge: every participant must solve at least one LeetCode problem per day.

## Features

- `/register <leetcode_nick>` links a Telegram user to a LeetCode profile.
- `/check` shows your accepted problems for today.
- `/check @username` or `/list @username` shows another registered user's accepted problems for today.
- `/who @username` returns the user's LeetCode profile URL.
- `/leaderboard` ranks users by points: Easy = 1, Medium = 3, Hard = 5.
- `/recalculate` lets admins rebuild leaderboard points from saved daily snapshots.
- `/recheckday YYYY-MM-DD` lets admins refetch a specific day and then rebuild the leaderboard.
- Daily report marks who solved at least one problem and applies warnings.
- Users with 3 warnings are removed from the configured group, if the bot has admin rights.
- `/backup` and `/restore` help move the bot between servers.
- If `DATABASE_URL` is set, the bot stores data in Postgres; otherwise it falls back to local SQLite.
- Every mutating command auto-sends a JSON backup to `OWNER_ID` in Telegram (see `auto_backup`), including a safety snapshot taken *immediately before* `/restore` wipes the tables — so an accidental or wrong `/restore` is always recoverable from the owner's own DM.
- On every start/restart, the bot pings `OWNER_ID` with its status and the date of the last sent daily report, and (if automation is on) automatically sends a missed daily report for yesterday if one wasn't sent yet. This is meant to make silent outages visible immediately instead of weeks later.

## Notification Rules

- Normal command output does not use `@username` mentions.
- Set `CHALLENGE_AUTOMATION_ENABLED=1` to enable scheduled reminders and the daily report.
- Reminder pings mention only users who have not solved anything yet, at 18:00 and 23:00 Asia/Almaty.
- The 00:00 daily report mentions only the daily MVP and users with 0 solved problems.
- Hidden admin command `/tagunregistered` pings known group members who have not registered yet.

## Why scoring is more reliable now

The bot uses LeetCode's accepted-submissions query with an explicit limit instead of the smaller mixed recent-submissions list. This avoids missing accepted problems when a user has many failed submissions or many attempts in one day.

Daily warning application is also guarded per date, so a duplicate daily report cannot give the same user multiple warnings for the same missed day.

## Local Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill `.env`, then export it before running:

```bash
set -a
source .env
set +a
python telegram_bot.py
```

## Required Telegram Setup

1. Create a bot through BotFather and copy `TELEGRAM_TOKEN`.
2. Add the bot to your Telegram group.
3. Give the bot admin rights if you want automatic removal after 3 warnings.
4. Run `/setgroup` inside the target group.
5. Each participant runs `/register <leetcode_nick>`.

## Hosting Notes

This bot uses polling, so it does not need a public HTTPS webhook URL. It can run on a VPS, Oracle Cloud Always Free, Railway, Render, Fly.io, or any always-on machine.

On Railway, attach a Postgres database to the bot service and expose its `DATABASE_URL` variable. This keeps users, daily stats, warnings, and leaderboard data outside the app container, so redeploys do not wipe the bot state.

**⚠️ Railway free Trial data-loss trap:** a new Railway account starts on a Trial with a one-time $5 credit that expires 30 days after signup — not 30 days of inactivity, 30 days total. When it expires, Railway stops *all* services in the project (the bot **and** its Postgres), which looks exactly like the bot silently dying with no error message and no daily reports. Worse: Railway deletes the Postgres volume (all data) 30 days after that expiration if the account is never upgraded. **Upgrade to the Hobby plan (add a payment method) well before day 30** if you want this bot to keep running and keep its data. If the bot has gone quiet and nobody touched the code, check the Railway dashboard for a "trial expired" / offline service before assuming it's a code bug.

For free hosting, Oracle Cloud Always Free is usually the most stable long-term option, but setup is more manual. A small paid VPS is simpler and more predictable.

## If the bot/DB ever goes down and data looks lost

1. Check Railway (or wherever it's hosted) first — an offline/removed service (billing, trial expiry, crash loop) is a far more common cause than an actual dropped database, and looks identical from inside the bot (no daily reports, commands stop responding).
2. Look in `OWNER_ID`'s Telegram DM with the bot for a `backup_*.json` file — every mutating command and every daily report auto-sends one there, so the last working state is usually already sitting in that chat.
3. Once the bot is back up and connected to a working database, send that `backup.json` as a Telegram document, reply to it with `/restore`, and confirm the reported `users`/`daily_stats` counts look right.
4. Never run `/restore` against a database you're not sure is the right/empty one — it fully replaces `users`, `daily_stats`, `config`, `leaderboard`, `warns`, `warn_events`, `seen_members`, and `problem_cache`. It does take a safety snapshot of whatever was there right before wiping (also sent to `OWNER_ID`), but recovering from that is still a manual `/restore` in reverse.
