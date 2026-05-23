/**
 * リール承認 Web アプリ（Google Apps Script）
 * - ChatWork通知のリンクから開く承認ページ
 * - HMAC-SHA256(id, APPROVAL_SECRET) トークンで保護（Node側 src/approval.js と一致）
 * - 「公開する」→ status=approved / 「見送り」→ status=skipped をスプレッドシートに記録
 *
 * Script Properties（プロジェクトの設定→スクリプト プロパティ）に設定:
 *   GSHEET_ID        … 状態管理スプレッドシートのID
 *   GSHEET_TAB       … タブ名（既定 posts）
 *   APPROVAL_SECRET  … Node の .env と同じ秘密鍵
 */

var PROP = PropertiesService.getScriptProperties();

function doGet(e) {
  return handle_(e, false);
}
function doPost(e) {
  return handle_(e, true);
}

function handle_(e, isPost) {
  var p = (e && e.parameter) || {};
  var id = p.id;
  var token = p.token;
  var action = p.action; // approve / skip（POST時）

  if (!id || !token) return simplePage_('エラー', 'リンクが不正です（id/token なし）。');
  var secret = PROP.getProperty('APPROVAL_SECRET');
  if (!secret) return simplePage_('設定エラー', 'APPROVAL_SECRET が未設定です。');
  if (token !== hmacHex_(id, secret)) return simplePage_('エラー', 'トークンが一致しません。');

  var ss = SpreadsheetApp.openById(PROP.getProperty('GSHEET_ID'));
  var sh = ss.getSheetByName(PROP.getProperty('GSHEET_TAB') || 'posts');
  var data = sh.getDataRange().getValues();
  var header = data[0];
  var col = {};
  header.forEach(function (h, i) { col[h] = i; });

  var rowIdx = -1;
  for (var r = 1; r < data.length; r++) {
    if (String(data[r][col['id']]) === String(id)) { rowIdx = r; break; }
  }
  if (rowIdx === -1) return simplePage_('エラー', '該当データがありません: ' + id);
  var row = data[rowIdx];
  var status = row[col['status']];

  // アクション処理（POST）
  if (isPost && action) {
    if (status === 'approved' || status === 'skipped') {
      return resultPage_('処理済み', 'この投稿は既に「' + status + '」です。');
    }
    var now = new Date().toISOString();
    if (action === 'approve') {
      setCell_(sh, rowIdx, col['status'], 'approved');
      setCell_(sh, rowIdx, col['decided_at'], now);
      return resultPage_('✅ 公開を予約しました', '公開予定日（' + row[col['publish_date']] + '）に自動投稿されます。');
    }
    if (action === 'skip') {
      setCell_(sh, rowIdx, col['status'], 'skipped');
      setCell_(sh, rowIdx, col['decided_at'], now);
      return resultPage_('⏭ 見送りにしました', 'この回は投稿されません。');
    }
    if (action === 'reject') {
      var comment = (p.comment || '').toString();
      setCell_(sh, rowIdx, col['status'], 'rejected');
      setCell_(sh, rowIdx, col['revision_comment'], comment);
      setCell_(sh, rowIdx, col['decided_at'], now);
      dispatchRegenerate_(id, comment);
      return resultPage_('🔁 差し戻しました', 'コメントを反映して再生成します。完了すると、また確認の通知が届きます。');
    }
    return simplePage_('エラー', '不明なアクションです。');
  }

  // 承認ページ表示（GET）
  var t = HtmlService.createTemplateFromFile('Page');
  t.id = id;
  t.token = token;
  t.publishDate = row[col['publish_date']] || '';
  t.theme = row[col['theme']] || '';
  t.status = status || '';
  t.previewUrl = row[col['preview_url']] || '';
  t.fileId = extractFileId_(row[col['preview_url']] || '');
  t.scriptUrl = ScriptApp.getService().getUrl();
  return t.evaluate()
    .setTitle('リール承認')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1');
}

function setCell_(sh, rowIdx, colIdx, val) {
  sh.getRange(rowIdx + 1, colIdx + 1).setValue(val);
}

// 差し戻し時、GitHub に再生成を依頼（repository_dispatch）。
// Script Properties: GITHUB_TOKEN（PAT）, GITHUB_REPO（例 JourneysPartner/reel-automation）
function dispatchRegenerate_(id, comment) {
  var token = PROP.getProperty('GITHUB_TOKEN');
  var repo = PROP.getProperty('GITHUB_REPO');
  if (!token || !repo) return; // 未設定なら Sheet記録のみ（手動再生成も可）
  UrlFetchApp.fetch('https://api.github.com/repos/' + repo + '/dispatches', {
    method: 'post',
    contentType: 'application/json',
    headers: {
      Authorization: 'Bearer ' + token,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
    },
    payload: JSON.stringify({ event_type: 'regenerate', client_payload: { id: id, comment: comment } }),
    muteHttpExceptions: true,
  });
}

function extractFileId_(url) {
  if (!url) return '';
  var m = String(url).match(/\/d\/([^\/]+)/);
  return m ? m[1] : '';
}

function hmacHex_(message, secret) {
  var raw = Utilities.computeHmacSha256Signature(message, secret);
  return raw
    .map(function (b) {
      var v = (b < 0 ? b + 256 : b).toString(16);
      return v.length === 1 ? '0' + v : v;
    })
    .join('');
}

function simplePage_(title, msg) {
  return HtmlService.createHtmlOutput(
    '<div style="font-family:sans-serif;max-width:520px;margin:0 auto;padding:32px;text-align:center">' +
      '<h2>' + title + '</h2><p>' + msg + '</p></div>'
  ).addMetaTag('viewport', 'width=device-width, initial-scale=1');
}

function resultPage_(title, msg) {
  return HtmlService.createHtmlOutput(
    '<div style="font-family:sans-serif;max-width:520px;margin:0 auto;padding:40px;text-align:center">' +
      '<h1 style="font-size:48px;margin:0">' + title.split(' ')[0] + '</h1>' +
      '<h2>' + title + '</h2><p style="color:#444">' + msg + '</p></div>'
  ).addMetaTag('viewport', 'width=device-width, initial-scale=1');
}
