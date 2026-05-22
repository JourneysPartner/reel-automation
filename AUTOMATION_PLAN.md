# 守護神税理士 Instagram 自動運用 計画書
### 〜 生成の全自動化 ＋ スマホ承認フロー（方式1）＋ 動画エンジン刷新（Creatomate撤去）〜

最終更新: 2026-05-22

---

## 1. ゴールと要件（ユーザー要望の整理）

| # | 要件 |
|---|---|
| R1 | 生成トリガーを**全自動**（PCを開かずに動く） |
| R2 | 公開予定の**数日前（既定3日前）に個別生成**。一括生成はしない（台本・テーマは事前作成可） |
| R3 | 生成完了を **ChatWork に通知**、リンクから動画/画像を確認 |
| R4 | 確認画面で **「公開」「差し戻し（修正コメント記入可）」「見送り」** を選べる（方式1=Webボタン） |
| R5 | 承認後、**スケジュール日に自動公開**。突発的な「見送り」にも対応 |
| R6 | ユーザーの手動操作は**確認のみ**。スマホで完結 |
| R7 | **リールもフィード（カルーセル）も同じ仕組み** |
| R8 | **Creatomateを使わず**、無料/低コストの自作エンジンに置き換え（Remotion検討） |

---

## 2. 動画エンジンの選定

### 2.1 比較

| 観点 | Creatomate（現状） | **Remotion（推奨）** | FFmpeg + HTML/Playwright（保険） |
|---|---|---|---|
| 仕組み | クラウドAPI | Reactで動画を記述→Chromiumで描画 | 各シーンをPNG化→FFmpegで連結 |
| 現デザイン再現 | ✅ | ✅ | ✅ |
| **動き・アニメ** | ✅ 楽 | ✅ **コードで自在**（字幕フェード/キャラ動き/ズーム） | △ filterで頑張る |
| コスト | 従量課金 | **自己ホストは無料**（※企業ライセンス要確認） | **完全無料** |
| 技術スタック適合 | - | ✅ Node/TSで reel-bot と同じ | ✅ 既存Playwright流用 |
| GitHub Actions | ✅ | ✅（Chromium） | ✅（ffmpeg+playwright導入） |
| 学習/構築コスト | 低 | 中（Reactコンポジション） | 中（FFmpeg filter） |

### 2.2 結論と理由

- **第一候補: Remotion**
  - reel-bot が既に Node/JS なので**同一スタックで統合**できる
  - 将来「字幕のフェードイン」「キャラの軽い動き」「進捗バー」など**リールで効く動き**を後から足しやすい（リールは静止画より動きがある方が伸びやすい）
  - 台本/タイミング/画像/音声を **props で渡すだけ**で、`textLayout.js` の自然改行ロジックもそのまま流用可
- **保険: FFmpeg + HTML/Playwright**
  - Remotion のライセンスが企業で有償になる場合の代替（**完全無料**）
  - 現状の静止画デザインなら品質差ゼロ、カルーセルの描画資産を流用できる

> ⚠️ **要確認（ユーザー作業）**: Remotion は個人・小規模は無料だが、一定規模以上の企業は有償ライセンスが必要な場合がある。毛利事務所の従業員数で要否が変わるため、https://www.remotion.pro/license で確認。
> - 無料で使える → **Remotion 採用**
> - 有償が必要で避けたい → **FFmpeg+HTML 採用**（このときも他の設計は変わらない）

どちらに転んでも、**台本・音声・GCS・Drive・Instagram投稿・自然改行・キャラ拡大の各モジュールはそのまま流用**でき、差し替えるのは「動画生成部分」だけです。

---

## 3. 全体アーキテクチャ（イベント駆動・3段＋承認受け皿）

