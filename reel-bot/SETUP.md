# reel-bot セットアップガイド（新スタック）

リール生成パイプライン: ①台本(Sonnet 4.6) → ②音声(VOICEVOX 青山龍星/喜び) → ③GCS → ④**Remotion**(動画生成) → ⑤GCS → ⑥Instagram → ⑦Google Drive

## 現状

| 工程 | 状態 |
|---|---|
| ①〜⑤ | ✅ 実装・テスト済み（ローカルで 1080×1920 フル解像度を確認） |
| ⑥ Instagram | ✅ 実装済み（`--post` で公開。既定は生成のみ） |
| ⑦ Google Drive | ✅ 実装済み（OAuth設定済み。手順は STEP 2） |

残りのユーザー作業は **STEP 2（Drive OAuth, 設定済みなら不要）** と **STEP 3（GitHub運用）** のみ。

---

## STEP 1. 動画エンジン = Remotion（Creatomate 廃止 / 課金不要）

- 動画生成は **Remotion**（Reactベース・自己ホスト）に移行済み。**1080×1920 をローカル/CIで無料生成**。
- **ライセンス**: 個人・小規模企業（目安 3名以下）は無料。詳細は https://www.remotion.pro/license で確認。
- 初回の `node src/index.js ...` 実行時に **Chrome Headless Shell（約113MB）を自動取得**（2回目以降はキャッシュ）。
- プレビュー（任意・PC）: `npm run studio` で Remotion Studio が開き、デザインを視覚的に確認できる。
- デザイン定義: `remotion/Reel.tsx`（背景クリーム/上部フック/中央字幕/下部キャラ1.4倍/クレジット・アカウント/音声）。Creatomateテンプレと同じ vmin/% 値で 1:1 再現。字幕の自然改行は `src/textLayout.js`。

---

## STEP 2. Google Drive 保管（⑦）の OAuth 設定

個人Gmail（k.nakagawa662@gmail.com）はサービスアカウント直書き込み不可・共有ドライブも不可のため **OAuth** を使う。
スコープは `drive.file`（アプリ作成ファイルのみ）。保管先はアプリが作る `リール動画 (自動保管)` フォルダ配下。

### 2-1. OAuth クライアント作成（GCP コンソール）
1. https://console.cloud.google.com/ → プロジェクト `reels-automation`
2. 「API とサービス」→「OAuth 同意画面」
   - User Type: **外部** → 作成
   - アプリ名・サポートメール等を入力
   - スコープは追加不要（drive.file はここで列挙しなくても可）
   - 「テストユーザー」に **自分のGoogleアカウントを追加**
3. 「認証情報」→「認証情報を作成」→「OAuth クライアント ID」
   - アプリの種類: **デスクトップアプリ**
   - 作成後、**クライアントID** と **クライアントシークレット** を控える

### 2-2. .env に追記
```
GOOGLE_OAUTH_CLIENT_ID=（控えたID）
GOOGLE_OAUTH_CLIENT_SECRET=（控えたシークレット）
```

### 2-3. リフレッシュトークン取得（初回1回）
```
cd reel-bot
node scripts/google-oauth.js
```
- 表示URLをブラウザで開く → 自分のアカウントで許可（「未確認のアプリ」警告は「詳細」→「移動」で続行）
- ターミナルに出る `GOOGLE_OAUTH_REFRESH_TOKEN=...` を **.env に貼る**

### 2-4. 動作確認
```
node src/index.js --date 2026-06-05 --reuse
```
→ `=== ⑦ Google Drive 保管 ===` で `✓ Drive: ...` が出れば成功。

---

## STEP 3. GitHub Actions で動かす

### 3-1. リポジトリ
- `ig-tax-guardian/` をリポジトリのルートとして push（private 推奨）
- `.gitignore` で `.env` とサービスアカウント鍵は除外済み（**コミットしないこと**）

### 3-2. GitHub Secrets（Settings → Secrets and variables → Actions）
| Secret | 値 |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic キー |
| `GCP_PROJECT_ID` | `reels-automation` |
| `GCS_BUCKET_NAME` | `tax-reels-public-assets` |
| `GCP_SA_KEY` | サービスアカウント JSON **の中身全体**（ファイルではなく文字列） |
| `GOOGLE_OAUTH_CLIENT_ID` | STEP 2 のID |
| `GOOGLE_OAUTH_CLIENT_SECRET` | STEP 2 のシークレット |
| `GOOGLE_OAUTH_REFRESH_TOKEN` | STEP 2 のトークン |
| `META_ACCESS_TOKEN` | Meta 長期トークン（60日。期限管理に注意） |
| `INSTAGRAM_BUSINESS_ACCOUNT_ID` | `17841423413355551` |

> Creatomate 関連の Secret は不要になりました（Remotion 化で廃止）。

