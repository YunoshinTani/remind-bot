from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .config import Config, ConfigError, parse_time
from .store import Store


class CalendarError(ValueError):
    pass


@dataclass(frozen=True)
class SchoolDay:
    date: date
    is_school_day: bool
    morning_time: time
    lunch_time: time
    dismissal_time: time


def nested(data: dict, path: str) -> object:
    value = data
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


class Calendar:
    def __init__(self, config: Config, store: Store):
        self.config = config
        self.store = store

    def parse(self, payload: dict, target: date, *, mapped: bool = False) -> SchoolDay:
        def field(name: str) -> object:
            return nested(payload, self.config.calendar_fields.get(name, name) if mapped else name)

        if not isinstance(payload, dict) or field("date") != target.isoformat():
            raise CalendarError(f"{target}: 時間割の date が要求日付と一致しません。")
        school = field("is_school_day")
        if not isinstance(school, bool):
            raise CalendarError(f"{target}: is_school_day は true / false が必要です。")
        try:
            times = [
                parse_time(field(name)) if field(name) is not None else getattr(self.config, name)
                for name in ("morning_time", "lunch_time", "dismissal_time")
            ]
        except ConfigError as exc:
            raise CalendarError(f"{target}: 時間割の時刻が不正です。") from exc
        if school and not (times[0] < times[1] and times[0] < times[2]):
            raise CalendarError(f"{target}: 朝の時刻は昼休み・授業終了より前にしてください。")
        return SchoolDay(target, school, *times)

    def normalize_myid(self, payload: dict, target: date) -> dict:
        if not isinstance(payload, dict) or not isinstance(payload.get("dailyPlan"), list):
            raise CalendarError(f"{target}: MyiD の dailyPlan を確認できません。")
        ends = []
        for lesson in payload["dailyPlan"]:
            if not isinstance(lesson, dict) or lesson.get("classDate") != target.isoformat():
                raise CalendarError(f"{target}: MyiD が要求日以外または不正な授業を返しました。")
            if not isinstance(lesson.get("isCanceled"), bool):
                raise CalendarError(f"{target}: MyiD の休講情報が不正です。")
            if lesson["isCanceled"]:
                continue
            try:
                start = time.fromisoformat(lesson["startTime"])
                end = time.fromisoformat(lesson["endTime"])
                if start.tzinfo or end.tzinfo or start >= end:
                    raise ValueError
                ends.append(end)
            except (KeyError, TypeError, ValueError):
                raise CalendarError(f"{target}: MyiD の授業時刻が不正です。") from None
        result = {"date": target.isoformat(), "is_school_day": bool(ends)}
        if ends:
            result["dismissal_time"] = max(ends).strftime("%H:%M")
        return result

    def request_url(self, target: date, end: date | None = None) -> str:
        if self.config.calendar_source != "myid":
            return self.config.calendar_url.replace("{date}", target.isoformat())
        # 貼り付けられた週のまま固定せず、毎回対象日へ置換する。
        parsed = urlsplit(self.config.calendar_url)
        params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        params.update(startDayTos=target.strftime("%Y/%m/%d"), endDayTos=(end or target).strftime("%Y/%m/%d"))
        return urlunsplit(parsed._replace(query=urlencode(params)))

    def read_file(self, path: Path, target: date, *, optional_day: bool = False) -> dict | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            days = payload["days"]
            if not isinstance(days, list) or any(not isinstance(day, dict) or "date" not in day for day in days):
                raise ValueError
            dates = [day["date"] for day in days]
            if len(dates) != len(set(dates)):
                raise ValueError
            matches = [day for day in days if day["date"] == target.isoformat()]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise CalendarError(f"{path.name}: 日程 JSON を読み込めません。") from exc
        if not matches and not optional_day:
            raise CalendarError(f"{target}: 日程がありません。休日も明示してください。")
        return matches[0] if matches else None

    def request_json(self, target: date, end: date | None = None) -> dict:
        headers = {"Accept": "application/json", "User-Agent": "class-diary-remind-bot/0.1"}
        token = os.environ.get("TIMETABLE_API_TOKEN", "")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(self.request_url(target, end), headers=headers)
        try:
            with urlopen(request, timeout=self.config.timeout_seconds) as response:
                # 誤った接続先から巨大な応答を読み込まない。
                body = response.read(2_000_001)
                if len(body) > 2_000_000:
                    raise CalendarError(f"{target}: 時間割の応答が大きすぎます。")
                payload = json.loads(body)
                return payload
        except CalendarError:
            raise
        except HTTPError as exc:
            raise CalendarError(f"{target}: 時間割 API が HTTP {exc.code} を返しました。") from None
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            raise CalendarError(f"{target}: 時間割 API の取得・JSON 解釈に失敗しました。") from None

    def get_http(self, target: date) -> dict:
        payload = self.request_json(target)
        return self.normalize_myid(payload, target) if self.config.calendar_source == "myid" else payload

    def get_http_range(self, start: date, end: date) -> dict[date, dict]:
        payload = self.request_json(start, end)
        if not isinstance(payload, dict) or not isinstance(payload.get("dailyPlan"), list):
            raise CalendarError("MyiD の dailyPlan を確認できません。")
        grouped = {start + timedelta(days=n): [] for n in range((end - start).days + 1)}
        for lesson in payload["dailyPlan"]:
            try:
                day = date.fromisoformat(lesson["classDate"])
            except (KeyError, TypeError, ValueError):
                raise CalendarError("MyiD の授業日が不正です。") from None
            if day not in grouped:
                raise CalendarError("MyiD が要求期間外の授業を返しました。")
            grouped[day].append(lesson)
        return {day: self.normalize_myid({"dailyPlan": lessons}, day) for day, lessons in grouped.items()}

    def cached_day(self, target: date, now: datetime, *, refresh: bool = False) -> SchoolDay | None:
        # ローカル日程は編集がすぐ反映される。HTTP の過去日は保存結果を使う。
        if self.config.overrides_file:
            payload = self.read_file(self.config.overrides_file, target, optional_day=True)
            if payload is not None:
                return self.parse(payload, target)
        if self.config.calendar_source == "file":
            payload = self.read_file(self.config.calendar_file, target)
            if payload is None:
                raise CalendarError(f"{target}: 日程がありません。")
            return self.parse(payload, target)
        if refresh:
            return None
        cached = self.store.cached_schedule(target)
        if cached is not None:
            age = (now - datetime.fromisoformat(cached["fetched_at"])).total_seconds()
            if target < now.date() or 0 <= age < self.config.refresh_seconds:
                try:
                    return self.parse(json.loads(cached["payload"]), target)
                except CalendarError:
                    pass  # 旧形式のキャッシュはAPIから取得し直す。
        return None

    def save_day(self, result: SchoolDay, now: datetime):
        # 保存するのは判定と時刻のみ。科目名・講師名などは保存しない。
        canonical = {"date": result.date.isoformat(), "is_school_day": result.is_school_day}
        for name in ("morning_time", "lunch_time", "dismissal_time"):
            canonical[name] = getattr(result, name).strftime("%H:%M")
        self.store.save_schedule(result.date, canonical, now)

    async def get(self, target: date, now: datetime, *, refresh: bool = False) -> SchoolDay:
        result = self.cached_day(target, now, refresh=refresh)
        if result is None:
            payload = await asyncio.to_thread(self.get_http, target)
            result = self.parse(payload, target, mapped=self.config.calendar_source == "http")
            self.save_day(result, now)
        return result

    async def get_range(self, start: date, end: date, now: datetime) -> tuple[SchoolDay, ...]:
        """担当日照会用。MyiDは最大4週間を一度に取得し、通知と同じ判定を使う。"""
        if not 0 <= (end - start).days < 28:
            raise ValueError("日程の取得期間は1〜28日です。")
        days = [start + timedelta(days=n) for n in range((end - start).days + 1)]
        if self.config.calendar_source != "myid":
            return tuple([await self.get(day, now) for day in days])
        resolved: dict[date, SchoolDay] = {}
        missing: list[date] = []
        for day in days:
            cached = self.cached_day(day, now)
            if cached is None:
                missing.append(day)
            else:
                resolved[day] = cached
        if missing:
            payloads = await asyncio.to_thread(self.get_http_range, missing[0], missing[-1])
            # 全体の検証が終わるまではキャッシュへ保存しない。
            parsed = {day: self.parse(payloads[day], day) for day in missing}
            for day, result in parsed.items():
                self.save_day(result, now)
                resolved[day] = result
        return tuple(resolved[day] for day in days)
