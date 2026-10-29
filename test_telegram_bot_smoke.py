import importlib
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
import asyncio


class TelegramBotDbSmokeTest(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(prefix="leetcode_bot_test_", suffix=".db")
        os.close(fd)
        os.unlink(self.db_path)
        os.environ["DB_PATH"] = self.db_path
        os.environ["DATABASE_URL"] = ""
        sys.modules.pop("telegram_bot", None)
        self.bot = importlib.import_module("telegram_bot")
        self.bot.init_db()

    def tearDown(self):
        sys.modules.pop("telegram_bot", None)
        try:
            os.remove(self.db_path)
        except FileNotFoundError:
            pass

    def test_snapshot_leaderboard_and_backup_tables(self):
        self.bot.add_user(1, "alice", "alice_lc")
        today = datetime.now(self.bot.TZ).strftime("%Y-%m-%d")
        titles = [self.bot._encode_task_entry("EASY", "Two Sum", "two-sum")]

        merged = self.bot.update_snapshot_and_leaderboard(today, 1, 1, titles)

        self.assertEqual(merged, titles)
        self.assertEqual(self.bot.get_leaderboard_points()[1], 1)
        snapshot = self.bot.get_daily_snapshot(today, 1, 3600)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["titles"], titles)
        self.assertGreater(snapshot["fetched_at"], 0)

        self.bot.set_cached_problem_difficulty("two-sum", "EASY")
        backup_tables = self.bot.collect_backup_data()["tables"]
        self.assertTrue(backup_tables["users"])
        self.assertTrue(backup_tables["daily_stats"])
        self.assertTrue(backup_tables["leaderboard"])
        self.assertTrue(backup_tables["problem_cache"])

    def test_warning_is_awarded_once_per_day_and_backed_up(self):
        self.bot.add_user(1, "alice", "alice_lc")
        today = datetime.now(self.bot.TZ).strftime("%Y-%m-%d")

        warn_count, awarded = self.bot.award_warn_once(today, 1)
        warn_count_again, awarded_again = self.bot.award_warn_once(today, 1)

        self.assertTrue(awarded)
        self.assertEqual(warn_count, 1)
        self.assertFalse(awarded_again)
        self.assertEqual(warn_count_again, 1)
        backup_tables = self.bot.collect_backup_data()["tables"]
        self.assertEqual(backup_tables["warns"][0]["count"], 1)
        self.assertEqual(backup_tables["warn_events"][0]["day"], today)

    def test_migrates_v5_daily_stats_without_losing_rows(self):
        sys.modules.pop("telegram_bot", None)
        try:
            os.remove(self.db_path)
        except FileNotFoundError:
            pass

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT)")
        cur.execute(
            """
            CREATE TABLE daily_stats (
                day TEXT,
                telegram_id BIGINT,
                solved_count INTEGER,
                titles_json TEXT,
                PRIMARY KEY(day, telegram_id)
            )
            """
        )
        cur.execute("INSERT INTO config(key, value) VALUES('db_schema_version', '5')")
        cur.execute(
            "INSERT INTO daily_stats(day, telegram_id, solved_count, titles_json) VALUES(?, ?, ?, ?)",
            ("2026-07-13", 1, 1, "[]"),
        )
        conn.commit()
        conn.close()

        self.bot = importlib.import_module("telegram_bot")
        self.bot.init_db()

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(daily_stats)")
        columns = {row[1] for row in cur.fetchall()}
        cur.execute("SELECT solved_count FROM daily_stats WHERE day='2026-07-13' AND telegram_id=1")
        row = cur.fetchone()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='problem_cache'")
        problem_cache_exists = cur.fetchone() is not None
        conn.close()

        self.assertIn("fetched_at", columns)
        self.assertEqual(row[0], 1)
        self.assertTrue(problem_cache_exists)

    def test_bulk_snapshot_and_warning_reads(self):
        self.bot.add_user(1, "alice", "alice_lc")
        self.bot.add_user(2, "bob", "bob_lc")
        today = datetime.now(self.bot.TZ).strftime("%Y-%m-%d")

        self.bot.update_snapshots_and_leaderboard(
            today,
            [
                (1, 1, [self.bot._encode_task_entry("EASY", "Two Sum", "two-sum")]),
                (2, 1, [self.bot._encode_task_entry("HARD", "N-Queens", "n-queens")]),
            ],
        )
        self.bot.set_warn_count(1, 2)

        snapshots = self.bot.get_daily_snapshots(today, [1, 2])
        warn_counts = self.bot.get_warn_counts([1, 2])

        self.assertEqual(len(snapshots), 2)
        self.assertEqual(snapshots[1]["solved_count"], 1)
        self.assertEqual(snapshots[2]["titles"][0], "HARD N-Queens||n-queens")
        self.assertEqual(warn_counts, {1: 2})

    def test_group_leetcode_batch_reuses_single_response(self):
        today = datetime.now(self.bot.TZ).date()
        timestamp = str(int(datetime.now(self.bot.TZ).timestamp()))
        calls = []

        original_submissions = self.bot.leetcode_recent_accepted_submissions_batch
        original_difficulties = self.bot.leetcode_question_difficulties
        try:
            def fake_submissions(nicks):
                calls.append(list(nicks))
                return {
                    "alice_lc": [{"title": "Two Sum", "titleSlug": "two-sum", "timestamp": timestamp}],
                    "bob_lc": [{"title": "N-Queens", "titleSlug": "n-queens", "timestamp": timestamp}],
                }

            self.bot.leetcode_recent_accepted_submissions_batch = fake_submissions
            self.bot.leetcode_question_difficulties = lambda slugs: {
                "two-sum": "EASY",
                "n-queens": "HARD",
            }

            result = self.bot.accepted_titles_on_day_batch(["alice_lc", "bob_lc"], today)
        finally:
            self.bot.leetcode_recent_accepted_submissions_batch = original_submissions
            self.bot.leetcode_question_difficulties = original_difficulties

        self.assertEqual(calls, [["alice_lc", "bob_lc"]])
        self.assertEqual(result["alice_lc"][0], ["EASY Two Sum||two-sum"])
        self.assertEqual(result["bob_lc"][0], ["HARD N-Queens||n-queens"])

    def test_list_ranks_by_points_not_by_task_count(self):
        """4 Easy (4 points) must rank below 1 Hard (5 points)."""
        self.bot.add_user(1, "easyguy", "easy_lc")
        self.bot.add_user(2, "hardguy", "hard_lc")
        today = datetime.now(self.bot.TZ).strftime("%Y-%m-%d")

        easy_titles = [
            self.bot._encode_task_entry("EASY", f"Easy Task {i}", f"easy-task-{i}")
            for i in range(4)
        ]
        hard_titles = [self.bot._encode_task_entry("HARD", "N-Queens", "n-queens")]
        self.bot.update_snapshots_and_leaderboard(
            today, [(1, len(easy_titles), easy_titles), (2, len(hard_titles), hard_titles)]
        )

        self.assertEqual(self.bot.points_from_titles(easy_titles), 4)
        self.assertEqual(self.bot.points_from_titles(hard_titles), 5)

        sent = []

        class FakeMessage:
            async def reply_text(self, text, **kwargs):
                sent.append(text)

        update = SimpleNamespace(
            message=FakeMessage(),
            effective_chat=SimpleNamespace(id=1, type="private"),
            effective_user=SimpleNamespace(id=1, username="easyguy", full_name="Easy", is_bot=False),
        )
        context = SimpleNamespace(args=[], bot=None)

        asyncio.run(self.bot.listcmd(update, context))

        self.assertEqual(len(sent), 1)
        lines = [ln for ln in sent[0].splitlines() if "балл" in ln and "—" in ln]
        self.assertEqual(len(lines), 2)
        self.assertIn("hardguy", lines[0])
        self.assertIn("5 балл(ов) · 1 задач", lines[0])
        self.assertIn("easyguy", lines[1])
        self.assertIn("4 балл(ов) · 4 задач", lines[1])

    def test_membership_checks_are_cached_between_commands(self):
        class FakeBot:
            def __init__(self):
                self.calls = []

            async def get_chat_member(self, chat_id, user_id):
                self.calls.append((chat_id, user_id))
                return SimpleNamespace(status="member")

        fake_bot = FakeBot()
        context = SimpleNamespace(bot=fake_bot)
        rows = [(1, "alice", "alice_lc"), (2, "bob", "bob_lc")]
        self.bot._membership_cache.clear()

        async def run_checks():
            first = await self.bot.prune_inactive_users(context, -100, rows, "test")
            second = await self.bot.prune_inactive_users(context, -100, rows, "test")
            return first, second

        first, second = asyncio.run(run_checks())
        self.assertEqual(first, rows)
        self.assertEqual(second, rows)
        self.assertEqual(sorted(fake_bot.calls), [(-100, 1), (-100, 2)])


