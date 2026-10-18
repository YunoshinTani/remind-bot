# 公開用ソースと新しいリポジトリ

[READMEへ戻る](../README.md) · [ディレクトリ構成](STRUCTURE.md)

公開版には架空の氏名・名簿・時間割と、一般化した運用手順を含めます。実際のDiscordメッセージへのリンクや個別の在籍・照合結果は掲載しません。導入先固有の詳細が必要な場合は、開発者へDiscordのDMでお問い合わせください。

## 公開用ZIPを作る

プロジェクトのルートから実行します。

```powershell
.\.venv\Scripts\python.exe deployment/build_public.py
```

`dist/remind-bot-public-バージョン.zip` に、ソース・文書・テスト・設定例・ライセンス・開発用設定を保存します。対象は `deployment/layout.py` の許可リストです。同名ZIPは上書きしません。作り直す場合は `--output dist/remind-bot-public-revised.zip` のように別名を指定してください。

次のものは公開用ZIPへ含めません。

- `.git/` と過去のコミット・名前・メールアドレス
- 本番設定・トークン・実名簿・日程上書き・DB・ログ
- `deployment/connection.local.json`、`operations.local.md` などの個人用ファイル
- SFTPの個人用設定、`.venv/`、キャッシュ、バックアップ、過去の配布物

## 新しいリポジトリを始める

1. 公開用ZIPを別の空のフォルダへ展開する。
2. 展開された `remind-bot/` を新しい作業フォルダとして開く。
3. 公開するGitの名前・メールアドレスを確認する。匿名性が必要なら、初回コミット前に公開用の値へ設定する。
4. 新しいリポジトリを作り、変更を確認して初回コミットする。
5. 新しい公開先に接続し、公開・タグ・リリースを作成する。

```sh
git init
git add .
git diff --cached --stat
git commit -m "Initial public release"
git tag -a v0.1.0 -m "Initial public release"
```

古い作業フォルダの `.git/` をコピーしたり、古いブランチ・タグを新しい公開先へpushしたりすると、以前の情報が履歴に戻ります。公開用ZIPから始めてください。旧リポジトリの削除や新しい公開先の作成は、利用しているGitホスティングサービスで行います。

このZIPにGit履歴はありませんが、新しく作るコミットの名前・メールアドレスと、`LICENCE` の著作者表記は公開対象です。ライセンスの著作者表記は維持しています。

## 非公開ファイルの扱い

既存の `backups/` や過去の同梱パッケージには、個人情報・トークンが含まれる場合があります。Git管理外でも、Releaseへの手動添付やフォルダ全体の共有には自動で適用されません。公開用には `remind-bot-public-*.zip` を使い、バックアップや個人用ファイルを添付しないでください。

実際のサーバー更新には、元の開発環境で `connection.local.json` を使うか、新しいPCで非公開の接続設定を作り直します。移行時の設定手順は [開発ガイド](DEVELOPMENT.md#サーバーへ反映する) を参照してください。
