# 設定と動作のリファレンス

[READMEへ戻る](../README.md) · [ディレクトリ構成](STRUCTURE.md)

設定値と時間割データの形式を確認するための文書です。新規構築の順序は [初期設定](START_HERE.md)、稼働中の変更操作は [運用手順](OPERATIONS.md) を参照してください。

## 設定ファイル

設定の全項目は [config.example.toml](../config.example.toml)、環境変数の名前は [.env.example](../.env.example) にあります。例の時刻・日付・人数は仮の値です。

| ファイル・項目 | 設定するもの |
| --- | --- |
| `.env` | `DISCORD_TOKEN`、必要な場合の `TIMETABLE_API_TOKEN` |
| `config.toml` の `[discord]` | サーバー・通知先チャンネルのID、名前からの照合の有効化 |
| `[rotation]` | 名簿のパス、巡回の起点日、最初の組番号 |
| `[notifications]` | タイムゾーン、通知時刻、回収期限、回収・提出場所、提出通知の有効化、通知の猶予・判定間隔 |
| `[calendar]` | 日程の取得方法、ファイル・URL、通信時間制限、再取得間隔、日程上書きファイル |
| `[calendar.fields]` | 汎用GET応答の項目名の対応 |
| `[storage]` | データベースの保存先 |
| `roster.csv` | 出席番号、氏名、DiscordユーザーID、在籍状態、Discord上の名前 |

設定内の相対パスは、**`config.toml` が置かれたフォルダ**を基準に解決します。`config.toml`・名簿・`.env`・日程上書きJSONは、学校サーバー側の最新ファイルを編集します。通常のコード更新ではこれらを転送・上書きしません。PCには本番ファイルを置かず、仮データの `examples/config.demo.toml` で表示を確認します。

`first_pair` は対象外の人を除く前の組番号です。1は1・2番、2は3・4番です。組の一人が `active=false` ならもう一人だけが担当します。氏名・在籍状態・ID・時刻・巡回の起点は、サーバーの設定に従います。公開例は架空の情報です。導入先固有の詳細は開発者へDiscordのDMでお問い合わせください。

`pickup_location`・`submission_location` は1〜200文字の1行で指定し、実際の教員名や場所はサーバーの設定だけに記載します。省略時はいずれも「担当教員の部屋の前」です。

## Discordと名簿の設定

