# Rekordbox 7 × DDJ-FLX4 リアルタイム状態取得 — 実装方針

更新日: 2026-11-16  
対象: Windows / Rekordbox 7（継続的に最新版へ更新）/ DDJ-FLX4

## 1. 目的

Rekordbox と DDJ-FLX4 の情報を取得し、外部アプリから参照できる **非公式 Live State API** を構築する。

最初の目標は、**デッキごとの曲情報・BPM・再生位置**と、**チャンネルフェーダー／クロスフェーダー位置**の取得・統合。将来はOBS用Now Playingや配信ダッシュボードに利用する。

## 2. 取得対象と見込み

| 項目 | 主な取得元 | 備考 |
| --- | --- | --- |
| デッキ別曲名・アーティスト | Rekordboxメモリ監視 / `rkbx_link` | 対応バージョン・ライセンス要確認 |
| デッキ別BPM | `rkbx_link` | 現在BPMと解析BPMは区別する |
| 再生位置・ビート位置 | `rkbx_link` | OSC出力内容を実測する |
| MASTERデッキ | `rkbx_link` 等 | MASTER指定＝実際の客席出力とは限らない |
| PLAY / PAUSE | 取得情報から推定 | 再生位置差分のみではスクラッチ等で誤判定あり |
| CH1/CH2 フェーダー | FLX4 MIDI | Rekordboxとのポート同時利用可否が最大の課題 |
| クロスフェーダー | FLX4 MIDI | クロスフェーダー割当の影響あり |
| 客席に流れているデッキ | 統合判定 | フェーダー以外にTRIM・EQ・ミュート等も影響 |

> 注意: ここでの「取得可能」は実装候補を示す。使用中のRekordbox 7最新版＋FLX4での動作は、これから検証する。

## 3. 構成案

```text
Rekordbox 7 (Windows)
  ├─ メモリ参照ツール（候補: rkbx_link）
  │    └─ OSC → 楽曲 / BPM / 再生位置 / MASTER
  │
  └─ DDJ-FLX4（USB MIDI）
       └─ MIDI監視 → CHフェーダー / クロスフェーダー
                  ※同時ポート利用可否を検証

             ↓
       Python 統合プロセス
       ├─ DeckState保持
       ├─ MixerState保持
       ├─ イベント時刻管理
       └─ On-air推定
             ↓
       JSON / WebSocket API
             ↓
       OBS / ブラウザ / 自作ダッシュボード
```

初期実装は **Python** を採用。利用候補は `mido` + `python-rtmidi`（MIDI）、`python-osc`（OSC）、`FastAPI` など（API）。

## 4. 実装フェーズ

### Phase 1 — FLX4のMIDIを検証【最優先】

1. FLX4をWindows PCへUSB接続し、デバイス認識を確認。
2. **Rekordboxを終了**して、PythonまたはMIDIモニターから入力ポートを列挙。
3. CH1・CH2・クロスフェーダーを動かし、生のMIDIメッセージを記録。
4. 14bit値の合成・0〜100%正規化が正しいか確認。
5. MIDIモニターを終了し、Rekordbox上で通常操作できるか確認。
6. Rekordbox起動中に別プロセスからMIDIポートを監視できるか検証。

**合格条件:** フェーダー3本の位置が実測でき、通常のDJ操作に影響しないこと。

**重要な制約:** WindowsのMIDIポートは、Rekordboxと監視アプリが同時に開けない可能性がある。失敗した場合は無理にデバイスを奪わず、USB通信の受動監視や安全な中継方式を別途調査する。

### Phase 2 — Rekordboxの楽曲・再生情報を検証

1. `rkbx_link` のWindows版Rekordbox 7対応状況・料金・利用条件を確認。
2. 対応していればOSC受信テストを行い、楽曲名／アーティスト／BPM／再生位置などの実際のアドレスと値をログに保存。
3. 曲ロード、PLAY/PAUSE、テンポ変更、MASTER切替で変化を確認。
4. バージョン更新時に取得できなくなるケースを考慮する。

**合格条件:** デッキごとの楽曲とBPM・再生位置を取得できること。

> `rkbx_link` の最新版対応には有料ライセンスが必要となる場合がある。独自メモリ解析・DLL注入はRekordboxの安定性に影響し得るため、初期段階では優先しない。