### 3-3. 実行
- Actions タブ → `reel-auto` → Run workflow
- 入力: `date`（または `text` / `url`）、`post`（true で実際に投稿。既定 false=生成のみ）
- VOICEVOX は Docker サービスコンテナ（`voicevox/voicevox_engine:cpu-latest`）で自動起動
- Remotion は実行時に Chrome Headless Shell を自動取得。CI が重い場合は事前取得ステップ（`npx remotion browser ensure`）の追加を検討

---

## STEP 4. 自動生成＋通知（Phase 1）

公開予定の **3日前** に自動でリールを生成し、Driveに保存→**スプレッドシートに状態記録**→**ChatWorkに確認依頼通知**。投稿はしない（確認後の公開は次フェーズ）。

### 4-1. Google Sheets API を有効化
- GCPコンソール → プロジェクト `reels-automation` → 「API とサービス」→「ライブラリ」→ **Google Sheets API** を有効化

### 4-2. 状態管理スプレッドシートを作成・共有
1. 空の Google スプレッドシートを新規作成
2. 「共有」→ 下記サービスアカウントを **編集者** で追加:
   ```
   tax-reels@reels-automation-497107.iam.gserviceaccount.com
   ```
3. URL（`https://docs.google.com/spreadsheets/d/【ここがID】/edit`）の **ID** を `.env` の `GSHEET_ID` に設定
4. ヘッダ行を作成: `node src/sheet.js init`

### 4-3. ChatWork
1. ChatWork → 右上アイコン →「サービス連携」→「API Token」で **APIトークン** を取得 → `.env` の `CHATWORK_API_TOKEN`
2. 通知したいルームを開き、URL末尾 `#!rid【数字】` の **数字** を `.env` の `CHATWORK_ROOM_ID`
3. 疎通確認: `node src/chatwork.js "テスト"`

### 4-4. 動作確認
```
# 対象表示のみ（生成しない）
node src/scheduler.js --dry-run --date 2026-06-02

# 指定1件を実生成 → Drive保存・Sheet更新・ChatWork通知
node src/scheduler.js --id 2026-06-05
```

### 4-5. GitHub Secrets 追加（自動運用する場合）
`GSHEET_ID` / `CHATWORK_API_TOKEN` / `CHATWORK_ROOM_ID` を追加。
ワークフロー `reel-generate`（`.github/workflows/generate.yml`）が毎朝 08:00 JST に当日分を生成。

---

## STEP 5. 承認Webアプリ（Phase 2 / Apps Script）

ChatWork通知のリンクから、スマホで **「公開する」「見送り」** を押せる承認ページ。HMACトークンで保護（ログイン不要）。

### 5-1. 共有秘密鍵を決める
ターミナルで生成（例）:
```
node -e "console.log(require('crypto').randomBytes(24).toString('hex'))"
```
出た値を **同じ値** で「`.env` の `APPROVAL_SECRET`」と「Apps Script の Script Property `APPROVAL_SECRET`」に設定する。

### 5-2. Apps Script プロジェクト作成
1. https://script.google.com → 新しいプロジェクト
2. `appsscript/Code.gs` の中身を `Code.gs` に貼付
3. ファイル＋ → HTML を追加し名前を **`Page`** にして `appsscript/Page.html` を貼付
4. ⚙️プロジェクトの設定 →「スクリプト プロパティ」に追加:
   - `GSHEET_ID` = 状態管理シートID（.env と同じ）
   - `GSHEET_TAB` = `posts`
   - `APPROVAL_SECRET` = 5-1 の値

### 5-3. ウェブアプリとしてデプロイ
- 「デプロイ」→「新しいデプロイ」→ 種類「ウェブアプリ」
- 実行ユーザー: **自分**／アクセスできるユーザー: **全員**
- デプロイ → 表示される **ウェブアプリURL（/exec）** を控える

### 5-4. .env に設定
```
APPROVAL_BASE_URL=（5-3 の /exec URL）
APPROVAL_SECRET=（5-1 の値）
```
GitHub 運用時は同じ2つを Secrets にも追加。

### 5-5. 動作確認
```
node src/approval.js 2026-06-05        # 承認URLを表示 → スマホ/PCで開く
```
→ 動画プレビューと「公開する／見送り」ボタンが出る。押すとスプレッドシートの status が approved / skipped に変わる（`node src/sheet.js list` で確認）。
※ 以降の生成（scheduler）通知には、この承認URLが自動で含まれます。

---

## STEP 6. 公開cron（Phase 4 / 承認→予定日に自動投稿）

承認済み（status=approved）のリールを、**公開予定日に自動でInstagram投稿**する。
動画・台本は生成時に GCS（`reels/<id>/`）へ保存済みで、公開時に署名URLを取り直して投稿する。

### 6-1. 必要なGitHub Secrets（生成側に加えて）
- `META_ACCESS_TOKEN` / `INSTAGRAM_BUSINESS_ACCOUNT_ID`（投稿用）
- `GCP_SA_KEY` / `GCS_BUCKET_NAME` / `GCP_PROJECT_ID` / `GSHEET_ID` / `CHATWORK_*`（既出）

