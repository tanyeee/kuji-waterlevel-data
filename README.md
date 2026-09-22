# 久慈川・那珂川水系 10分水位アーカイブ

[`kuji-waterlevel`](https://github.com/tanyeee/kuji-waterlevel) が公開する国土交通省の河川水位を、長期参照用のコンパクトなJSONとして保存します。

- `data/10min/YYYY/MM/YYYY-MM-DD.json`: 48時間の補正待ち後に確定する日別10分値。作成後は原則変更しません。
- `data/hourly/STATION/YYYY.json`: 2016年以降の1時間値。10分アーカイブ開始前のフォールバックです。
- 時刻はJSTです。値はm、欠測は `null` です。
- 日別ファイルは全観測地点をまとめ、時刻を繰り返さず `stepMinutes` と配列位置で表現します。

毎日03:25 JSTに10分値を確定し、毎週日曜03:45 JSTに1時間値を同期します。10分値は原本として保持し、30分・1時間などの集約は利用側で行います。
