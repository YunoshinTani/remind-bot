# 初めて環境を準備するとき

[READMEへ戻る](../README.md) · [ディレクトリ構成](STRUCTURE.md)

PCは開発用、サーバーは本番用として準備します。本番設定・名簿・トークン・DBは運用サーバーで管理します。導入先固有の接続先や運用の詳細は、開発者へDiscordのDMでお問い合わせください。

## 1. 設置先とファイルを確認する

プロジェクトのルートにはソース・文書・テスト・仮データがあります。本番サーバーの `config.toml`・`roster.csv`・`.env`・`data/` はGit管理や通常のコード更新の対象外です。

## 2. 新しいPCのPython環境を用意する

Python 3.11以降のPCで、プロジェクトのルートから実行します。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
```

既存の `.venv/` は作り直す必要はありません。VS Codeと開発用ツールは [開発ガイド](DEVELOPMENT.md#vs-codeとpython環境) を参照してください。

## 3. PCでは仮データを使う

```powershell
.\.venv\Scripts\python.exe -m remind_bot preview --config examples/config.demo.toml --date 2026-10-15
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v
```

トークンは不要で、Discordへ投稿しません。`examples/` の氏名と予定は架空です。本番の名簿・トークンをPCのルートへコピーして確認する必要はありません。

## 4. 本番の設定はサーバーで管理する

初回だけ運用サーバーで設定例から `config.toml`・`.env`・`roster.csv` を作成します。通知先・当番の起点・時刻・回収場所・時間割の取得先を実際の運用に合わせ、トークンを設定してください。既存の本番を移行する場合は最新のバックアップを引き継ぎます。

CentOS 7向けの同梱環境を使う場合は [初回展開](CENTOS7.md#3-運用サーバーで初回展開する)、設定項目は [リファレンス](CONFIGURATION.md) を参照してください。

## 5. 本番を起動する

運用サーバーで `./bot.sh start`、`./bot.sh status` とログを確認します。サーバーが常時稼働していれば、PCを起動し続ける必要はありません。

通常のコード修正はPCで編集・検査し、非公開の接続設定を用意したうえで `deployment/update_school.ps1` から反映します。設定・名簿・トークン・DBは転送しません。操作は [運用手順](OPERATIONS.md)、公開用ソースの作成は [公開手順](PUBLICATION.md) にあります。