### 6-2. ワークフロー
- `.github/workflows/publish.yml` が **毎日 19:00 JST** に実行
- 「公開日==今日 かつ approved」を投稿 → status=published・permalink記録 → ChatWork完了通知
- 未承認（review）が当日締切なら催促通知（公開はしない）

### 6-3. 動作確認
```
# 公開対象の確認（投稿しない）
node src/publish.js --dry-run --date 2026-06-05

# 実際に投稿（approved のもの。ライブ投稿なので注意）
node src/publish.js --id 2026-06-05
```

> ⚠️ `--dry-run` 無しは**本番アカウントに即投稿**されます。最初のテストは慎重に。

---

## STEP 7. 差し戻し＋再生成（任意）

承認ページに「✏️ 差し戻して再生成」ボタン＋コメント欄を追加。差し戻すとコメントを台本に反映して自動で作り直し、再通知する。

### 7-1. GitHub PAT を作成
- GitHub → Settings → Developer settings → **Fine-grained tokens** → Generate
  - Resource owner: **JourneysPartner**／Repository access: **reel-automation のみ**
  - Permissions → Repository → **Contents: Read and write**（repository_dispatch に必要）
  - 期限は任意（切れたら差し戻しの再生成だけ止まる。Sheetへの記録は残る）

### 7-2. Apps Script に設定
1. Script Properties に追加:
   - `GITHUB_TOKEN` = 7-1 のPAT
   - `GITHUB_REPO` = `JourneysPartner/reel-automation`
2. `Code.gs` と `Page` を最新版に貼り替え（差し戻しボタン対応版）→ 保存 → **再デプロイ（新バージョン）**

### 7-3. 動作
- 承認ページでコメントを書いて「差し戻して再生成」→ Sheet status=rejected＋コメント記録 → `regenerate.yml` が起動 → 台本を作り直し → status=review に戻り再通知。
- ローカル手動でも: `node src/scheduler.js --id 2026-06-14 --revision "フックを短く"`

---

## STEP 8. フィード（カルーセル）統合（任意）

カルーセル投稿も**同じ生成→承認→公開フロー**に統合済み。`schedule.yaml` の `type: carousel` の投稿は、生成cronが既存Python（content_generator + image_renderer）で生成し、スライドをGCS/承認ページ/公開へ流す。

- **生成**: Node の scheduler が `python -m src.content_generator --day N` と `python -m src.image_renderer --date <date>` を呼び、`output/posts/<date>/slide_*.png` を GCS（`posts/<date>/`）へ。承認ページは画像を並べて表示。
- **公開**: 承認後、`publish.js` が GCS のスライドを署名URL化して**カルーセル投稿**（2〜10枚）。キャプションは `caption.md`。
- **CI**: `generate.yml` / `regenerate.yml` に Python + Playwright のセットアップを追加済み（`pip install -r requirements.txt` + `playwright install chromium`）。
- **ローカルテスト**: `node src/carousel.js 2026-06-07 7 --reuse`（既存スライドで確認）。
- **制限**: カルーセルの「差し戻し」はコメントを内容に反映せず再生成のみ（リールはコメント反映あり）。

---

## ローカル実行コマンド早見表

```
# 台本のみ
node src/index.js --date 2026-06-05 --dry-run

# 台本+音声+動画+GCS+Drive（投稿しない）※VOICEVOX起動が必要
node src/index.js --date 2026-06-05

# 既存の script.json / voice.wav を再利用（VOICEVOX不要）
node src/index.js --date 2026-06-05 --reuse

# 実際に Instagram へ公開
node src/index.js --date 2026-06-05 --reuse --post

# 自動生成スケジューラ（Phase 1）
node src/scheduler.js --dry-run --date 2026-06-02   # 対象確認
node src/scheduler.js --id 2026-06-05               # 1件生成→Sheet→ChatWork

# 単体テスト
node src/scriptGenerator.js --date 2026-06-05
node src/voicevoxClient.js --input ../output/reels/2026-06-05/script.json
node src/gcs.js <localPath> <destName>
node src/googleDrive.js <slug> <file1> [file2 ...]
node src/sheet.js list
node src/chatwork.js "テスト"
```

## 既知の注意点
- **動画エンジン**: Remotion（無料・1080×1920）。Creatomate は廃止
- **Meta トークン**: 60日で失効。期限前に更新（自動更新は別途）
- **GCS 署名付きURL**: 24時間有効。Remotion/Instagram は即時取得するため問題なし
- **VOICEVOX 青山龍星**: `cpu-latest` イメージに同梱（バージョン更新で確認）
- **状態管理シート**: SA を編集者で共有必須。Sheets API 有効化が必要
- **GitHub Actions cron**: 時刻が数十分ずれる／60日無活動で自動停止の可能性