[Developer Portal](https://discord.com/developers/applications) でアプリケーションと Bot を作成し、対象サーバーに追加します。インストール時のスコープは `bot` と `applications.commands` を指定します。

日誌チャンネルで次の権限を与えてください。

- チャンネルを見る
- メッセージを送信
- メッセージ履歴を読む
- `@everyone`、`@here` とすべてのロールにメンション

名前からの照合では **Server Members Intent を ON** にしてください。管理者権限、Message Content Intent は使用しません。[Discord のメンバー一覧取得仕様](https://github.com/discord/discord-api-docs/blob/main/developers/resources/guild.mdx#list-guild-members)

回収ボタンは、日誌チャンネルを見られるサーバーメンバーなら誰でも押せます。代理回収にも対応し、最初に押した人の ID と時刻を記録します。

### 表示名からメンションする

`resolve_members=true` では、`01-氏名` というサーバーニックネーム・表示名・ユーザー名を名簿と完全一致で照合し、実際の `<@ユーザーID>` に変換して送信します。番号だけで推測しません。重複した名前や一致しない人は警告し、通知には出席番号と名簿の氏名を表示します。もう片方を照合できる場合は通常どおりメンションします。両方を照合できない場合も氏名付きの通知を送ります。昼の全体通知でも未照合の担当者は氏名を表示します。

例外的なユーザー名は、名簿の `discord_name` 列で追加できます。名簿に保存した `discord_id` は名前より優先します。照合は最長5分間キャッシュします。

同期・氏名変更の操作は [運用手順の「名簿・Discord名の変更」](OPERATIONS.md#5-名簿discord名の変更) を参照してください。

`--save` は氏名・休学状態・未解決の行を保持します。一部が未解決でも、照合できた分は保存します。通常のBotを停止して実行し、保存後に再起動してください。

Bot が `@everyone` を送信しても、受信側の通知設定によって通知の出方は変わります。サーバーの既定通知設定は導入先の運用方針に合わせて手動で設定してください。[Discord のメンション仕様](https://docs.discord.com/developers/resources/message#allowed-mentions-object)

## 時間割の形式

### ファイルで登校日を設定する場合

`calendar.source = "file"` とし、`calendar.json` に開始日からの**全日付**を記載します。

```json
{
  "days": [
    {"date": "2026-10-15", "is_school_day": true},
    {"date": "2026-10-16", "is_school_day": true, "lunch_time": "12:00"},
    {"date": "2026-10-17", "is_school_day": false}
  ]
}
```

土日・祝日・長期休暇も `false` として明示します。土日の登校にも対応できます。日程が欠けている場合、誤って当番を進めないよう判定を保留します。朝の通知は昼休み・授業終了より前に設定します。午前中だけの授業日にも対応します。

### MyiD APIを使う場合

`calendar.source = "myid"` として、利用する時間割のGET URL を `calendar.url` に設定します。URLの `startDayTos` と `endDayTos` は、判定する日付の `YYYY/MM/DD` に毎回置き換えます。URLに含まれていた期間に固定されることはありません。

応答の `dailyPlan` に対象日の授業があり、`isCanceled=false` の授業が1件以上あれば登校日とします。授業なし・全件休講なら通知せず、担当も進めません。APIエラーや不正な応答は休校と判断せず保留します。授業がない学校行事など、APIに載らない登校日は `overrides_file` で上書きしてください。

授業終了時刻は、その日の有効な授業の最終終了時刻を使います。提出リマインドは `dismissal_enabled` で切り替えます。朝と昼の通知には、設定した `morning_time` と `lunch_time` を使います。変更操作は [運用手順](OPERATIONS.md#6-通知時刻通知先の変更) を参照してください。

設定した回収期限を過ぎても未回収の場合、追加の通知や自動処理は行いません。回収状況は未回収のまま残り、回収ボタンは当日中であれば押せます。回収の判断はボタンの記録によります。

### GET API を使う場合

```toml
[calendar]
source = "http"
url = "https://example.invalid/timetable?date={date}"
timeout_seconds = 10
refresh_seconds = 300
```

`{date}` を `YYYY-MM-DD` に置き換えて GET します。最低限の応答は次の形式です。

```json
{"date": "2026-10-15", "is_school_day": true}
```

`morning_time`・`lunch_time`・`dismissal_time` を返すと、その日の時刻を上書きできます。日付が要求と異なる場合や、登校日の値が真偽値でない場合は通知しません。認証が必要なら `.env` の `TIMETABLE_API_TOKEN` を Bearer トークンとして使用します。

JSON の階層が異なる場合はキーを対応付けられます。

```toml
[calendar.fields]
date = "data.date"
is_school_day = "data.is_school_day"
lunch_time = "data.lunch_start"
```

授業配列から登校日を判定する形式、日付形式の変換、独自認証などは、実際の API 仕様に合わせて `remind_bot/calendar.py` のアダプターを調整する必要があります。

通知・巡回の登校日判定では、当日の日程は既定で5分ごとに更新し、取得した過去の日程はデータベースに保持します。開始日から当日までの履歴を取得できる必要があります。過去を返さない API の場合は、`overrides_file` に同じ `days` 形式のファイルを指定して過去日程を補ってください。上書きファイルは API より優先されます。

## 保存する情報と判定の方針

- `[storage].database` で指定したSQLiteに、日程キャッシュ・確定した担当・回収記録・送信履歴を保存します。
- 当日の担当は一度確定すると保存され、日程変更で自動的に入れ替わりません。
- 通知には設定した猶予時間があり、期限を過ぎた古い通知は送信しません。送信直前に記録し、通信結果が不明な場合は自動再送を止めます。
- APIエラーや欠けた日程から登校日・休校日を推測せず、判定を保留します。
- `/next` は未来の担当・回収・通知記録を確定せず、時間割に基づく予定を表示します。
- `/schedule` は任意の日の授業詳細を毎回MyiDから取得し、科目・教室・教員名をDBに保存しません。通知用の日程キャッシュも更新しません。

日常の確認方法、回収期限後の動作、バックアップ、起点の変更は [運用手順](OPERATIONS.md) にまとめています。データの配置は [構成ガイド](STRUCTURE.md)、テストの実行は [開発ガイド](DEVELOPMENT.md#自動テストの構成) を参照してください。
