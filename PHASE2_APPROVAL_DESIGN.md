# Phase 2 設計書 — 承認Webアプリ（方式1 / Google Apps Script）

最終更新: 2026-05-22

## 目的
ChatWork通知のリンクから、スマホで開ける承認ページを表示。
**「公開する」「差し戻し（コメント記入）」「見送り」** のボタンで、スプレッドシートの状態を更新する（PC不要）。

---

## フロー全体

```
[Phase1] 生成 → Sheet(status=review) → ChatWork通知（承認ページURL付き）
                                              │ スマホでURLを開く
                                              ▼
                         Apps Script Web App（承認ページ）
                           ・動画プレビュー（Drive埋め込み）
                           ・公開予定日 / テーマ
                           ・[公開する] [差し戻し+コメント欄] [見送り]
                                              │ ボタン押下
                                              ▼
                  公開する → Sheet status=approved, decided_at
                  差し戻し → Sheet status=rejected, revision_comment, decided_at
                              → GitHub に再生成を依頼（repository_dispatch）
                              → 再生成後 status=review に戻り再通知
                  見送り   → Sheet status=skipped, decided_at
                                              ▼
                              完了画面を表示（押し間違い防止に確認）

[Phase4で実装] 公開cron: status=approved を公開予定日に Instagram 投稿
```

---

## コンポーネント

### A. Apps Script Web App（私がコード提供 → ユーザーが貼付・デプロイ）
- `Code.gs`
  - `doGet(e)`: `id` と `token` を検証 → スプレッドシートから該当行を読み、承認ページHTMLを返す
  - `doPost(e)` または `google.script.run`: アクション（approve/reject/skip）を受けてSheet更新
  - HMACトークン検証（`Utilities.computeHmacSha256Signature`）
  - 差し戻し時: GitHub REST `repository_dispatch` を呼んで再生成をトリガー
- `Page.html`
  - 動画プレビュー（Drive のプレビュー埋め込み or 直リンク）
  - 3ボタン＋差し戻しコメント欄（モバイル最適化・大きめボタン）
- **Script Properties（Apps Scriptに保存する設定）**
  - `GSHEET_ID` / `GSHEET_TAB`（=posts）
  - `APPROVAL_SECRET`（Node と共有するHMAC秘密鍵）
  - `GITHUB_TOKEN`（再生成トリガー用PAT）/ `GITHUB_REPO`（JourneysPartner/reel-automation）

### B. Node 側（私が実装）
- `config`: `APPROVAL_BASE_URL`（Web App URL）, `APPROVAL_SECRET`
- `src/approval.js`: `makeApprovalUrl(id)` = `BASE?id=<id>&token=<HMAC-SHA256(id, secret)>`
- `chatwork.buildReviewMessage` に **承認ページURL** を含める／`scheduler` が付与
- `scriptGenerator` / `runPipeline`: `revisionComment` を受け取り、台本生成プロンプトへ「修正指示」として反映（差し戻し対応）

### C. GitHub（私がワークフロー、ユーザーがPAT）
- `.github/workflows/regenerate.yml`: `on: repository_dispatch (types: [regenerate])`
  - payload の `id` と `comment` を受け、`scheduler --id <id> --revision "<comment>"` を実行（再生成→Sheet review→再通知）
- **PAT**（fine-grained, 対象リポジトリのみ）: Contents=R/W, Actions=R/W, Metadata=R。Apps Script に保存

---

## 状態遷移（スプレッドシート status）

```
planned → generating → review ──公開する──→ approved ──(Phase4)──→ published
                          │
                          ├──差し戻し──→ rejected → (再生成) → review …(ループ)
                          └──見送り────→ skipped
```

---

## セキュリティ

- 承認URLに **HMAC-SHA256(id, APPROVAL_SECRET)** トークンを付与。Apps Script が再計算して一致検証（秘密鍵が無いと他idのURLを偽造できない）。
- Web App アクセス権の選択肢（要決定）:
  - **(1) 全員（リンクを知っている人）+ HMACトークン**: ログイン不要でスマホから即操作。手軽。URLが漏れても秘密鍵が無ければ他を操作不可。
  - **(2) Googleアカウントが必要**: 承認者のGoogleログイン必須＋メール限定も可。より厳格だがスマホでログイン手間。
- 機密はリポジトリにコミットしない（`.gitignore` 済）。Apps Script の鍵は Script Properties に保存。

---

## 差し戻し→再生成ループ

- 差し戻しコメントを `revision_comment` に保存し、台本生成プロンプトへ「視聴者/依頼者からの修正指示」として渡して**台本から作り直す**。
- 実行は GitHub Actions（`regenerate.yml`）＝PC不要。ローカルでも `node src/scheduler.js --id <id> --revision "コメント"` で再現可。
- 再生成後 `status=review` に戻し、新プレビューで再通知。
- ループ回数や履歴は将来 Sheet に追記可（まずはコメント上書き）。

---

## あなた（ユーザー）の作業（Phase 2）

1. **Apps Script プロジェクト作成** → 私が渡す `Code.gs` / `Page.html` を貼付
2. **Script Properties 設定**（GSHEET_ID, GSHEET_TAB=posts, APPROVAL_SECRET, GITHUB_TOKEN, GITHUB_REPO）
3. **ウェブアプリとしてデプロイ**（実行=自分 / アクセス=上記で選んだ方式）→ **Web App URL** を取得
4. `.env` と GitHub Secrets に **`APPROVAL_BASE_URL`** と **`APPROVAL_SECRET`** を追加
5. **GitHub PAT 作成**（fine-grained, reel-automation のみ, Contents/Actions R/W）→ Apps Script の Script Properties へ

---

## 実装ステップ（私の作業）

1. Node: `approval.js`（HMAC URL生成）＋ config 追加
2. Node: `buildReviewMessage`/`scheduler` に承認URL付与
3. Node: `scriptGenerator`/`runPipeline`/`scheduler` に `--revision` 対応（差し戻し再生成）
4. Apps Script: `Code.gs` + `Page.html`（私が全文提供）
5. GitHub: `regenerate.yml`
6. ドキュメント: SETUP に Phase 2 手順
7. テスト: 承認ページ表示 → 各ボタン → Sheet反映 → 差し戻しで再生成

---

## 検証（受け入れ）
- 承認ページがスマホで開き、動画が見られる
- 「公開する」で status=approved に変わる
- 「見送り」で status=skipped に変わる
- 「差し戻し」でコメントが保存され、再生成→再通知される
- トークン不一致のURLは拒否される