```
                    ┌──────────────────────────────────────────────┐
                    │  正: config/schedule.yaml（内容計画＝日付/種別/テーマ）│
                    └──────────────────────────────────────────────┘
                                        │ 取り込み
                                        ▼
   [A] 生成cron（毎朝・GitHub Actions）           [状態ストア]
       公開日 == 今日+3日 の未生成を抽出   ───▶   Google スプレッドシート
       ├ リール: Node(Remotion)生成               （1行=1投稿の状態管理）
       ├ カルーセル: Python(既存)生成
       ├ Google Drive に保存（プレビュー用リンク）
       └ ChatWork通知（プレビュー＋承認ページURL）
                                        │
                                        ▼
   [B] 承認Webアプリ（方式1・Google Apps Script）
       スマホで開く確認ページ:
         ┌────────────────────────┐
         │  ▶ 動画/画像プレビュー        │
         │  [ 公開する ]               │
         │  [ 差し戻し ] + コメント入力欄  │
         │  [ 見送り ]                 │
         └────────────────────────┘
       押下 → スプレッドシートの状態を更新
         ├ 公開する  → status=approved
         ├ 差し戻し  → status=rejected(+comment) → 再生成をトリガー → 再通知
         └ 見送り    → status=skipped
                                        │
                                        ▼
   [C] 公開cron（毎日・GitHub Actions）
       公開日 == 今日 かつ status=approved を抽出
       → Instagram公開 → status=published, permalink記録
       → ChatWork完了通知
       （見送り/差し戻し/未承認は公開しない。未承認は催促通知）
```

**ポイント**: GitHub Actions は「短時間で終わる実行環境」なので、承認を“待つ”ことはできない。よって**待たない設計**（生成cron／承認受け皿／公開cron に分離し、状態をスプレッドシートで持つ）にする。

---

## 4. コンポーネント詳細設計

### 4.1 状態ストア（Google スプレッドシート）

1行 = 1投稿。生成cronが行を作り、承認・公開で更新。

| 列 | 例 | 説明 |
|---|---|---|
| id | 2026-06-05 | 一意キー（公開日） |
| publish_date | 2026-06-05 | 公開予定日 |
| type | reel / carousel | 種別 |
| theme | クリエイターの経費… | schedule.yaml由来 |
| status | planned→generating→review→approved/rejected/skipped→published/failed | 状態遷移 |
| preview_url | Driveリンク | 確認用 |
| revision_comment | 「利益700万に」 | 差し戻し時のコメント |
| generated_at / decided_at / published_at | 日時 | 監査用 |
| permalink | instagram.com/p/… | 公開後 |

採用理由: 人が見て分かる／Apps Scriptから読み書き簡単／GitHub Actionsからも API で読める。

### 4.2 生成cron（`.github/workflows/generate.yml`）

- トリガー: 毎朝（例 JST 8:00）。`workflow_dispatch` でも手動可
- 処理:
  1. `schedule.yaml` を読み、`publish_date == 今日 + LEAD_DAYS(=3)` の投稿を抽出
  2. スプレッドシートで未生成（status空 or planned）を確認
  3. 種別で分岐:
     - reel → `reel-bot`（Remotion）で 台本→音声→動画
     - carousel → 既存Python（content_generator/image_renderer）
  4. 成果物を Google Drive に保存（プレビュー用の閲覧リンク取得）
  5. スプレッドシートを `status=review, preview_url=…` に更新
  6. ChatWork に通知（プレビューリンク＋承認ページURL）
- VOICEVOX は service container（リールのみ）

### 4.3 承認Webアプリ（Google Apps Script）— 方式1

- **デプロイ**: Apps Script を「ウェブアプリ」として公開（実行=自分、アクセス=リンクを知る全員＋トークンで保護）
- **URL**: `https://script.google.com/macros/s/…/exec?id=2026-06-05&token=署名`
- **画面（HTML）**:
  - 動画（Driveの埋め込み or 直接リンク）/ カルーセルは画像一覧
  - ボタン3つ＋差し戻し用コメント欄
- **動作**:
  - 「公開する」→ スプレッドシート status=approved
  - 「差し戻し」→ status=rejected, revision_comment保存 → **GitHub の repository_dispatch API を叩いて再生成をトリガー**（コメントを台本生成プロンプトに反映）→ 再生成後また通知
  - 「見送り」→ status=skipped
