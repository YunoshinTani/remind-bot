# CentOS 7での構築・更新

[READMEへ戻る](../README.md) · [ディレクトリ構成](STRUCTURE.md)

Pythonとライブラリをホーム配下へ同梱し、システムPythonを変更せずに実行する手順です。対象はLinux x86_64・glibc 2.17です。接続先はすべて架空の例で、実際の運用先については開発者へDiscordのDMでお問い合わせください。

既存サーバーのコード修正は [7. 運用中の更新](#7-運用中の更新) へ進んでください。新しい同梱パッケージにも本番設定・トークン・実名簿・DBは含めません。既存環境の復旧にはサーバーの最新バックアップを使います。

## 1. PC側で同梱パッケージを作る

Python 3.11以降の開発環境で、プロジェクトのルートから実行します。

```powershell
.\.venv\Scripts\python.exe deployment/build_portable.py
```

配布物は `dist/`、ダウンロードのキャッシュは `.cache/` です。実行環境の情報は `deployment/runtime.json`、追加ライブラリの固定一覧は `deployment/requirements-extra.lock` にあります。本番データを同梱するオプションはありません。通常のコード変更では同梱環境の作り直しは不要です。

## 2. PCから運用サーバーへ転送する

最新の配布物を選び、自分の接続先に置き換えて転送します。

```powershell
$botBundle = Get-ChildItem -LiteralPath '.\dist' -Filter 'remind-bot-centos7-*.tar.gz' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
scp $botBundle.FullName 'botuser@server.example.invalid:remind-bot-centos7.tar.gz'
ssh botuser@server.example.invalid
```

パスワードは通常のSSH画面へ入力します。SFTPでホーム内へアップロードしても構いません。VS Code Serverは不要です。必要ならPCの `Get-FileHash` とサーバーの `sha256sum` でSHA-256を比較します。

## 3. 運用サーバーで初回展開する

以下はホーム内にまだ設置先がない場合だけ実行します。既存の本番フォルダへ重ねて展開しません。

```sh
cd ~
umask 077
chmod 600 remind-bot-centos7.tar.gz
tar -xzf remind-bot-centos7.tar.gz
cd ~/remind-bot-centos7
./bot.sh version
cp config.example.toml config.toml
cp .env.example .env
cp examples/roster.example.csv roster.csv
chmod 600 config.toml .env roster.csv
```

サーバー上で通知先・起点・時刻・回収場所・MyiD URL・実際の名簿・Botトークンを設定します。例の値のまま起動しません。既存環境から移行する場合は、ひな形のコピーを省き、最新の設定・名簿・トークン・日程・DBを引き継ぎます。

## 4. 実行環境と通信を確認する

```sh
./bot.sh doctor
./bot.sh members
./bot.sh unit-tests
```

`doctor` はライブラリ・設定・HTTPS・WebSocket・時間割取得を確認し、投稿や本番DB変更は行いません。名簿照合やチャンネルへの権限は `members` と、必要なら `test` で確認します。

```sh
./bot.sh test
```

メンションなしの投稿で「回収テスト」を5分以内に押します。本番の当番・回収記録は変更しません。テスト中は通常のBotを停止してください。

## 5. 本番起動する

```sh
./bot.sh start
./bot.sh status
tail -n 30 data/bot.log
```

今回の起動後の「Discord に接続しました」を確認します。一度ログアウトし、再接続して稼働が続くことも確認してください。サーバーが稼働していればPCは停止できます。通知はサーバーの設定時刻に従います。

## 6. 停止・再起動・ログ

```sh
cd ~/remind-bot-centos7
./bot.sh stop
./bot.sh start
./bot.sh status
tail -n 50 data/bot.log
tail -f data/bot.log
```

ログ追跡の `Ctrl+C` はログ表示だけを終了します。画面上で直接動かす `./bot.sh run` の場合はBotを停止します。サーバー再起動後の自動起動・異常終了時の自動復帰は別途設定が必要です。

## 7. 運用中の更新

### 通常のコード更新

PCの `deployment/connection.example.json` を `deployment/connection.local.json` へコピーし、実際のSSH接続先・絶対パスを記入します。既存の個人用設定は上書きしません。接続設定はGit管理外です。詳しくは [開発ガイド](DEVELOPMENT.md#サーバーへ反映する) を参照してください。

```powershell
.\deployment\update_school.ps1
```

PCでテスト後、SCP転送とSSH適用を実行します。通常のパスワード認証では2回入力を求められます。サーバー側で停止・コードのバックアップ・既存設定の検証・自動テスト・再起動・新しい接続ログの確認を行います。失敗した場合は変更前のコードへ戻します。

本番の `config.toml`・`.env`・名簿・日程上書きJSON・`data/` は作成側でも適用側でも通常更新から除外します。設定・名簿の変更はサーバー上の最新版に対して行ってください。

転送せずアーカイブだけ作る場合は `-PrepareOnly` を指定します。`UPDATE OK` の後はDiscordで変更した表示・コマンドを確認します。

| 変更内容 | 反映方法 |
| --- | --- |
| 通知文・コマンド・処理・文書 | 通常のコード更新 |
| 時刻・場所・チャンネル・名簿 | サーバーの設定を編集し、検証・再起動 |
| `bot.sh` | 停止・バックアップ後に個別転送。LF改行と実行権限を確認 |
| Python・外部ライブラリ | 対応する同梱環境を更新 |
| DBの保存形式 | 既存記録の移行処理と復旧方法を検証 |

更新の許可範囲は `deployment/layout.py` で管理します。Pythonコード・テスト・文書・設定例・ライセンスが対象です。既知の廃止ファイルは `RETIRED_FILES` で明示し、復旧用に保存してから取り除きます。

### SFTPで個別に反映する場合

PCで関連する変更とテストを済ませ、サーバーのBotを停止・バックアップしてから対応するファイルへ転送します。保存時の自動アップロードで編集中のコードを本番へ送らないようにしてください。

例えば `remind_bot/messages.py` は、サーバーでも設置先の `remind_bot/messages.py` です。関連ファイルをすべて転送し、`check`・必要なテストを確認してから起動します。PCの `deployment/bot.sh` だけは、サーバー設置先直下の `bot.sh` に対応します。

```sh
chmod 700 bot.sh
./bot.sh check
./bot.sh unit-tests
./bot.sh start
./bot.sh status
```

依存関係一覧の転送だけではライブラリ本体は更新されません。Linux向け `vendor/` を用意し、Windowsの `.venv` は転送しないでください。更新時もサーバーの最新設定・名簿・DBを保持します。

## 動かない場合

| 症状 | 次の確認 |
| --- | --- |
| `Could not resolve hostname` | 普段使う正式なホスト名・IP、必要なネットワーク接続 |
| `Permission denied` | 接続権限、ホーム内の実行許可、ファイルの実行権限 |
| `GLIBC_... not found` | `getconf GNU_LIBC_VERSION` と同梱環境の対象条件 |
| `WEBSOCKET OK` が出ない | HTTPSとは別にWebSocketが許可されているか |
| 起動直後に終了 | `data/bot.log` の今回の起動後のエラー |
| `PyNaCl` / `davey` 警告 | 音声用ライブラリの警告。通知・ボタン用の追加導入は不要 |

配布物のSHA-256と構成は `bundle-manifest.json` に記録します。Python配布物は配布元のハッシュと照合し、ライブラリのライセンス情報も同梱します。
