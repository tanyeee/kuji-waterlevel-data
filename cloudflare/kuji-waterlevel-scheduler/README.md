# kuji-waterlevel-scheduler（Cloudflare Worker）

Cloudflare の Cron Trigger から、このリポジトリの GitHub Actions を `workflow_dispatch` で起動する Worker です。GitHub Actions の `schedule` はこのプロジェクトでは起動が不規則なため主系にせず、各 workflow に予備として残しています。

## Cron Trigger と起動する workflow

| Cron Trigger（UTC） | 日本時間 | 起動する workflow |
|---|---|---|
| `2,12,22,32,42,52 * * * *` | 10分ごと（毎時02分、12分…） | `publish-live.yml`：最新10分値のPages配信 |
| `35 * * * *` | 毎時35分 | `sync-hourly.yml`：1時間値の年別アーカイブ更新 |
| `25 18 * * *` | 毎日03:25 | `archive.yml`：10分値の日別アーカイブ確定 |

Cron Trigger の文字列は [`worker.js`](worker.js) の `WORKFLOW_BY_CRON` と完全に一致させてください。対応表に無い Cron で起動した場合、Worker は何も起動せずエラーを記録します。分だけを指定する Cron は日本時間でも同じ分に動きますが、日次の Cron は UTC で書くため、日本時間から9時間引いた時刻を指定します。

## Secret

| 名前 | 内容 |
|---|---|
| `GITHUB_DATA_REPO_TOKEN` | `tanyeee/kuji-waterlevel-data` だけを対象にした Fine-grained personal access token。Repository permissions は **Actions: Read and write** のみ（Metadata: Read-only は自動で付きます） |

トークンの値は、このリポジトリ・チャット・引継ぎ文書に書かないでください。

- 有効期限: 2027-09-23（2026-09-23 作成）

期限が切れると最新10分値の配信が止まります。期限前に再生成し、Cloudflare の Secret を更新してください。

## デプロイ

Worker のコードは Cloudflare ダッシュボードで編集しています（wrangler は使っていません）。

1. Workers & Pages → `kuji-waterlevel-scheduler` のコード編集画面で、内容を [`worker.js`](worker.js) に置き換えて Deploy
2. Settings → Variables and Secrets に `GITHUB_DATA_REPO_TOKEN` を Secret として登録
3. Settings → Trigger Events（Cron Triggers）に上の表の3つを登録

ダッシュボードで直接コードを変えた場合は、このファイルも同じ内容に更新してください。

## 動作確認

次の3点がそろって、はじめて Cron からの起動が成功したとみなします。手動の `workflow_dispatch` の成功は、Cron が動いている証明になりません。

1. Worker のログに `{"result":"dispatched",...}` が出ている。失敗時は例外メッセージに GitHub の応答コードが出ます（401：トークンが無効または期限切れ／403：Actions の書き込み権限が無い／404・422：workflow 名や設定の誤り）。
2. このリポジトリの Actions に、Cron と同じ時刻の `workflow_dispatch` の run があり、成功している。
3. `https://tanyeee.github.io/kuji-waterlevel-data/live/stations/kuji-ohashi/recent_10min.json` の `meta.last_fetch_utc` が約10分ごとに進んでいる。

## ロールバック

以前の Worker は旧リポジトリ `tanyeee/kuji-waterlevel` の `update_recent_10min.yml` を起動し、そこから `publish-live.yml` を中継していました。新しい経路に問題があれば、次の手順で戻します。

1. ダッシュボードのデプロイ履歴（Deployments）から直前のバージョンに戻す。または、置き換え前に保存した旧コードを貼って Deploy する。
2. 追加した `35 * * * *` と `25 18 * * *` の Cron Trigger を削除する。

旧経路は Cloudflare の Secret `GITHUB_TOKEN` と旧リポジトリの Actions secret `DATA_REPO_DISPATCH_TOKEN` を使います。新経路の安定を確認するまで、どちらも削除しないでください。
