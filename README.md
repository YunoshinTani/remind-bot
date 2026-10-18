# クラス日誌リマインダー

Discordで日誌当番の通知・回収記録・次の担当日の照会を行うBotです。MyiDや日程ファイルを使って登校日を判定し、名簿の順番で当番を割り当てます。

- 朝：担当者へメンションし、回収ボタンを表示。
- 昼：未回収の場合に `@everyone` で通知。
- 回収ボタン：最初に押した人と時刻を記録し、通知を回収済み表示へ更新。
- `/today`：今日の当番と回収状況を本人だけに表示。
- `/next`：今日を含む次の担当日を本人だけに表示。
- `/schedule`：今日または指定日の時間割を本人だけに表示。
- `/remind`：管理者が朝・昼を選び、確認後に手動通知。

氏名・在籍状態・通知時刻・回収場所は導入先の設定に従います。Discordで照合できない担当者も番号・氏名で表示します。休学などで対象外の人は当番から除外できます。

## 公開している情報

例に使う氏名・名簿・時間割は架空のデータです。実際のDiscordメッセージへのリンク、個別の在籍・参加状況、運用サーバーの接続先は掲載していません。

**導入先固有の詳細が必要な場合は、開発者へDiscordのDMでお問い合わせください。** 個人情報・認証情報を公開リポジトリやIssueへ投稿しないでください。

## 運用方針

| 管理するもの | 場所・操作 |
| --- | --- |
| コード・文書・仮データ | 開発用PCで編集・テストし、更新スクリプトで反映 |
| 通知時刻・当番の起点・回収場所・時間割URL | 運用サーバーの `config.toml` |
| 名簿・DiscordユーザーID | 運用サーバーの `roster.csv`。`members --save` もサーバーで実行 |
| Botトークン・回収記録・ログ | 運用サーバーの `.env` と `data/` |
| SSH接続先 | PCの `deployment/connection.local.json`。Git管理・配布の対象外 |

本番の設定・名簿・トークン・DBを通常のコード更新で上書きしません。PCでのプレビューは `examples/` の仮データを使います。

## 手順書

| 確認したいこと | 文書 |
| --- | --- |
| ディレクトリ・設定ファイルの役割 | [構成ガイド](docs/STRUCTURE.md) |
| 起動・停止、名簿・通知時刻・場所の変更 | [運用手順](docs/OPERATIONS.md) |
| CentOS 7向け同梱環境と更新・復旧 | [CentOS 7の手順](docs/CENTOS7.md) |
| PyNaCl・daveyの追加と警告の解消 | [音声用ライブラリ](docs/VOICE_LIBRARIES.md) |
| コードの編集、VS Code、テスト | [開発ガイド](docs/DEVELOPMENT.md) |
| 新しいPC・サーバーの準備 | [初期設定](docs/START_HERE.md) |
| 設定形式とAPI応答 | [設定リファレンス](docs/CONFIGURATION.md) |
| 架空のDiscord表示例 | [表示サンプル](docs/DISPLAY_EXAMPLES.md) |
| 検証方法・確認範囲 | [動作確認](docs/TEST_RESULTS.md) |
| 公開用ZIP・新しいGitリポジトリ | [公開手順](docs/PUBLICATION.md) |

## フォルダ構成

```text
remind-bot/
├─ remind_bot/       Bot本体
├─ deployment/       配布・更新・起動の補助プログラム
├─ tests/            自動テスト
├─ docs/             運用・設定・開発の説明
├─ examples/         架空の設定・名簿・時間割
├─ .vscode/          開発用Pythonとテスト検出の設定
├─ config.example.toml 設定のひな形
├─ .env.example      環境変数のひな形
├─ .venv/            PCの開発環境（配布対象外）
├─ backups/          非公開のバックアップ（配布対象外）
└─ dist/             更新・公開用アーカイブの出力先
```

## PCでの確認

Python 3.11以降を使い、プロジェクトのルートから実行します。どちらもDiscordへ投稿しません。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m remind_bot preview --config examples/config.demo.toml --date 2026-10-15
```

## 編集から更新まで

通知文は `remind_bot/messages.py`、実際の回収・提出場所はサーバーの設定で管理します。接続先の準備と更新手順は [開発ガイド](docs/DEVELOPMENT.md#サーバーへ反映する) を参照してください。

```powershell
.\deployment\update_school.ps1
```

PCのテスト後にコード・文書を転送し、サーバーで既存設定の検証・テスト・再起動・接続確認を行います。失敗時は変更前のコードへ戻します。通常のコード修正では、同梱環境全体の作り直しは不要です。
