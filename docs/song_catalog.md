# CSV楽曲情報

`song_catalog`はRekordbox・Windows・MIDIに依存しない独立したPythonパッケージです。
現時点の配布物はrekordbox-syncのwheelに同梱していますが、監視サービスを起動せず単独で使用できます。
CSVは別システムで管理する入力データとして読み取るだけで、内容を書き換えません。

## 入力

既定のキーは「タイトル」と「歌手」の組み合わせです。UTF-8（BOMあり/なし）を読み取ります。
今回のassets/song_info.csvは3,164行で、キーの重複と空キーはありません。出力列は放送年とTVAです。
（2026-11-16に、Rekordboxのコメ欄へ直接整理されていた1,027キーをコメントから復元して統合した。）
出力列は自由に追加できます。コメントなどの列を追加しても検索処理の修正は不要です。

照合時のみ、キーの前後空白を除去しUnicodeをNFCへ正規化します。大文字小文字、全角半角、記号、内部空白、歌手順序、Remix等の表記は変更しません。登録値はそのまま文字列で返します。

- matched: 1件一致。
- not_found: 一致なし。fieldsは空。
- ambiguous: 複数行に一致。勝手に選択せず、件数のみ返す。
- 空のメタデータセルは空文字列。未一致とは区別する。
- CSVの列不整合、キー不足・空キー、文字コードエラー等はCatalogError。
- ファイルの更新時刻/サイズが変われば次回検索時に再読込。更新中や不正なCSVはエラーを返し、以前の値を返さない。
- 更新時刻とサイズを維持する編集ではreload()を明示的に呼ぶ。

## Python

```python
from song_catalog import CsvSongCatalog

catalog = CsvSongCatalog("assets/song_info.csv")
result = catalog.lookup(
    "いつかの、いくつかのきみとのせかい", "fhána", fields=["TVA"]
)
print(result.to_dict())
```

fields省略時はキー列以外を全て返します。未知の列名はValueErrorです。
異なる列名の場合はtitle_column / artist_columnをコンストラクターで指定できます。
返すSongLookupResultは不変で、to_dict()は独立した辞書です。

## CLI

```powershell
.\.venv\Scripts\python.exe -X utf8 -m song_catalog assets/song_info.csv --title "いつかの、いくつかのきみとのせかい" --artist "fhána" --field TVA
```

インストール後はsong-catalogコマンドも使えます。複数列は--fieldを繰り返します。

## ライブ情報と組み合わせる

```powershell
.\.venv\Scripts\python.exe -m rekordbox_sync serve --catalog assets/song_info.csv
```

- `/song-info?title=...&artist=...&field=TVA`: 曲を指定して検索。日本語等はURLエンコードする。
- `/state/enriched?field=TVA`: 現在のデッキ情報と、その曲に対応するCSV情報。
- field省略時は全メタデータ列、複数列はfieldパラメーターを繰り返す。
- 既存の`/state`と`/health`のJSONは変更しない。
- CSV未設定/読み取り失敗はHTTP 503、引数不正は400。未一致・曖昧は200とstatusで表現。

`/state/enriched`は独立したschema_version=1で、`state`に従来の状態、`song_info`にデッキID別の検索結果を返します。
未ロードまたは曲名/歌手が不明なデッキはtrack_unavailable。UI取得元が失効した場合はデッキ情報とsong_infoとも空になり、前の曲のメタデータを残しません。
画面で選択中の曲ではなく、監視スナップショットのロード済み曲を照合します。ただし、元のUI読み取りの非同時性は引き続き存在します。

CSVが複数デッキの検索の途中で更新された場合、それぞれ異なるCSV版を参照する可能性があります。CSV更新全体とUI取得をまとめたトランザクションではありません。