- **セキュリティ**: URLにHMAC署名トークン（他人が叩けない）。GitHub PAT は Apps Script の Script Properties に保管

### 4.4 公開cron（`.github/workflows/publish.yml`）

- トリガー: 毎日（公開時刻に近い時刻、例 JST 19:00）
- 処理:
  1. スプレッドシートで `publish_date == 今日 かつ status=approved` を抽出
  2. Instagram 公開（リール=reel-bot、カルーセル=既存Python）
  3. status=published, permalink を記録
  4. ChatWork 完了通知
  5. `publish_date == 今日 かつ status=review`（未承認）→ 公開せず、ChatWorkで催促

### 4.5 動画エンジン（Remotion 採用時の設計）

- `reel-bot/remotion/` に Remotion プロジェクト
- コンポジション `Reel`（1080×1920, 30fps）:
  - 背景: クリーム単色（#F7F4EE）
  - Hook: 上部固定（台本の hook）
  - Subtitle: `timings.json` で1文ずつ表示（`textLayout.js` の自然改行を流用）＋白カード＋軽いフェードイン
  - Character: 下部（現行1.4倍相当）
  - Credit / Account: 最下部
  - Audio: VOICEVOX wav
- props: `script.json` / `timings.json` / 画像パス / 音声パス
- レンダリング: `npx remotion render Reel out/reel.mp4 --props=…`
- 流用: `scriptGenerator.js` `voicevoxClient.js` `textLayout.js` `gcs.js` `googleDrive.js` `instagram.js` はそのまま
- 撤去: `creatomate.js`（参照用に残置可）

> FFmpeg+HTML 採用時: `reel-bot/src/videoAssembler.js` を新設し、各シーンを既存HTML→PNG（Playwright）で描画→FFmpegで連結＋音声。props・流用範囲は同じ。

### 4.6 ChatWork通知

- ChatWork API（`POST /rooms/{room_id}/messages`、APIトークン）
- 通知種別: 生成完了（承認依頼）／公開完了／エラー／未承認の催促
- メッセージ例:
  ```
  [info][title]リール生成完了 6/5[/title]
  テーマ: クリエイターの経費
  確認はこちら: https://script.google.com/.../exec?id=2026-06-05&token=...
  [/info]
  ```

---

## 5. スケジュールとリードタイム

- `config/schedule.yaml` を内容計画の正とする（6月: リール 6/5・6/14・6/21、他カルーセル）
- リードタイム `LEAD_DAYS = 3`（生成 = 公開日−3日）
- 突発見送り: 承認ページで「見送り」→ 公開cronがスキップ（締切に間に合えば確実に止められる）
- 台本/テーマの事前作成: themes は schedule.yaml に既存。必要なら台本だけ先行一括生成も可（動画は直前生成）

---

## 6. フィード投稿の統合

- 承認・通知・公開・状態管理の仕組みは**リールと共有**
- 生成だけ Python（`content_generator.py` / `image_renderer.py` / `story_generator.py`）を使用
- プレビュー: カルーセルの複数画像を Drive に上げ、承認ページで一覧表示
- 既存の「修正予定キュー」も、この承認フローの差し戻しコメントに統合していける

---

## 7. セキュリティ / 認証

- 承認ページURL に HMAC 署名トークン（第三者が叩けない）
- 機密: GitHub Secrets（APIキー類）／Apps Script Script Properties（GitHub PAT, ChatWorkトークン）
- `.env`・サービスアカウント鍵は非コミット（`.gitignore` 設定済み）
- **Metaトークン60日問題**: 自動運用中の失効を防ぐため、長期トークンの**自動更新ワークフロー**を別途用意（または失効前にChatWork催促）

---

## 8. コスト試算（月3〜10本想定）

