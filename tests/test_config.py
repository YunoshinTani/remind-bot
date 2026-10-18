from __future__ import annotations

import unittest
import json
import tempfile
from pathlib import Path

from remind_bot.config import ConfigError, load_config


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        examples = Path(__file__).resolve().parents[1] / "examples"
        (self.folder / "roster.example.csv").write_bytes((examples / "roster.example.csv").read_bytes())
        self.template = "\n".join(
            line for line in (examples / "config.demo.toml").read_text(encoding="utf-8").splitlines()
            if not line.startswith(("pickup_location =", "submission_location ="))
        )

    def config_with_locations(self, **locations: object):
        values = "".join(f"{key} = {json.dumps(value, ensure_ascii=False)}\n" for key, value in locations.items())
        path = self.folder / "config.toml"
        path.write_text(self.template.replace("[notifications]\n", "[notifications]\n" + values), encoding="utf-8")
        return load_config(path)

    def test_demo_config_uses_fictional_roster_without_credentials(self):
        root = Path(__file__).resolve().parents[1]
        config = load_config(root / "examples/config.demo.toml")
        self.assertEqual(config.first_pair, 1)
        self.assertEqual([s.number for s in config.pairs[0].students], [1, 2])
        self.assertEqual(config.pickup_location, "サンプル保管場所")
        with self.assertRaises(ConfigError):
            load_config(root / "examples/config.demo.toml", live=True)

    def test_existing_configs_without_locations_use_generic_defaults(self):
        config = self.config_with_locations()
        self.assertEqual(config.pickup_location, "担当教員の部屋の前")
        self.assertEqual(config.submission_location, "担当教員の部屋の前")

    def test_locations_are_independent_and_trimmed(self):
        config = self.config_with_locations(pickup_location="  サンプル保管場所  ", submission_location="サンプル提出場所")
        self.assertEqual(config.pickup_location, "サンプル保管場所")
        self.assertEqual(config.submission_location, "サンプル提出場所")

    def test_locations_reject_empty_multiline_long_and_non_string_values(self):
        for key in ("pickup_location", "submission_location"):
            for value in ("", "  ", "一行目\n二行目", "一行目\r二行目", "あ" * 201, 123, False, []):
                with self.subTest(key=key, value=value):
                    with self.assertRaises(ConfigError):
                        self.config_with_locations(**{key: value})
