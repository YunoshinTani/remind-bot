# 開発と変更の反映

[READMEへ戻る](../README.md) · [ディレクトリ構成](STRUCTURE.md)

PCでコード・文書を編集し、テスト後にサーバーへ反映します。本番の設定・名簿・トークン・DBは運用サーバーで管理します。公開サンプルの氏名・予定は架空です。実際の導入先の詳細は開発者へDiscordのDMでお問い合わせください。

## VS CodeとPython環境

プロジェクトのルートを開き、Pythonインタープリターに `.venv/Scripts/python.exe` を選びます。新しいPCは [初期設定](START_HERE.md#2-新しいpcのpython環境を用意する) に従って準備します。

型チェック用ツールを追加する場合は、開発用環境だけにインストールします。サーバーへの追加導入は不要です。

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
```

Pylanceの指摘は実行時エラーとは限りませんが、無視せず内容を確認します。Python環境の選択、ライブラリの導入、型や継承の不整合を確認してください。`addCleanup` の認識にはテスト用基底クラスの継承、`log_message` の上書きには引数名・型の互換性も関係します。

## 変更後に確認する

プロジェクトのルートから実行します。いずれも本番への投稿は行いません。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pyright
.\.venv\Scripts\python.exe -m pyright --pythonplatform Linux
.\.venv\Scripts\python.exe -m remind_bot preview --config examples/config.demo.toml --date 2026-10-15
```

表示だけを変更した場合も、日時・担当・状態・メンションなどが維持されているか確認します。テストで特定の言い回しを期待している場合は、意図した変更と本来の検証目的を確認してから修正します。失敗を無視して更新しないでください。

## 変更内容から編集場所を探す

| ファイル | 主な役割 |
| --- | --- |
| `remind_bot/messages.py` | 通知・コマンド・ボタンの表示、日付・見出しなどの共通書式 |
| `remind_bot/config.py` | TOML・名簿の読み込みと検証 |
| `remind_bot/calendar.py` | 登校日判定、時間割API・ファイル・上書き |
| `remind_bot/rotation.py` | 登校日数に基づく担当巡回 |
| `remind_bot/service.py` | 通知判定・回収・手動通知・照会の調整 |
| `remind_bot/forecast.py` | 次の担当日の計算 |
| `remind_bot/discord_app.py` | Discord接続、コマンド・ボタン、メンバー照合との連携 |
| `remind_bot/members.py` | Discordの名前・ID照合と名簿保存 |
| `remind_bot/store.py` | SQLite保存と重複防止 |
| `remind_bot/__main__.py` | PC・サーバー共通のコマンド入口 |
| `deployment/layout.py` | 更新・公開するファイルの許可範囲 |
| `deployment/apply_update.py` | サーバー更新、既存設定の検証、失敗時の復旧 |
| `deployment/control.py` | 同梱版プロセスの起動・停止・確認 |

## 表示する文字を変更する

一般的な文章は `messages.py` を変更します。実際の回収・提出場所はサーバーの `[notifications]` の `pickup_location`・`submission_location` で設定してください。個人名や導入先固有の情報をソースへ書き込まないでください。

`panel()` の見出しにはタイトルだけを渡します。共通処理が `## ` と改行を付けます。本文は空行を入れずに1項目ずつ改行し、担当の区切りは「、」を使います。注意・エラーは `warning()`、成功は `success()` を使います。[表示サンプル](DISPLAY_EXAMPLES.md) を参照してください。

Pythonの `f"...{value}..."` は変数の値を文章へ埋め込む書式です。文言だけを変える場合も `{...}` 内の処理や必要な情報を消さないようにします。更新・再起動後の新しい投稿や返信に反映され、過去の投稿を一括で書き換えません。

## 自動テストの構成

`tests/helpers.py` に一時設定・架空のID・Discordの代用品をまとめ、機能別の `tests/test_*.py` で検証します。実際の学校予定や在籍状況を再現するためのデータではありません。

主に日程・巡回・通知の重複防止・回収記録・未照合時の表示・管理者権限・時間割照会・更新の保護と復旧を確認します。公開ZIPについても本番ファイルやGit履歴を除外することを検証します。確認範囲は [動作確認](TEST_RESULTS.md) にあります。

## サーバーへ反映する

初めてのPCだけ、接続設定を作ります。既に `connection.local.json` がある場合はコピーせず、その内容を確認してください。

```powershell
Copy-Item -LiteralPath deployment/connection.example.json -Destination deployment/connection.local.json
```

`destination` を実際の `ユーザー名@ホスト名`、`remote_root` をホーム配下の設置先の絶対パスに変更します。末尾は `/remind-bot-centos7` です。スペース・親ディレクトリ参照は使えません。パスワードは保存せず、SSH画面で入力します。例示用の `.invalid` ホストでは更新を開始しません。

```powershell
.\deployment\update_school.ps1
```

PCのテスト後に転送し、サーバーで停止・バックアップ・検証・テスト・再起動・接続確認を行います。失敗時はコードを復旧します。接続設定を別の非公開ファイルに置く場合は `-ConnectionFile` で指定できます。`-PrepareOnly` は接続設定なしで更新アーカイブだけを作ります。

本番設定・トークン・名簿・日程上書き・DB・ログ・個人用接続設定は転送しません。通常更新にはPython本体・ライブラリ・`bot.sh` を含めません。これらを変える場合は [CentOS 7の更新手順](CENTOS7.md#7-運用中の更新) を参照してください。

## コマンドの実装上の注意

`/remind` は管理者限定で、本人だけの確認画面と実行時の再検証を行います。確認後に日程・担当・回収状態が変わった場合は送信しません。予定通知と手動通知で回収記録・重複防止を共有します。

`/schedule` は照会ごとにMyiDから取得し、日誌の担当・通知記録・キャッシュを変更しません。長い出力は非公開メッセージに分割します。回収ボタンの永続識別子を変更すると既存投稿が操作できなくなるため、安易に変更しないでください。

公開用のソースZIPとGit履歴の扱いは [公開手順](PUBLICATION.md) を参照してください。