| 項目 | コスト |
|---|---|
| Remotion | 小規模なら無料（要ライセンス確認）／FFmpegなら完全無料 |
| Creatomate | **廃止 → 0円** |
| GCS（一時配信） | 無料枠〜数十円 |
| Google Drive | 無料枠 |
| Apps Script | 無料 |
| GitHub Actions | 無料枠（private 2000分/月）。1本3〜5分想定で十分 |
| Anthropic（台本） | 既存・少額 |

---

## 9. リスクと対策

| リスク | 対策 |
|---|---|
| GitHub cron の時刻ズレ / 60日無活動で自動停止 | 厳密な時刻が要るなら GCP Cloud Scheduler に移行／定期コミットで活性維持 |
| Remotion ライセンスが企業で有償 | FFmpeg+HTML にフォールバック（無料） |
| Metaトークン失効 | 自動更新ワークフロー＋失効前催促 |
| 締切までに未承認 | 公開cronは「未承認＝公開しない」を既定に。前日/当日に催促通知 |
| 生成失敗 | エラーをChatWork通知＋status=failed＋手動/自動リトライ |
| 差し戻しループが長引く | コメント反映の再生成は台本からやり直し。回数上限/履歴をSheetへLog |

---

## 10. 実装フェーズ（段階導入）

| Phase | 内容 | 完了基準 |
|---|---|---|
| 0 ✅ | 動画エンジン刷新（**Remotion採用**＝従業員3名以下で無料・Creatomate撤去） | **完了**: ローカルで 1080×1920 フル解像度・自然改行・キャラ1.4倍を確認 |
| 1 ⏳ | 状態ストア(Sheet)＋生成cron＋ChatWork通知 | **コード完成・dry-run検証済**。ユーザー初期設定（Sheets API/シート共有/ChatWork）後にライブ確認 |
| 2 ⏳ | 承認Webアプリ（公開/見送り・HMAC） | **コード完成**。Apps Scriptデプロイ＋APPROVAL_*設定後に動作確認（差し戻し再生成は後続） |
| 3 | 差し戻し＋コメント→再生成ループ | 差し戻しコメントが台本に反映され再通知 |
| 4 ⏳ | 公開cron（承認→当日公開） | **コード完成・dry-run検証済**。実投稿テスト（ライブ）はユーザー確認後 |
| 5 | フィード投稿の統合 | カルーセルも同フローで運用 |
| 6 | Metaトークン自動更新・監視 | 失効せず連続運用 |

---

## 11. あなた（ユーザー）がやること

1. **Remotion ライセンス要否を確認**（従業員数）→ Remotion or FFmpeg を確定
2. **ChatWork**: APIトークン発行＋通知先 room_id を用意
3. **Google スプレッドシート**: 状態管理シートを作成（私がテンプレ提供）
4. **Apps Script**: プロジェクト作成→ウェブアプリ公開（私が手順とコード提供）
5. **GitHub**: リポジトリ作成＋Secrets登録（既存SETUP.mdに追記）
6. **公開時刻/生成時刻・リードタイム日数の確定**（既定: 生成=公開3日前 朝、公開=当日19時）

---

## 12. 検証（受け入れテスト）

- 生成cron: 指定日の3日前に該当投稿だけが生成され、ChatWork通知＋承認ページが開ける
- 承認: 「公開」「差し戻し（コメント）」「見送り」でSheetが正しく更新、差し戻しは再生成される
- 公開cron: 承認済みのみ予定日に公開、permalink取得、未承認は公開されない
- フィード: カルーセルも同様に通る
- PC不要: 全工程をスマホ確認のみで完走

---

## 付録: 現状からの差分まとめ

- **撤去**: Creatomate（`creatomate.js` は参照用に残置可）
- **新規**: Remotion(or videoAssembler) / Googleスプレッドシート状態管理 / Apps Script承認アプリ / generate.yml / publish.yml / ChatWork通知 / Metaトークン更新
- **流用（変更なし）**: 台本(Sonnet4.6) / 音声(VOICEVOX) / 自然改行(textLayout.js) / キャラ1.4倍 / GCS / Drive(OAuth) / Instagram投稿 / schedule.yaml