class FakeGroupBot:
    """Records what the bot would do in Telegram; everyone is a group member unless listed in `left`."""

    def __init__(self, left=()):
        self.left = set(left)
        self.sent = []
        self.banned = []

    async def get_chat_member(self, chat_id, user_id):
        return SimpleNamespace(status="left" if user_id in self.left else "member")

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append(text)

    async def send_document(self, *args, **kwargs):
        pass

    async def ban_chat_member(self, chat_id, user_id):
        self.banned.append(user_id)

    async def unban_chat_member(self, chat_id, user_id):
        pass


class FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


class FreezeTest(unittest.TestCase):
    ADMIN = 900
    CHAT = -100

    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(prefix="leetcode_bot_test_", suffix=".db")
        os.close(fd)
        os.unlink(self.db_path)
        os.environ["DB_PATH"] = self.db_path
        os.environ["DATABASE_URL"] = ""
        sys.modules.pop("telegram_bot", None)
        self.bot = importlib.import_module("telegram_bot")
        self.bot.ADMIN_IDS = {self.ADMIN}
        self.bot.AUTO_BACKUP_ENABLED = False
        self.bot._membership_cache.clear()
        self.bot.init_db()
        self.bot.db_set_config("report_chat_id", str(self.CHAT))
        self.bot.add_user(1, "alice", "alice_lc")
        self.bot.add_user(2, "bob", "bob_lc")
        self.today = datetime.now(self.bot.TZ).strftime("%Y-%m-%d")

    def tearDown(self):
        sys.modules.pop("telegram_bot", None)
        try:
            os.remove(self.db_path)
        except FileNotFoundError:
            pass

    def run_cmd(self, handler, args, user_id=ADMIN):
        message = FakeMessage()
        update = SimpleNamespace(
            message=message,
            effective_chat=SimpleNamespace(id=1, type="private"),
            effective_user=SimpleNamespace(id=user_id, username="admin", full_name="Admin", is_bot=False),
        )
        context = SimpleNamespace(args=args, bot=FakeGroupBot())
        asyncio.run(handler(update, context))
        return message.replies

    def solved_nothing(self):
        """Patch LeetCode so nobody solved anything today."""
        async def no_titles(nick, day, deep_check=False):
            return [], None

        async def no_titles_for_user(tid, nick, day, deep_check=False, prefer_snapshot=False):
            return [], None, False

        self.bot.accepted_titles_on_day_async = no_titles
        self.bot.get_titles_for_user_on_day = no_titles_for_user

    def test_freeze_and_unfreeze_commands(self):
        replies = self.run_cmd(self.bot.freeze_cmd, ["@alice"])
        self.assertIn("отставку", replies[0])
        self.assertEqual(self.bot.get_frozen_users(), {1: self.today})

        replies = self.run_cmd(self.bot.freeze_cmd, ["@alice"])
        self.assertIn("уже в отставке", replies[0])

        replies = self.run_cmd(self.bot.freeze_cmd, [])
        self.assertIn("alice", replies[0])
        self.assertNotIn("bob", replies[0])

        replies = self.run_cmd(self.bot.unfreeze_cmd, ["alice_lc"])  # by LeetCode nick
        self.assertIn("вернулся", replies[0])
        self.assertEqual(self.bot.get_frozen_users(), {})

        replies = self.run_cmd(self.bot.unfreeze_cmd, ["@alice"])
        self.assertIn("и так не в отставке", replies[0])

        replies = self.run_cmd(self.bot.freeze_cmd, ["@nobody"])
        self.assertIn("Не нашёл", replies[0])

    def test_freeze_is_admin_only(self):
        replies = self.run_cmd(self.bot.freeze_cmd, ["@alice"], user_id=1)
        self.assertIn("только для админа", replies[0])
        self.assertEqual(self.bot.get_frozen_users(), {})

        self.bot.set_user_frozen(1, True)
        replies = self.run_cmd(self.bot.unfreeze_cmd, ["@alice"], user_id=1)
        self.assertIn("только для админа", replies[0])
        self.assertEqual(self.bot.get_frozen_users(), {1: self.today})

    def test_daily_report_does_not_warn_or_kick_frozen_user(self):
        self.solved_nothing()
        self.bot.set_warn_count(1, 2)  # alice is one miss away from a kick
        self.bot.set_warn_count(2, 2)
        self.bot.set_user_frozen(1, True)

        fake_bot = FakeGroupBot()
        asyncio.run(self.bot.daily_report_job(SimpleNamespace(bot=fake_bot)))

        # alice keeps 2/3; bob hit 3/3, got kicked, and remove_user() wiped his warns
        self.assertEqual(self.bot.get_warn_counts([1, 2]), {1: 2})
        self.assertEqual(fake_bot.banned, [2])
        report = fake_bot.sent[0]
        self.assertNotIn("alice", report)
        self.assertIn("bob", report)
        self.assertIn("warn 3/3 ❌ KICK", report)
        # alice stays registered with her stats; bob was kicked and removed
        self.assertEqual([int(r[0]) for r in self.bot.list_users()], [1])

    def test_daily_report_warns_user_again_after_unfreeze(self):
        self.solved_nothing()
        self.bot.set_user_frozen(1, True)
        self.bot.set_user_frozen(1, False)

        asyncio.run(self.bot.daily_report_job(SimpleNamespace(bot=FakeGroupBot())))

        self.assertEqual(self.bot.get_warn_counts([1, 2]), {1: 1, 2: 1})

    def test_reminder_and_evening_status_skip_frozen_user(self):
        self.solved_nothing()
        self.bot.set_user_frozen(1, True)

        fake_bot = FakeGroupBot()
        context = SimpleNamespace(bot=fake_bot)
        asyncio.run(self.bot.reminder_job(context))
        asyncio.run(self.bot.evening_status_job(context))

        self.assertEqual(len(fake_bot.sent), 2)
        for text in fake_bot.sent:
            self.assertNotIn("alice", text)
            self.assertIn("bob", text)

    def test_reminder_celebrates_when_only_frozen_users_have_zero(self):
        async def titles_for_user(tid, nick, day, deep_check=False, prefer_snapshot=False):
            if tid == 2:
                return [self.bot._encode_task_entry("EASY", "Two Sum", "two-sum")], None, True
            return [], None, True

        self.bot.get_titles_for_user_on_day = titles_for_user
        self.bot.set_user_frozen(1, True)

        fake_bot = FakeGroupBot()
        asyncio.run(self.bot.reminder_job(SimpleNamespace(bot=fake_bot)))

        self.assertEqual(len(fake_bot.sent), 1)
        self.assertIn("ВСЕ МОЛОДЦЫ", fake_bot.sent[0])

    def test_frozen_user_who_left_group_is_not_pruned(self):
        self.bot.set_user_frozen(1, True)
        fake_bot = FakeGroupBot(left={1, 2})

        rows = asyncio.run(
            self.bot.prune_inactive_users(SimpleNamespace(bot=fake_bot), self.CHAT, self.bot.list_users(), "test")
        )

        self.assertEqual([int(r[0]) for r in rows], [1])
        self.assertEqual([int(r[0]) for r in self.bot.list_users()], [1])

    def test_list_shows_frozen_separately_and_warns_marks_them(self):
        self.bot.set_user_frozen(1, True)
        self.bot.refresh_day_snapshots = lambda rows, day: asyncio.sleep(0, result=(0, 0))

        text = self.run_cmd(self.bot.listcmd, [], user_id=2)[0]
        status_part, frozen_part = text.split("🧊 В отставке:")
        self.assertNotIn("alice", status_part)
        self.assertIn("bob", status_part)
        self.assertIn("alice", frozen_part)

        warns_text = self.run_cmd(self.bot.warns, [], user_id=2)[0]
        alice_line = next(ln for ln in warns_text.splitlines() if "alice" in ln)
        bob_line = next(ln for ln in warns_text.splitlines() if "bob" in ln)
        self.assertIn("в отставке", alice_line)
        self.assertNotIn("в отставке", bob_line)

    def test_backup_includes_freeze_state(self):
        self.bot.set_user_frozen(1, True)

        backup = self.bot.collect_backup_data()["tables"]["users"]
        by_id = {row["telegram_id"]: row for row in backup}
        self.assertEqual(by_id[1]["frozen_since"], self.today)
        self.assertIsNone(by_id[2]["frozen_since"])

    def test_migrates_v6_users_table_without_losing_rows(self):
        sys.modules.pop("telegram_bot", None)
        os.remove(self.db_path)

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT)")
        cur.execute("CREATE TABLE users (telegram_id BIGINT PRIMARY KEY, username TEXT, leetcode_nick TEXT)")
        cur.execute("INSERT INTO config(key, value) VALUES('db_schema_version', '6')")
        cur.execute("INSERT INTO users VALUES(7, 'carol', 'carol_lc')")
        conn.commit()
        conn.close()

        self.bot = importlib.import_module("telegram_bot")
        self.bot.init_db()

        self.assertEqual(self.bot.db_get_config("db_schema_version"), "7")
        self.assertEqual([tuple(r) for r in self.bot.list_users()], [(7, "carol", "carol_lc")])
        self.assertEqual(self.bot.get_frozen_users(), {})
        self.bot.set_user_frozen(7, True)
        self.assertIn(7, self.bot.get_frozen_users())


if __name__ == "__main__":
    unittest.main()
