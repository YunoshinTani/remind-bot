from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from remind_bot.config import ConfigError, Pair, Student
from remind_bot.members import Resolution, resolve_pairs, save_member_matches

from tests.helpers import stamp


class MemberTests(unittest.TestCase):
    def member(self, member_id, *, name="account", nick=None, global_name=None, bot=False):
        return SimpleNamespace(id=member_id, name=name, nick=nick, global_name=global_name, bot=bot)

    def test_exact_number_and_name_match_all_display_fields(self):
        pairs = (Pair(1, (Student(1, None, True, "山田太郎"), Student(2, None, True, "山田花子"))),)
        result = resolve_pairs(pairs, [self.member(11, nick="01-山田太郎"), self.member(22, global_name="02-山田花子"), self.member(99, nick="01-山田太郎", bot=True)])
        self.assertEqual(result.problems, ())
        self.assertEqual([s.discord_id for s in result.pairs[0].students], [11, 22])

    def test_ambiguous_or_only_number_is_not_guessed(self):
        pairs = (Pair(1, (Student(1, None, True, "山田太郎"), Student(2, None, True, "山田花子"))),)
        result = resolve_pairs(pairs, [self.member(11, nick="01-山田太郎"), self.member(12, name="01-山田太郎"), self.member(22, nick="02-別人")])
        self.assertEqual(len(result.problems), 2)
        self.assertTrue(all(s.discord_id is None for s in result.pairs[0].students))

    def test_explicit_alias_and_manual_id(self):
        pairs = (Pair(1, (Student(1, None, True, "山田太郎", "custom-account"), Student(2, 22, True, "山田花子"))),)
        result = resolve_pairs(pairs, [self.member(11, name="custom-account"), self.member(22, name="renamed")])
        self.assertFalse(result.problems)
        self.assertEqual([s.discord_id for s in result.pairs[0].students], [11, 22])

    def test_save_backups_and_preserves_unresolved_and_inactive_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "roster.csv"
            original = "number,name,discord_id,active,discord_name\n1,山田太郎,,true,old\n2,山田花子,,true,unknown\n3,休学者,99,false,inactive\n"
            path.write_text(original, encoding="utf-8")
            member = self.member(11, nick="01-山田太郎")
            resolution = Resolution((Pair(1, (Student(1, 11, True, "山田太郎"), Student(2, None, True, "山田花子"))),), ("02番: 未解決",))
            result = save_member_matches(path, resolution, [member], backup_dir=root / "backups", now=stamp())
            self.assertEqual(result.changed, 1)
            assert result.backup is not None
            self.assertEqual(result.backup.read_text(encoding="utf-8"), original)
            with path.open(encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(rows[0], {"number": "1", "name": "山田太郎", "discord_id": "11", "active": "true", "discord_name": "01-山田太郎"})
            self.assertEqual(rows[1]["discord_id"], "")
            self.assertEqual(rows[1]["discord_name"], "unknown")
            self.assertEqual(rows[2]["active"], "false")
            self.assertEqual(rows[2]["discord_id"], "99")
            repeated = save_member_matches(path, resolution, [member], backup_dir=root / "backups", now=stamp())
            self.assertEqual(repeated.changed, 0)
            self.assertIsNone(repeated.backup)

    def test_invalid_sync_does_not_replace_existing_roster(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "roster.csv"
            original = "number,name,discord_id,active\n1,山田太郎,,true\n2,山田花子,,true\n"
            path.write_text(original, encoding="utf-8")
            resolution = Resolution((Pair(1, (Student(1, 11, True, "山田太郎"), Student(2, 11, True, "山田花子"))),), ())
            with self.assertRaises(ConfigError):
                save_member_matches(path, resolution, [self.member(11, nick="example")], backup_dir=root / "backups", now=stamp())
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            self.assertEqual(list(root.glob(".roster-sync-*.tmp")), [])
