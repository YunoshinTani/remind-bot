"""Discordで表示する通知・返信・案内の本文と共通書式。"""
from __future__ import annotations

from datetime import date, datetime, time
import re
from typing import TYPE_CHECKING

from .config import Student

if TYPE_CHECKING:
    from .schedule import Lesson


def panel(title: str, *paragraphs: str) -> str:
    # Discordの見出しは行頭の「## 」から始める。
    return "\n".join((f"## {title}", *paragraphs))


def date_field(day: date) -> str:
    weekday = "月火水木金土日"[day.weekday()]
    return f"日付：**{day:%Y/%m/%d} ({weekday})**"


def warning(title: str, *paragraphs: str) -> str:
    return "\n".join((f"⚠️ **{title}**", *paragraphs))


def success(title: str, *paragraphs: str) -> str:
    return "\n".join((f"✅ **{title}**", *paragraphs))


def show_collected(content: str, stamp: datetime, collected_by: str | None) -> str:
    # 再押下と旧版の投稿にも対応し、回収済み表示を重複追加しない。
    markers = ("\n回収状況：**✅回収済み**", "\n✅ **回収済み**", "\n✅ 回収済み（")
    if not any(marker in content for marker in markers):
        content = content.rstrip("\n") + f"\n回収状況：**✅回収済み**\n記録時刻：**{stamp:%H:%M}**"
    # 旧版の回収済み投稿にも、保存された最初の記録者だけを補う。
    if collected_by is not None and not re.search(r"(?m)^記録者：", content):
        content = content.rstrip("\n") + f"\n記録者：<@{collected_by}>"
    return content


def student_label(student: Student, *, mention: bool = False) -> str:
    if mention and student.discord_id is not None:
        return f"<@{student.discord_id}>（{student.number}番）"
    return f"{student.number}番 {student.name}".rstrip()


def assignee_field(students: tuple[Student, ...], *, mention: bool = False) -> str:
    names = "、".join(student_label(s, mention=mention) for s in students)
    return f"担当：**{names}**"


def notification_text(day: date, kind: str, students: tuple[Student, ...], lunch_end: time, *,
                      pickup_location: str = "担当教員の部屋の前",
                      submission_location: str = "担当教員の部屋の前") -> str:
    if kind == "morning":
        return panel("日誌の回収をお願いします", date_field(day), assignee_field(students, mention=True),
                     f"{pickup_location}から日誌を回収してください。",
                     "回収できたら「回収しました」を押してください。")
    if kind == "lunch":
        return "@everyone\n" + panel(
            "日誌がまだ回収されていません", date_field(day), assignee_field(students),
            f"回収期限：**{lunch_end:%H:%M}**（昼休み終了）",
            "期限までに日誌を回収してください。\nすでに回収している場合は「回収しました」を押してください。")
    if kind == "dismissal":
        return panel("日誌の提出をお願いします", date_field(day), assignee_field(students, mention=True),
                     f"授業が終わりました。日誌を{submission_location}に提出してください。")
    raise ValueError(f"不明な通知種別: {kind}")


def status_text(day: date, students: tuple[Student, ...], *, collected_at: str | None,
                collected_by: str | None, uncertain: list[str]) -> str:
    state = "✅回収済み" if collected_at else "❌未回収"
    lines = [date_field(day), assignee_field(students), f"回収状況：**{state}**"]
    if collected_at:
        stamp = datetime.fromisoformat(collected_at)
        lines.extend((f"記録時刻：**{stamp:%H:%M}**", f"記録者：<@{collected_by}>"))
    for kind in uncertain:
        label = {"morning": "朝の通知", "lunch": "昼の通知", "dismissal": "提出の通知"}[kind]
        lines.append(warning(f"{label}の送信結果が不明です", "日誌チャンネルの投稿を確認してください。"))
    return panel("今日の日誌当番", *lines)


def next_turn_text(day: date, students: tuple[Student, ...], *, today: date, collected: bool) -> str:
    title = "あなたの日誌当番は今日です" if day == today else "あなたの日誌当番の予定"
    lines = [date_field(day), assignee_field(students)]
    if collected:
        lines.append("回収状況：**✅回収済み**")
    return panel(title, *lines)