### Phase 3 — 統合・On-air推定

- 各取得元のデータに受信時刻を付けて状態を統合する。
- 再生中・待機中・MASTERを別々のフラグで保持する。
- フェーダー位置だけで「音が出ている」と断定しない。
- クロスフェーダーの割当、EQ、TRIM、ヘッドホンCUE、外部ミキサー等を考慮する。
- 判定できない情報は `null` / `unknown` として扱う。

### Phase 4 — API・OBS連携

- `GET /state` で現在状態をJSONで返す。
- `/ws` で状態変化をWebSocket配信する。
- 必要に応じて `current_track.txt` を出力し、OBSテキストソースと連携する。
- 将来的にブラウザUIでデッキ状態を可視化する。

## 5. FLX4のMIDI割当（検証候補）

以前確認したMIDIマッピングの候補値。**実機のMIDIログで必ず照合する。**

| 操作 | 上位・下位バイトの候補 |
| --- | --- |
| CH1 フェーダー | `B0 13` / `B0 33` |
| CH2 フェーダー | `B1 13` / `B1 33` |
| クロスフェーダー | `B6 1F` / `B6 3F` |
| CH1 テンポ | `B0 00` / `B0 20` |
| CH2 テンポ | `B1 00` / `B1 20` |

MSB/LSB方式で14bit値が届く場合、次の式で正規化する。

```python
value_14bit = (msb << 7) | lsb
normalized = value_14bit / 16383.0  # 0.0 ～ 1.0
```

> 実際のMIDIチャンネル、値の方向、MSB/LSBの更新順などは実測すること。Rekordbox画面で操作したフェーダーはFLX4側のMIDIイベントとして現れない場合がある。

## 6. 目標とするJSON

```json
{
  "decks": {
    "1": {
      "title": "Example Track A",
      "artist": "Artist A",
      "bpm": 128.0,
      "position_seconds": 83.5,
      "playing": true,
      "master": true,
      "channel_fader": 0.95
    },
    "2": {
      "title": "Example Track B",
      "artist": "Artist B",
      "bpm": 128.0,
      "position_seconds": 0.0,
      "playing": false,
      "master": false,
      "channel_fader": 0.0
    }
  },
  "mixer": {
    "crossfader": 0.5
  },
  "on_air_estimate": [1]
}
```

上記は**ダミーデータと目標形式**。未取得・判定不能の項目は偽の数値で埋めず `null` にする。

## 7. 初回テストチェックリスト

- [ ] FLX4をUSBでPCへ接続
- [ ] Windowsでデバイス認識を確認
- [ ] Rekordbox終了状態でMIDI入力ポートを列挙
- [ ] CH1フェーダーのMIDIログを採取
- [ ] CH2フェーダーのMIDIログを採取
- [ ] クロスフェーダーのMIDIログを採取
- [ ] 受信値を0〜100%へ正規化
- [ ] MIDIモニターを終了してRekordboxの通常動作を確認
- [ ] Rekordbox起動中にも並列監視できるか確認

### テストで残すログ

```text
OS:
Rekordbox version:
DDJ-FLX4 firmware:
MIDI port names:
CH1 fader sample:
CH2 fader sample:
Crossfader sample:
Works while Rekordbox is open?:
Notes/errors:
```

## 8. 参考プロジェクト

- [rkbx_link](https://github.com/grufkork/rkbx_link) — Rekordboxの状態を取得しOSCなどで公開するプロジェクト
- [RekordBoxSongExporter](https://github.com/Unreal-Dan/RekordBoxSongExporter) — Windows版Rekordboxの内部フックを利用する別案
- [Microsoft Process Monitor](https://learn.microsoft.com/en-us/sysinternals/downloads/procmon) — ファイルアクセスの観察用。読み込みファイルの検出だけでは実再生中の曲は確定できない

## 9. 優先順位と判断基準

1. **DJ操作の安定性を最優先**する。PCやRekordboxを不安定にする監視方法は避ける。
2. **Phase 1でFLX4のMIDIが取得できるか**を確定する。
3. **Phase 2で曲情報が取得できるか**を確定する。
4. 両方揃ったら、ON AIR判定とAPI化へ進む。
5. Rekordboxのアップデートで監視が壊れ得るため、取得元ごとに独立した設計にする。
