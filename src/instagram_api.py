"""
Meta Graph API (Instagram) クライアント

仕様の参考: https://developers.facebook.com/docs/instagram-api/

提供する3つの投稿メソッド:
  - post_single_image(image_path, caption)
  - post_carousel(image_paths, caption)        # 子コンテナ→親コンテナ→公開
  - post_reel(video_path, caption)             # 現時点はスタブ（STEP 9 完了後に実装）

設計メモ:
- 認証情報は .env から META_ACCESS_TOKEN / INSTAGRAM_BUSINESS_ACCOUNT_ID を読む
- ローカル画像は事前に "公開URL" にアップロードする必要がある
  → S3 / Cloudflare R2 への upload は upload_image_to_public_url() に TODO スタブを置く
- リトライ: 5xx 系で最大 max_retries 回、指数バックオフ
- レート制限: 24時間あたり25件（プロセス内カウンタ。永続化は今後の拡張）
- dry_run=True の場合はネットワーク呼び出しを一切行わずスタブ結果を返す
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


# ----------------------------- Constants ---------------------------------

ROOT = Path(__file__).resolve().parents[1]
GRAPH_API_VERSION = "v21.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

# レート制限のデフォルト
RATE_LIMIT_PER_DAY = 25
RATE_LIMIT_WINDOW_SEC = 86400

# 公開処理待機のデフォルト（画像 / 動画で異なる）
IMAGE_POLL_TIMEOUT_SEC = 120
IMAGE_POLL_INTERVAL_SEC = 5
REEL_POLL_TIMEOUT_SEC = 600
REEL_POLL_INTERVAL_SEC = 8


load_dotenv(ROOT / ".env")


class InstagramAPIError(Exception):
    """Instagram API 呼び出し失敗"""


# ----------------------------- Rate limit state --------------------------

@dataclass
class RateLimitState:
    """簡易レートリミッタ。24時間ウィンドウで25件まで。
    プロセス内のみで保持（複数プロセス間では共有されない）。
    永続化が必要なら、ファイル/Redis に書き出す形へ拡張してください。
    """
    timestamps: list[float] = field(default_factory=list)
    limit: int = RATE_LIMIT_PER_DAY
    window_sec: int = RATE_LIMIT_WINDOW_SEC

    def _purge(self) -> None:
        cutoff = time.time() - self.window_sec
        self.timestamps = [t for t in self.timestamps if t >= cutoff]

    def can_post(self) -> bool:
        self._purge()
        return len(self.timestamps) < self.limit

    def remaining(self) -> int:
        self._purge()
        return max(0, self.limit - len(self.timestamps))

    def record(self) -> None:
        self.timestamps.append(time.time())


# ----------------------------- Public URL upload (TODO) ------------------

_CLOUDINARY_CONFIGURED = False


def _configure_cloudinary() -> None:
    """環境変数から Cloudinary を初期化する（プロセス内で1回だけ実行）。"""
    global _CLOUDINARY_CONFIGURED
    if _CLOUDINARY_CONFIGURED:
        return

    cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME", "").strip()
    api_key = os.environ.get("CLOUDINARY_API_KEY", "").strip()
    api_secret = os.environ.get("CLOUDINARY_API_SECRET", "").strip()

    missing = [
        n for n, v in [
            ("CLOUDINARY_CLOUD_NAME", cloud_name),
            ("CLOUDINARY_API_KEY", api_key),
            ("CLOUDINARY_API_SECRET", api_secret),
        ] if not v
    ]
    if missing:
        raise InstagramAPIError(
            f"Cloudinary 認証情報が .env に設定されていません: {', '.join(missing)}"
        )

    try:
        import cloudinary  # type: ignore
    except ImportError as e:
        raise InstagramAPIError(
            "cloudinary パッケージが見つかりません。`pip install cloudinary` を実行してください。"
        ) from e

    cloudinary.config(
        cloud_name=cloud_name,
        api_key=api_key,
        api_secret=api_secret,
        secure=True,
    )
    _CLOUDINARY_CONFIGURED = True


def upload_image_to_public_url(local_path: Path, dry_run: bool = False) -> str:
    """ローカル画像を Cloudinary にアップロードして公開URL (secure_url) を返す。

    アップロード仕様:
      - resource_type: image
      - public_id   : "instagram/<date>/<filename_stem>"
                       例: instagram/2026-06-01/slide_1
      - overwrite   : True（同じ public_id なら上書き）

    Parameters
    ----------
    local_path : Path
        アップロード対象のローカル画像。output/posts/<date>/slide_N.png 想定。
    dry_run : bool
        True の場合は実際にアップロードせずスタブURLを返す。
    """
    if dry_run:
        return f"https://example.invalid/stub/{local_path.name}"

    _configure_cloudinary()

    # 親ディレクトリ名を date として扱う（output/posts/<date>/slide_N.png 想定）
    date = local_path.parent.name
    public_id = f"instagram/{date}/{local_path.stem}"

    # cloudinary.uploader は遅延 import（_configure_cloudinary 後で安全）
    import cloudinary.uploader  # type: ignore

    try:
        result = cloudinary.uploader.upload(
            str(local_path),
            public_id=public_id,
            overwrite=True,
            resource_type="image",
        )
    except Exception as e:
        raise InstagramAPIError(
            f"Cloudinary アップロード失敗 (public_id={public_id}): {e}"
        ) from e

    secure_url = result.get("secure_url")
    if not secure_url:
        raise InstagramAPIError(
            f"Cloudinary レスポンスに secure_url がありません: keys={list(result.keys())}"
        )
    return secure_url


def upload_video_to_public_url(local_path: Path, dry_run: bool = False) -> str:
    """ローカル動画を Cloudinary にアップロードして公開URLを返す（リール用）。

    画像と同じ Cloudinary アカウントを使用。resource_type を 'video' に切り替える。
    public_id: instagram_reels/<slug>/<filename_stem>
    """
    if dry_run:
        return f"https://example.invalid/stub/{local_path.name}"

    _configure_cloudinary()

    # 親ディレクトリ名を slug として扱う（output/reels/<slug>/reel.mp4 想定）
    slug = local_path.parent.name
    public_id = f"instagram_reels/{slug}/{local_path.stem}"

    import cloudinary.uploader  # type: ignore

    try:
        result = cloudinary.uploader.upload_large(
            str(local_path),
            public_id=public_id,
            overwrite=True,
            resource_type="video",
        )
    except Exception as e:
        raise InstagramAPIError(
            f"Cloudinary 動画アップロード失敗 (public_id={public_id}): {e}"
        ) from e

    secure_url = result.get("secure_url")
    if not secure_url:
        raise InstagramAPIError(
            f"Cloudinary レスポンスに secure_url がありません: keys={list(result.keys())}"
        )
    return secure_url


# ----------------------------- Main client -------------------------------

class InstagramAPI:
    def __init__(
        self,
        access_token: str | None = None,
        ig_user_id: str | None = None,
        dry_run: bool = False,
        max_retries: int = 3,
        rate_limit_state: RateLimitState | None = None,
    ) -> None:
        self.access_token = (access_token or os.environ.get("META_ACCESS_TOKEN", "")).strip()
        self.ig_user_id = (ig_user_id or os.environ.get("INSTAGRAM_BUSINESS_ACCOUNT_ID", "")).strip()
        self.dry_run = dry_run
        self.max_retries = max(1, int(max_retries))
        self.rate_limit = rate_limit_state or RateLimitState()

        if not self.dry_run:
            self._validate_credentials()

    # --------- Credentials / rate limit ---------

    def _validate_credentials(self) -> None:
        missing = []
        if not self.access_token:
            missing.append("META_ACCESS_TOKEN")
        if not self.ig_user_id:
            missing.append("INSTAGRAM_BUSINESS_ACCOUNT_ID")
        if missing:
            raise InstagramAPIError(
                f"認証情報が .env に設定されていません: {', '.join(missing)}\n"
                "META_API_SETUP.md を参照してセットアップしてください。\n"
                "確認のみであれば --dry-run で動作確認できます。"
            )

    def _ensure_rate_limit(self) -> None:
        if not self.rate_limit.can_post():
            raise InstagramAPIError(
                f"24時間レート制限に達しました（上限{self.rate_limit.limit}件）。"
                "明日以降に再試行してください。"
            )

    # --------- HTTP layer ---------

    def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        url = f"{GRAPH_BASE}/{path}"
        params = dict(kwargs.pop("params", {}) or {})
        params.setdefault("access_token", self.access_token)
        kwargs["params"] = params

        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                resp = requests.request(method, url, timeout=60, **kwargs)
            except requests.RequestException as e:
                last_exc = e
                self._sleep_backoff(attempt)
                continue

            if resp.status_code == 200:
                return resp.json()

            # Retryable: 5xx
            if 500 <= resp.status_code < 600 and attempt < self.max_retries - 1:
                last_exc = InstagramAPIError(
                    f"{resp.status_code} {resp.text[:300]}"
                )
                self._sleep_backoff(attempt)
                continue

            # Non-retryable error
            raise InstagramAPIError(
                f"Graph API error {resp.status_code} on {method} {path}: {resp.text[:500]}"
            )

        raise InstagramAPIError(
            f"Request failed after {self.max_retries} retries: {last_exc}"
        )

    @staticmethod
    def _sleep_backoff(attempt: int) -> None:
        # 1s, 2s, 4s
        time.sleep(2 ** attempt)

    # --------- Media polling ---------

    def _wait_media_ready(
        self,
        creation_id: str,
        timeout_sec: int = IMAGE_POLL_TIMEOUT_SEC,
        interval_sec: int = IMAGE_POLL_INTERVAL_SEC,
    ) -> None:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            info = self._request("GET", creation_id, params={"fields": "status_code,status"})
            code = info.get("status_code") or info.get("status")
            if code == "FINISHED":
                return
            if code == "ERROR":
                raise InstagramAPIError(
                    f"Media processing error (creation_id={creation_id}): {info}"
                )
            time.sleep(interval_sec)
        raise InstagramAPIError(
            f"Media processing timeout after {timeout_sec}s (creation_id={creation_id})"
        )

    def _fetch_permalink(self, media_id: str) -> str:
        info = self._request("GET", media_id, params={"fields": "permalink"})
        return info.get("permalink", "")

    # --------- Public posting methods ---------

    def post_single_image(self, image_path: Path | str, caption: str) -> dict:
        """単一画像投稿。"""
        image_path = Path(image_path)
        if not self.dry_run:
            self._ensure_rate_limit()

        image_url = upload_image_to_public_url(image_path, dry_run=self.dry_run)

        if self.dry_run:
            return {
                "dry_run": True,
                "media_type": "IMAGE",
                "image_path": str(image_path),
                "image_url": image_url,
                "caption_chars": len(caption),
            }

        # 1. Create container
        container = self._request(
            "POST",
            f"{self.ig_user_id}/media",
            data={"image_url": image_url, "caption": caption},
        )
        creation_id = container["id"]

        # 2. Wait
        self._wait_media_ready(creation_id)

        # 3. Publish
        publish = self._request(
            "POST",
            f"{self.ig_user_id}/media_publish",
            data={"creation_id": creation_id},
        )
        media_id = publish["id"]

        # 4. Permalink
        permalink = self._fetch_permalink(media_id)

        self.rate_limit.record()
        return {
            "media_type": "IMAGE",
            "media_id": media_id,
            "creation_id": creation_id,
            "permalink": permalink,
        }

    def post_carousel(self, image_paths: list[Path | str], caption: str) -> dict:
        """カルーセル投稿（2〜10枚）。"""
        image_paths = [Path(p) for p in image_paths]
        if not (2 <= len(image_paths) <= 10):
            raise InstagramAPIError(
                f"カルーセルは2〜10枚で指定してください（現在: {len(image_paths)}枚）"
            )
        if not self.dry_run:
            self._ensure_rate_limit()

        image_urls = [upload_image_to_public_url(p, dry_run=self.dry_run) for p in image_paths]

        if self.dry_run:
            return {
                "dry_run": True,
                "media_type": "CAROUSEL",
                "child_count": len(image_urls),
                "image_paths": [str(p) for p in image_paths],
                "image_urls": image_urls,
                "caption_chars": len(caption),
            }

        # 1. Create child containers
        child_ids: list[str] = []
        for url in image_urls:
            child = self._request(
                "POST",
                f"{self.ig_user_id}/media",
                data={"image_url": url, "is_carousel_item": "true"},
            )
            child_ids.append(child["id"])

        # 2. Wait for children
        for cid in child_ids:
            self._wait_media_ready(cid)

        # 3. Create parent
        parent = self._request(
            "POST",
            f"{self.ig_user_id}/media",
            data={
                "media_type": "CAROUSEL",
                "children": ",".join(child_ids),
                "caption": caption,
            },
        )
        parent_id = parent["id"]
        self._wait_media_ready(parent_id)

        # 4. Publish
        publish = self._request(
            "POST",
            f"{self.ig_user_id}/media_publish",
            data={"creation_id": parent_id},
        )
        media_id = publish["id"]

        # 5. Permalink
        permalink = self._fetch_permalink(media_id)

        self.rate_limit.record()
        return {
            "media_type": "CAROUSEL",
            "media_id": media_id,
            "creation_id": parent_id,
            "child_ids": child_ids,
            "permalink": permalink,
        }

    def post_story(self, image_path: Path | str) -> dict:
        """ストーリーズに画像を投稿する（24時間で消える）。

        - Meta Graph API: media_type=STORIES
        - キャプション・ハッシュタグはストーリーズでは表示されないため引数は取らない
        - 画像の推奨サイズは 1080x1920（9:16 縦型）
        """
        image_path = Path(image_path)
        if not self.dry_run:
            self._ensure_rate_limit()

        image_url = upload_image_to_public_url(image_path, dry_run=self.dry_run)

        if self.dry_run:
            return {
                "dry_run": True,
                "media_type": "STORIES",
                "image_path": str(image_path),
                "image_url": image_url,
            }

        # 1. Create story container
        container = self._request(
            "POST",
            f"{self.ig_user_id}/media",
            data={"media_type": "STORIES", "image_url": image_url},
        )
        creation_id = container["id"]

        # 2. Wait for processing
        self._wait_media_ready(creation_id)

        # 3. Publish
        publish = self._request(
            "POST",
            f"{self.ig_user_id}/media_publish",
            data={"creation_id": creation_id},
        )
        media_id = publish["id"]

        # 4. Permalink (story permalink may be empty / temporary)
        permalink = ""
        try:
            info = self._request("GET", media_id, params={"fields": "permalink"})
            permalink = info.get("permalink", "")
        except Exception:
            pass

        self.rate_limit.record()
        return {
            "media_type": "STORIES",
            "media_id": media_id,
            "creation_id": creation_id,
            "permalink": permalink,
        }

    def post_reel(self, video_path: Path | str, caption: str) -> dict:
        """リール投稿。

        フロー:
          1. Cloudinary に動画をアップロードして公開URLを取得
          2. POST /{ig_user_id}/media (media_type=REELS, video_url, caption)
          3. _wait_media_ready で処理完了を待つ（最大 REEL_POLL_TIMEOUT_SEC 秒）
          4. POST /{ig_user_id}/media_publish で公開
          5. permalink を取得
        """
        video_path = Path(video_path)
        if not self.dry_run:
            self._ensure_rate_limit()

        video_url = upload_video_to_public_url(video_path, dry_run=self.dry_run)

        if self.dry_run:
            return {
                "dry_run": True,
                "media_type": "REELS",
                "video_path": str(video_path),
                "video_url": video_url,
                "caption_chars": len(caption),
            }

        # 1. リールコンテナ作成
        container = self._request(
            "POST",
            f"{self.ig_user_id}/media",
            data={
                "media_type": "REELS",
                "video_url": video_url,
                "caption": caption,
            },
        )
        creation_id = container["id"]

        # 2. 処理完了待ち（動画は時間がかかるので長めのタイムアウト）
        self._wait_media_ready(
            creation_id,
            timeout_sec=REEL_POLL_TIMEOUT_SEC,
            interval_sec=REEL_POLL_INTERVAL_SEC,
        )

        # 3. 公開
        publish = self._request(
            "POST",
            f"{self.ig_user_id}/media_publish",
            data={"creation_id": creation_id},
        )
        media_id = publish["id"]

        # 4. permalink 取得
        permalink = self._fetch_permalink(media_id)

        self.rate_limit.record()
        return {
            "media_type": "REELS",
            "media_id": media_id,
            "creation_id": creation_id,
            "video_url": video_url,
            "permalink": permalink,
        }
