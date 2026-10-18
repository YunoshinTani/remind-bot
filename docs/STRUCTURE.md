# ディレクトリとファイルの役割

[READMEへ戻る](../README.md)

開発用PCには公開できるコード・文書・仮データを置き、本番設定・名簿・トークン・回収記録は運用サーバーで管理します。公開文書の例は架空の情報です。実際の導入先の詳細は、開発者へDiscordのDMでお問い合わせください。

## PC側の全体像

```text
remind-bot/
├─ remind_bot/          Bot本体
├─ deployment/          配布・更新・起動の補助処理
│  ├─ connection.example.json 接続設定のひな形
│  ├─ connection.local.json   個人用の接続先（非公開）
│  └─ *.local.md             個人用の運用メモ（非公開）
├─ tests/               自動テスト
├─ docs/                利用・運用・開発・公開の手順
├─ examples/            架空の名簿・日程・設定
├─ .vscode/settings.json 開発用のエディタ設定
├─ config.example.toml  サーバー設定のひな形
├─ .env.example         環境変数のひな形
├─ pyproject.toml       Pythonバージョン・依存関係・型チェック設定
├─ requirements.lock    依存ライブラリの固定一覧
├─ LICENCE              ライセンスと著作者表記
├─ .venv/               PCのPython環境（非公開）
├─ .cache/              ビルド用ダウンロード（非公開）
├─ backups/             非公開のバックアップ
└─ dist/                作成した配布物
```

## 各ディレクトリの役割

| 場所 | 内容 |
| --- | --- |
| `remind_bot/` | 設定・時間割・巡回・通知・Discord・DBの処理。対応表は [開発ガイド](DEVELOPMENT.md#変更内容から編集場所を探す) |
| `deployment/` | 同梱版・更新版・公開版の作成、更新の検証・復旧、プロセス管理 |
| `tests/` | 一時データと代用品を使うテスト。本番トークンや名簿は不要 |
| `docs/` | 手順書。全体案内は [README](../README.md#手順書) |
| `examples/` | プレビューと導入時の形式確認に使う架空データ |
| `.vscode/` | Python環境・テスト検出・文字コードなどの開発用設定 |
| `.venv/` | PCで作成する環境。OSをまたいで転送しない |
| `.cache/` | Python・wheelのダウンロードキャッシュ |
| `backups/` | 私有の旧ファイルなど。認証情報・個人情報を含む可能性があり公開不可 |
| `dist/` | 更新tar.gz・同梱tar.gz・公開ZIPの出力先。Git管理外 |

`.vscode/sftp.json` などの接続設定は非公開です。公開用ZIPは `settings.json` だけを選びます。フォルダ全体をそのまま共有しないでください。

## ルート直下のひな形

`config.example.toml` と `.env.example` は公開可能な形式例です。本番用の `config.toml`・`.env` はサーバーで作成し、実際の設定を記入します。`examples/roster.example.csv` の架空氏名も、本番ではサーバー上で実際の名簿に置き換えます。

PCの表示確認は `examples/config.demo.toml` を指定します。相対パスは設定ファイルの場所を基準に解決します。

## 運用サーバーで追加されるもの

```text
~/remind-bot-centos7/
├─ bot.sh               起動・停止・確認用の入口
├─ python/              同梱Python
├─ vendor/              Linux用ライブラリ
├─ config.toml          本番設定（非公開）
├─ roster.csv           実名・Discord ID・在籍状態（非公開）
├─ .env                 Botトークン（非公開）
├─ calendar-overrides.json 日程の上書き（使用時・非公開）
├─ data/                DB・ログ・PIDなど（非公開）
├─ updates/             受け取った更新アーカイブの展開先
└─ backups/             名簿・更新・手動のバックアップ（非公開）
```

DBには日程・確定担当・回収時刻と記録者・送信履歴を保存します。通常のコード更新はこれらを上書きしません。バックアップと復旧は [運用手順](OPERATIONS.md#9-バックアップと復旧) を参照してください。

## 配布物の違い

| 作成プログラム | 用途 | 含むもの |
| --- | --- | --- |
| `deployment/build_update.py` | 既存サーバーのコード更新 | ソース・テスト・文書・設定例 |
| `deployment/build_portable.py` | CentOS 7での初回構築 | 上記とLinux用Python・ライブラリ・起動シェル |
| `deployment/build_public.py` | 新しいGitリポジトリでの公開 | ソース・手順・テスト・ひな形・開発設定・ライセンス |

いずれも本番設定・実名簿・トークン・DB・ログを含めません。公開用ZIPは、さらにGit履歴・個人用接続設定・バックアップ・既存の配布物を除外します。新しい公開先は [公開手順](PUBLICATION.md) に従ってZIPから作成します。

## 変更したい内容から探す

| 変更内容 | 編集する場所 | 手順 |
| --- | --- | --- |
| 通知・コマンドの文言 | PCの `remind_bot/messages.py` | [表示の編集](DEVELOPMENT.md#表示する文字を変更する) |
| 時刻・通知先・回収場所 | サーバーの `config.toml` | [設定変更](OPERATIONS.md#6-通知時刻通知先の変更) |
| 氏名・在籍状態・Discord ID | サーバーの `roster.csv` | [名簿変更](OPERATIONS.md#5-名簿discord名の変更) |
| 休校・行事 | サーバーの日程上書きJSON | [日程変更](OPERATIONS.md#7-休校行事時間割変更への対応) |
| 稼働・ログ | サーバーの `status` と `data/bot.log` | [起動と停止](OPERATIONS.md#2-運用サーバーでの起動と停止) |
| SSH接続先 | PCの `deployment/connection.local.json` | [サーバー反映](DEVELOPMENT.md#サーバーへ反映する) |
