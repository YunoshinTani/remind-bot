from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Protocol

from .calendar import Calendar, SchoolDay
from .config import Config, Student
from .forecast import DutyForecast
from . import messages
from .rotation import assigned_students, rotation_students, school_day_count
from .store import Store

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Notification:
    day: date
    kind: str
    content: str
    user_ids: tuple[int, ...]
    everyone: bool
    collection_button: bool
    delivery_id: str | None = None


class Sender(Protocol):
    async def ready(self) -> None: ...
    async def send(self, notification: Notification) -> int: ...
    async def show_collected(self, day: date, message_ids: list[int]) -> None: ...


@dataclass(frozen=True)
class Plan:
    school_day: SchoolDay
    students: tuple[Student, ...]


@dataclass(frozen=True)
class ReminderPreview:
    notification: Notification
    students: tuple[Student, ...]
    stored_numbers: tuple[int, ...]

    @property
    def numbers(self) -> tuple[int, ...]:
        return tuple(s.number for s in self.students)


class DiaryService:
    def __init__(self, config: Config, store: Store, calendar: Calendar, sender: Sender | None = None):
        self.config = config
        self.store = store
        self.calendar = calendar
        self.sender = sender
        # 昼の判定・送信と回収ボタンを同じロックで直列化する。
        self.lock = asyncio.Lock()
        # 照会は通知・回収のロックを保持しない。時間割取得が遅くても通知を継続する。
        self.forecast = DutyForecast(store, calendar)

    def require_sender(self) -> Sender:
        if self.sender is None:
            raise RuntimeError("送信先が設定されていません。")
        return self.sender

    async def prepare(self, target: date, now: datetime, *, refresh: bool = False) -> Plan | None:
        if target < self.config.start_date:
            return None
        today = await self.calendar.get(target, now, refresh=refresh)
        if not today.is_school_day:
            return Plan(today, ())
        row = self.store.assignment(target)
        if row is None:
            count = await school_day_count(self.calendar, self.config.start_date, target, now)
            students = rotation_students(self.config, count)
            row = self.store.ensure_assignment(target, tuple(s.number for s in students))
        return Plan(today, assigned_students(self.config, row))

    def notification(self, plan: Plan, kind: str) -> Notification:
        target = plan.school_day.date
        content = messages.notification_text(target, kind, plan.students, self.config.lunch_end_time,
                                             pickup_location=self.config.pickup_location,
                                             submission_location=self.config.submission_location)
        return Notification(
            target, kind, content,
            tuple(s.discord_id for s in plan.students if s.discord_id is not None) if kind != "lunch" else (),
            kind == "lunch", kind in {"morning", "lunch"},
        )

    async def tick(self, now: datetime | None = None):
        explicit_now = now is not None
        now = (now or datetime.now(self.config.timezone)).astimezone(self.config.timezone)
        async with self.lock:
            plan = await self.prepare(now.date(), now)
            if plan is None or not plan.school_day.is_school_day:
                return
            if not explicit_now:
                now = datetime.now(self.config.timezone)
                if now.date() != plan.school_day.date:
                    return
            school = plan.school_day
            events = [("morning", school.morning_time), ("lunch", school.lunch_time)]
            if self.config.dismissal_enabled:
                events.append(("dismissal", school.dismissal_time))
            for kind, clock in events:
                if self.store.notification(school.date, kind) is not None:
                    continue
                scheduled = datetime.combine(school.date, clock, tzinfo=self.config.timezone)
                if now < scheduled:
                    continue
                collected = self.store.assignment(school.date)["collected_at"] is not None
                if collected and kind in {"morning", "lunch"}:
                    self.store.skip(school.date, kind, "collected")
                    continue
                deadline = scheduled + timedelta(minutes=self.config.grace_minutes)
                if kind == "morning":
                    deadline = min(deadline, datetime.combine(school.date, school.lunch_time, tzinfo=self.config.timezone))
                if now >= deadline:
                    self.store.skip(school.date, kind, "expired")
                    log.warning("通知時刻を過ぎたためスキップ: %s %s", school.date, kind)
                    continue
                sender = self.require_sender()
                # チャンネルと権限を確認してから送信枠を確保する。
                await sender.ready()
                # 名前からの照合は ready 内で完了する。最新の名簿で担当を引き直す。
                plan = await self.prepare(school.date, now)
                if plan is None or not plan.school_day.is_school_day:
                    return
                if kind != "lunch" and any(s.discord_id is None for s in plan.students):
                    log.warning("照合できない担当者は氏名で表示して通知します: %s %s", school.date, kind)
                if not self.store.claim(school.date, kind):
                    continue
                try:
                    message_id = await sender.send(self.notification(plan, kind))
                except Exception as exc:
                    # Discord が受理した後に通信が切れた可能性があるため自動再送しない。
                    self.store.finish(school.date, kind, "uncertain", reason=type(exc).__name__)
                    log.error("送信結果を確認できません。Discordを確認してください: %s %s (%s)", school.date, kind, type(exc).__name__)
                else:
                    self.store.finish(school.date, kind, "sent", message_id=message_id)
                    log.info("通知を送信しました: %s %s", school.date, kind)

    async def collect(self, day: date, user_id: int, now: datetime) -> str:
        now = now.astimezone(self.config.timezone)
        async with self.lock:
            if day != now.date():
                return messages.OLD_BUTTON
            row = self.store.assignment(day)
            if row is None:
                return messages.ASSIGNMENT_NOT_FOUND
            changed = self.store.collect(day, user_id, now)
            # 重複押下でも表示の更新を再試行できる。回収記録は最初の1回を保持。
            if self.sender is not None:
                try:
                    await self.sender.show_collected(day, self.store.messages_for_day(day))
                except Exception as exc:
                    log.warning("回収は記録済みですが表示更新に失敗: %s (%s)", day, type(exc).__name__)
                    return messages.COLLECTION_DISPLAY_ERROR
            return messages.collection_reply(changed)

    async def _reminder_preview(self, kind: str, now: datetime) -> ReminderPreview | str:
        if kind not in {"morning", "lunch"}:
            raise ValueError("手動通知は朝または昼を選んでください。")
        await self.require_sender().ready()
        plan = await self.prepare(now.date(), now, refresh=True)
        if plan is None:
            return messages.before_start(self.config.start_date)
        if not plan.school_day.is_school_day:
            return messages.no_school(now.date())
        if self.store.assignment(now.date())["collected_at"] is not None:
            return messages.REMIND_COLLECTED
        stored = tuple(s.number for s in plan.students)
        # 起点日の設定訂正だけを明示的な確認対象にする。他の日の確定担当は変えない。
        if now.date() == self.config.start_date:
            plan = replace(plan, students=rotation_students(self.config, 0))
        return ReminderPreview(self.notification(plan, kind), plan.students, stored)

    async def preview_reminder(self, kind: str, now: datetime | None = None) -> ReminderPreview | str:
        now = (now or datetime.now(self.config.timezone)).astimezone(self.config.timezone)
        async with self.lock:
            return await self._reminder_preview(kind, now)

    async def send_reminder(self, preview: ReminderPreview, request_id: str, user_id: int,
                            now: datetime | None = None) -> str:
        explicit_now = now is not None
        now = (now or datetime.now(self.config.timezone)).astimezone(self.config.timezone)
        async with self.lock:
            if self.store.manual_notification(request_id) is not None:
                return messages.REMIND_PROCESSED
            if preview.notification.day != now.date():
                return messages.REMIND_CHANGED
            current = await self._reminder_preview(preview.notification.kind, now)
            if isinstance(current, str):
                return current
            if current != preview or (not explicit_now and datetime.now(self.config.timezone).date() != now.date()):
                return messages.REMIND_CHANGED
            sender = self.require_sender()
            if not self.store.claim_manual(now.date(), preview.notification.kind, request_id, user_id, now,
                                           preview.numbers, preview.stored_numbers):
                return messages.REMIND_PROCESSED
            notification = replace(preview.notification, delivery_id=request_id)
            try:
                message_id = await sender.send(notification)
            except Exception as exc:
                self.store.finish_manual(request_id, "uncertain", reason=type(exc).__name__)
                log.error("手動通知の送信結果が不明: %s %s (%s)", now.date(), notification.kind, type(exc).__name__)
                return messages.REMIND_UNCERTAIN
            self.store.finish_manual(request_id, "sent", message_id=message_id)
            log.info("管理者が手動通知を送信: %s %s / %s", now.date(), notification.kind, user_id)
            return messages.reminder_sent(now.date(), notification.kind,
                                          f"https://discord.com/channels/{self.config.guild_id}/{self.config.channel_id}/{message_id}")

    async def status(self, now: datetime) -> str:
        now = now.astimezone(self.config.timezone)
        async with self.lock:
            plan = await self.prepare(now.date(), now)
            if plan is None:
                return messages.before_start(self.config.start_date)
            if not plan.school_day.is_school_day:
                return messages.no_school(now.date())
            row = self.store.assignment(now.date())
            uncertain = self.store.uncertain_kinds(now.date())
            return messages.status_text(now.date(), plan.students, collected_at=row["collected_at"],
                                        collected_by=row["collected_by"], uncertain=uncertain)

    async def next_turn(self, user_id: int, now: datetime, *, search_days: int = 90) -> str:
        return await self.forecast.next_turn(self.config, user_id, now, search_days=search_days)