def before_start(start: date) -> str:
    return panel("日誌当番の運用開始前です", f"開始日：**{start:%Y/%m/%d}**")


def no_school(day: date) -> str:
    return panel("今日は日誌当番がありません", date_field(day), "今日は登校日ではないため、日誌当番の通知はありません。")


def no_upcoming(search_days: int) -> str:
    return warning("次の担当日を確認できませんでした", f"今後{search_days}日分の時間割では、担当日が見つかりませんでした。",
                   "MyiDの時間割がまだ登録されていない可能性があります。日をおいて、もう一度確認してください。")


def collection_reply(changed: bool) -> str:
    return success("回収を記録しました", "ありがとうございます！") if changed else success("すでに回収済みです", "最初の回収記録を保持しています。")


WRONG_CHANNEL = warning("この場所では利用できません", "日誌チャンネルで使ってください。")
COLLECTION_WRONG_CHANNEL = warning("この場所では回収を記録できません", "設定された日誌チャンネルで使ってください。")
MESSAGE_NOT_FOUND = warning("送信記録が見つかりません", "管理者に確認してください。")
COLLECTION_ERROR = warning("回収ボタンの処理に失敗しました", "もう一度押すか、管理者に確認してください。")
OLD_BUTTON = warning("このボタンは当日のみ使えます", "今日の通知の「回収しました」を押してください。")
ASSIGNMENT_NOT_FOUND = warning("この日の日誌当番が見つかりません", "日誌チャンネルの通知を確認するか、管理者に連絡してください。")
COLLECTION_DISPLAY_ERROR = warning("回収は記録しましたが、表示の更新に失敗しました", "もう一度ボタンを押すと表示の更新を再試行できます。")
STATUS_ERROR = warning("日程または設定を確認できません", "少し待ってから試すか、管理者に確認してください。")
FORECAST_ERROR = warning("日程または設定を確認できませんでした", "少し待ってから試すか、管理者に確認してください。")
FORECAST_TIMEOUT = warning("時間割の確認に時間がかかっています", "少し待ってから、もう一度 `/next` を実行してください。")
FORECAST_BUSY = warning("担当日を確認中です", "少し待ってから、もう一度 `/next` を実行してください。")
MEMBER_NOT_FOUND = warning("名簿の照合ができませんでした", "あなたのDiscordアカウントを在籍中の名簿に照合できませんでした。\n表示名が「番号-氏名」になっているか確認し、管理者に連絡してください。")
SMOKE_PROMPT = panel("🧪 日誌Botの動作テスト", "動作確認用のテストです。", "「回収テスト」を押して動作を確認してください。")
SMOKE_WRONG_CHANNEL = warning("この場所ではテストできません", "テストを投稿したチャンネルで使ってください。")
SMOKE_FINISHED = success("このテストは完了済みです")
SMOKE_ERROR = warning("テストの処理に失敗しました", "もう一度「回収テスト」を押してください。")
SMOKE_EXPIRED = warning("テストの受付を終了しました", "受付時間内のボタン操作を確認できませんでした。")


def smoke_success(stamp: datetime) -> str:
    return success("回収ボタンの動作を確認しました", f"確認時刻：**{stamp:%H:%M}**")


REMIND_ADMIN_ONLY = warning("管理者専用のコマンドです", "サーバーの管理者権限が必要です。")
REMIND_OTHER_USER = warning("この確認画面は操作できません", "コマンドを実行した管理者が操作してください。")
REMIND_COLLECTED = warning("今日はすでに回収済みです", "未回収の通知は送信しません。")
REMIND_CHANGED = warning("確認後に日付・担当・送信内容が変わりました", "もう一度 `/remind` で内容を確認してください。")
REMIND_PROCESSED = warning("この送信操作は処理済みです", "日誌チャンネルの投稿を確認してください。")
REMIND_ERROR = warning("手動通知を準備できませんでした", "設定・時間割・権限を確認し、もう一度 `/remind` を実行してください。")
REMIND_UNCERTAIN = warning("手動通知の送信結果を確認できません", "重複を避けるため自動再送はしません。日誌チャンネルの投稿を確認してください。")
REMIND_CANCELLED = success("手動通知をキャンセルしました")
REMIND_EXPIRED = warning("手動通知の確認期限が切れました", "もう一度 `/remind` を実行してください。")


