# 音声用ライブラリの警告をなくす

[READMEへ戻る](../README.md) · [CentOS 7の手順](CENTOS7.md)

`PyNaCl is not installed` と `davey is not installed` は、Bot起動時にそれぞれ1回だけ出る警告です。同じ時刻の行がログに残っているだけなら、増え続けていません。時刻が変わって何度も出る場合は、Botや確認コマンドの起動が繰り返されていないか確認します。

ライブラリを追加すると今後の警告は出なくなりますが、使用容量は数MB増えます。容量節約のための変更ではありません。日誌Botで音声を使う設定や、音声チャンネルへの接続は追加しません。

## PCで追加パッケージを作る

プロジェクトのルートから実行します。

```powershell
.\.venv\Scripts\python.exe -X utf8 deployment/build_voice.py
```

`dist/voice-libs-centos7.tar.gz` とその `.sha256` を作ります。対象は同梱版と同じCPython 3.11・Linux x86_64・glibc 2.17です。`requirements.lock` に固定したPyNaCl・davey・cffi・pycparserをまとめ、設定・名簿・DB・トークン・Python本体は含めません。システムへのインストールやサーバー上でのビルドは不要です。

新しく作る同梱版にもこれらを含めます。通常のコード更新は `vendor/` を更新しないため、既存サーバーには下記の追加操作が必要です。

## PCから転送する

既存の非公開接続設定を使う例です。パスワードは通常のSSH画面に入力します。

```powershell
$botConnection = Get-Content -LiteralPath .\deployment\connection.local.json -Raw | ConvertFrom-Json
scp .\dist\voice-libs-centos7.tar.gz .\dist\voice-libs-centos7.tar.gz.sha256 "$($botConnection.destination):$($botConnection.remote_root)/"
ssh $botConnection.destination
```

SFTPで2ファイルを設置先へアップロードしても構いません。接続先の値や本番設定を公開する必要はありません。

## サーバーで追加する

まず、Botを動かしたまま別フォルダに展開して読み込みを確認します。各コマンドが成功してから次へ進みます。

```sh
cd ~/remind-bot-centos7
sha256sum -c voice-libs-centos7.tar.gz.sha256
mkdir -p updates
voice_dir=$(mktemp -d updates/voice-XXXXXXXX)
tar -xzf voice-libs-centos7.tar.gz -C "$voice_dir"
PYTHONPATH="$PWD/$voice_dir/vendor:$PWD/vendor" ./python/bin/python3 -s "$voice_dir/check_voice.py"
```

`VOICE OK` が出た場合だけ、続けて実行します。失敗した場合は、既存のBot・ライブラリは変更されていません。

```sh
./bot.sh stop
cp -a "$voice_dir/vendor/." vendor/
PYTHONPATH="$PWD/vendor" ./python/bin/python3 -s "$voice_dir/check_voice.py"
./bot.sh start
./bot.sh status
tail -n 10 data/bot.log
```

2回目も `VOICE OK` が出てから再開してください。設定・名簿・回収記録は変更しません。既存の `vendor/` を別用途にも使っている場合は、更新前にバックアップを取ってください。

以前の警告行はログに残ります。今回の起動時刻以降に警告が出ていないことを確認します。実際のCentOS 7上での読み込みは最初の `VOICE OK` で確認してください。

## PCの開発環境にも追加する場合

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -X utf8 deployment/check_voice.py
```

新しいPCでは `pyproject.toml` の `discord.py[voice]` に従って導入されます。既存の `.venv` は上記で更新できます。

音声用依存の指定は [discord.py公式の導入手順](https://discordpy.readthedocs.io/en/stable/intro.html#installing)、対応wheelは [PyNaCl](https://pypi.org/project/PyNaCl/1.5.0/) と [davey](https://pypi.org/project/davey/0.1.7/) の配布情報を参照しています。