def reminder_preview(day: date, kind: str, students: tuple[Student, ...], stored_numbers: tuple[int, ...], body: str) -> str:
    label = "朝の通知" if kind == "morning" else "昼の通知"
    lines = [date_field(day), f"種類：**{label}**", assignee_field(students)]
    if tuple(s.number for s in students) != stored_numbers:
        old = "、".join(f"{number}番" for number in stored_numbers)
        lines.extend((f"保存済みの担当：**{old}**",
                      "⚠️ 送信時に、本日の担当を設定に合わせて訂正します。回収・送信履歴は保持します。"))
    lines.append("予定時刻に関係なく送信します。すでに同じ通知がある場合も追加で送信します。")
    if kind == "lunch":
        lines.append("⚠️ 昼の通知は全員へのメンションを送ります。")
    lines.extend(("送信する本文：", body, "内容を確認し、「送信する」または「担当を訂正して送信」を押してください。"))
    return panel("管理者用・手動リマインドの確認", *lines)


def reminder_sent(day: date, kind: str, url: str) -> str:
    label = "朝の通知" if kind == "morning" else "昼の通知"
    return success(f"{label}を手動送信しました", date_field(day), f"[送信した通知を開く]({url})")


SCHEDULE_INVALID_DATE = warning("日付の形式を確認してください", "`2026-10-19` または `2026/10/19` の形式で指定してください。省略すると今日の時間割を表示します。")
SCHEDULE_UNAVAILABLE = warning("授業の詳細を取得できません", "このコマンドはMyiDの時間割を使用する設定で利用できます。")
SCHEDULE_BUSY = warning("時間割を確認中です", "少し待ってから、もう一度 `/schedule` を実行してください。")
SCHEDULE_ERROR = warning("時間割を取得できませんでした", "少し待ってから、もう一度 `/schedule` を実行してください。")
SCHEDULE_TIMEOUT = warning("時間割の取得に時間がかかっています", "少し待ってから、もう一度 `/schedule` を実行してください。")


def schedule_pages(day: date, lessons: tuple[Lesson, ...]) -> tuple[str, ...]:
    if not lessons:
        return (panel("時間割", date_field(day), "授業の登録がありません。",
                      "休日・休校、または時間割が未登録の可能性があります。"),)

    def field(value: str, limit: int = 180) -> str:
        text = value if len(value) <= limit else value[:limit - 1] + "…"
        return re.sub(r"([\\`*_~|\[\]<>])", r"\\\1", text)

    blocks = []
    for lesson in lessons:
        period = f"{lesson.period}限" if lesson.period is not None else "時限未登録"
        clock = f"{lesson.start:%H:%M}〜{lesson.end:%H:%M}" if lesson.start is not None and lesson.end is not None else "時刻未登録"
        label = f"{period}｜{clock}"
        if lesson.cancelled:
            lines = [f"**{label}｜⚠️休講**", f"科目：~~{field(lesson.subject)}~~"]
        else:
            lines = [f"**{label}**", f"科目：**{field(lesson.subject)}**"]
        detail = []
        if lesson.room:
            detail.append(f"教室：{field(lesson.room, 120)}")
        if lesson.lecturer:
            detail.append(f"担当：{field(lesson.lecturer, 120)}")
        if detail:
            lines.append("　".join(detail))
        blocks.append("\n".join(lines))
    prefix = [date_field(day)]
    if all(lesson.cancelled for lesson in lessons):
        prefix.append("**全授業が休講です。**")
    pages = []
    body = list(prefix)
    for block in blocks:
        if len(panel("時間割", *body, block)) > 1900:
            pages.append(panel("時間割", *body))
            body = list(prefix)
        body.append(block)
    pages.append(panel("時間割", *body))
    if len(pages) > 1:
        pages = [page.replace("## 時間割\n", f"## 時間割（{index}/{len(pages)}）\n", 1)
                 for index, page in enumerate(pages, 1)]
    return tuple(pages)
