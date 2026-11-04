from __future__ import annotations

import asyncio
import io
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, NewType, TypedDict, TYPE_CHECKING

from utils import json_load, json_save
from constants import URLType, CACHE_PATH, CACHE_DB

from PIL import Image as Image_module
from PIL.ImageTk import PhotoImage


if TYPE_CHECKING:
    from gui import GUIManager
    from PIL.Image import Image
    from typing_extensions import TypeAlias


ImageHash = NewType("ImageHash", str)
ImageSize: TypeAlias = "tuple[int, int]"
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_CONCURRENT_DOWNLOADS = 8
# Solid 10x10 fallbacks / broken CDN stubs land around this size on disk.
_POISON_FILE_BYTES = 200

# Twitch CDN dimension suffix before the file extension (jpg/png/gif/webp/jpeg).
_DIMS_PATTERN = re.compile(r"-\d+x\d+(?=\.(?:jpe?g|png|gif|webp)$)", re.I)
_EXT_PATTERN = re.compile(r"(\.(?:jpe?g|png|gif|webp))$", re.I)
_BOXART_HINT = re.compile(r"ttv-boxart|/boxart", re.I)

# Dark placeholder matching the Twitch-inspired UI (avoid white flash).
_PLACEHOLDER_RGB = (24, 24, 27)
_PLACEHOLDER_HASH = ImageHash("__placeholder__.png")


class ExpiringHash(TypedDict):
    hash: ImageHash
    expires: datetime


Hashes = Dict[URLType, ExpiringHash]
default_database: Hashes = {}


def remove_dimensions(url: URLType | str) -> URLType:
    return URLType(_DIMS_PATTERN.sub("", str(url)))


def sized_boxart_url(url: URLType | str, size: ImageSize) -> URLType:
    """
    Request a CDN-resized box art URL when Twitch supports -WxH suffixes.
    Benefit / generic asset URLs are returned unchanged (appending -80x80 can 404).
    """
    raw = str(url)
    if not (_BOXART_HINT.search(raw) or _DIMS_PATTERN.search(raw)):
        return URLType(raw)
    width, height = size
    if width <= 0 or height <= 0:
        return URLType(raw)
    base = remove_dimensions(URLType(raw))
    if not _EXT_PATTERN.search(str(base)):
        return base
    return URLType(_EXT_PATTERN.sub(rf"-{width}x{height}\1", str(base)))


def _is_solid_hash(img_hash: str) -> bool:
    """Perceptual hash of a flat-color image collapses to a single hex digit repeated."""
    hexpart = img_hash[:-4] if img_hash.endswith(".png") else img_hash
    return bool(hexpart) and len(set(hexpart.lower())) <= 1


class ImageCache:
    LIFETIME = timedelta(days=7)

    def __init__(self, manager: GUIManager) -> None:
        self._root = manager._root
        self._twitch = manager._twitch
        cleanup: bool = False
        CACHE_PATH.mkdir(parents=True, exist_ok=True)
        try:
            self._hashes: Hashes = json_load(CACHE_DB, default_database, merge=False)
        except json.JSONDecodeError:
            # if we can't load the mapping file, delete all existing files,
            # then reinitialize the image cache anew
            cleanup = True
            self._hashes = default_database.copy()
        self._images: dict[ImageHash, Image] = {}
        self._photos: dict[tuple[ImageHash, ImageSize], PhotoImage] = {}
        self._placeholders: dict[ImageSize, PhotoImage] = {}
        # Protects _hashes / _inflight bookkeeping only — not downloads themselves.
        self._map_lock = asyncio.Lock()
        self._inflight: dict[URLType, asyncio.Future[tuple[ImageHash, Image]]] = {}
        self._download_sem = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
        self._altered: bool = False
        # cleanup the URLs + purge solid-color stubs that were wrongly cached as art
        hash_counts: dict[ImageHash, int] = {}
        now = datetime.now(timezone.utc)
        for url, hash_dict in list(self._hashes.items()):
            img_hash = hash_dict["hash"]
            path = CACHE_PATH / img_hash
            poisoned = (
                _is_solid_hash(img_hash)
                or (path.exists() and path.stat().st_size < _POISON_FILE_BYTES)
            )
            if poisoned or now >= hash_dict["expires"]:
                del self._hashes[url]
                self._altered = True
                continue
            if img_hash not in hash_counts:
                hash_counts[img_hash] = 0
            hash_counts[img_hash] += 1
        for img_hash, count in hash_counts.items():
            if count == 0:
                # hashes come with an extension already
                CACHE_PATH.joinpath(img_hash).unlink(missing_ok=True)
                # NOTE: The hashes are deleted from self._hashes above
        # Remove leftover solid stubs even if unreferenced
        for path in CACHE_PATH.glob("*.png"):
            if _is_solid_hash(path.name) or path.stat().st_size < _POISON_FILE_BYTES:
                path.unlink(missing_ok=True)
                self._altered = True
        if cleanup:
            # This cleanups the cache folder from unused PNG files
            orphans = [
                file.name for file in CACHE_PATH.glob("*.png") if file.name not in hash_counts
            ]
            for filename in orphans:
                CACHE_PATH.joinpath(filename).unlink(missing_ok=True)

    def save(self, *, force: bool = False) -> None:
        if self._altered or force:
            json_save(CACHE_DB, self._hashes, sort=True)
            self._altered = False

    def _new_expires(self) -> datetime:
        return datetime.now(timezone.utc) + self.LIFETIME

    def _hash(self, image: Image) -> ImageHash:
        pixel_data = list(
            image.resize((10, 10), Image_module.Resampling.LANCZOS).convert("L").getdata()
        )
        avg_pixel = sum(pixel_data) / len(pixel_data)
        bits = "".join("1" if px >= avg_pixel else "0" for px in pixel_data)
        return ImageHash(f"{int(bits, 2):x}.png")

    @staticmethod
    def _is_poison_image(image: Image) -> bool:
        # Our failure fallback is always a flat 10x10 swatch — never treat it as CDN art.
        return image.size == (10, 10)

    def placeholder(self, size: ImageSize) -> PhotoImage:
        """Cached solid PhotoImage used while a real asset is still downloading."""
        if size in self._placeholders:
            return self._placeholders[size]
        image = Image_module.new("RGB", size, _PLACEHOLDER_RGB)
        photo = PhotoImage(master=self._root, image=image)
        self._placeholders[size] = photo
        return photo

    def _resolve_fetch_url(self, url: URLType, size: ImageSize | None) -> URLType:
        if size is None:
            return url
        return sized_boxart_url(url, size)

    async def _load_image_from_disk(self, img_hash: ImageHash) -> Image | None:
        path = CACHE_PATH / img_hash
        if _is_solid_hash(img_hash):
            return None
        if path.exists() and path.stat().st_size < _POISON_FILE_BYTES:
            return None

        def _open() -> Image:
            loaded = Image_module.open(path)
            loaded.load()
            return loaded

        try:
            image = await asyncio.to_thread(_open)
        except (FileNotFoundError, Image_module.UnidentifiedImageError, OSError):
            return None
        if self._is_poison_image(image):
            return None
        return image

    def _decode_image_bytes(self, raw_image: bytes) -> Image:
        with Image_module.open(io.BytesIO(raw_image)) as loaded:
            width, height = loaded.size
            if width <= 0 or height <= 0:
                raise ValueError("Image has invalid dimensions")
            if width * height > MAX_IMAGE_PIXELS:
                raise ValueError("Image has too many pixels")
            loaded.load()
            return loaded.copy()

    def _persist_image(self, img_hash: ImageHash, image: Image) -> None:
        image.save(CACHE_PATH / img_hash)

    async def _download_once(self, url: URLType) -> Image | None:
        try:
            async with self._twitch.request("GET", url) as response:
                if response.status == 404:
                    return None
                if response.status >= 400:
                    return None
                if (
                    response.content_length is not None
                    and response.content_length > MAX_IMAGE_BYTES
                ):
                    return None
                # twitch.request() already consumed the body via response.read();
                # do not read response.content again (can hang on an empty stream).
                raw_image = await response.read()
                if not raw_image or len(raw_image) > MAX_IMAGE_BYTES:
                    return None
            return await asyncio.to_thread(self._decode_image_bytes, raw_image)
        except Exception:
            return None

    async def _download_image(self, url: URLType) -> Image | None:
        async with self._download_sem:
            image = await self._download_once(url)
            # Sized boxart 404/empty → retry original unsized CDN URL once.
            if image is None and _BOXART_HINT.search(str(url)) and _DIMS_PATTERN.search(str(url)):
                image = await self._download_once(remove_dimensions(url))
            if image is None or self._is_poison_image(image):
                return None
            return image

    async def _ensure_image(self, fetch_url: URLType) -> tuple[ImageHash, Image]:
        """Return (hash, PIL image), downloading at most once per URL concurrently."""
        wait_for: asyncio.Future[tuple[ImageHash, Image]] | None = None
        async with self._map_lock:
            entry = self._hashes.get(fetch_url)
            if entry is not None:
                img_hash = entry["hash"]
                if _is_solid_hash(img_hash):
                    del self._hashes[fetch_url]
                    self._altered = True
                else:
                    cached = self._images.get(img_hash)
                    if cached is not None and not self._is_poison_image(cached):
                        return img_hash, cached
                    # Disk may still have it — leave mapping, try load outside lock.
                    inflight = self._inflight.get(fetch_url)
                    if inflight is None:
                        loop = asyncio.get_running_loop()
                        inflight = loop.create_future()
                        self._inflight[fetch_url] = inflight
                        asyncio.create_task(
                            self._complete_load(fetch_url, img_hash, inflight, prefer_disk=True)
                        )
                    wait_for = inflight

            if wait_for is None:
                inflight = self._inflight.get(fetch_url)
                if inflight is None:
                    loop = asyncio.get_running_loop()
                    inflight = loop.create_future()
                    self._inflight[fetch_url] = inflight
                    asyncio.create_task(
                        self._complete_load(fetch_url, None, inflight, prefer_disk=False)
                    )
                wait_for = inflight

        return await wait_for

    async def _complete_load(
        self,
        fetch_url: URLType,
        known_hash: ImageHash | None,
        fut: asyncio.Future[tuple[ImageHash, Image]],
        *,
        prefer_disk: bool,
    ) -> None:
        try:
            image: Image | None = None
            img_hash = known_hash
            if prefer_disk and known_hash is not None:
                image = await self._load_image_from_disk(known_hash)
                if image is not None:
                    self._images[known_hash] = image
                else:
                    # Stale / poisoned mapping — drop so a real download can replace it.
                    async with self._map_lock:
                        entry = self._hashes.get(fetch_url)
                        if entry is not None and entry["hash"] == known_hash:
                            del self._hashes[fetch_url]
                            self._altered = True
                    img_hash = None
            if image is None:
                image = await self._download_image(fetch_url)
                if image is None:
                    # Do NOT write fallback into mapping.json — that permanently blacks out art.
                    image = Image_module.new("RGB", (10, 10), _PLACEHOLDER_RGB)
                    img_hash = _PLACEHOLDER_HASH
                else:
                    img_hash = await asyncio.to_thread(self._hash, image)
                    if _is_solid_hash(img_hash) or self._is_poison_image(image):
                        image = Image_module.new("RGB", (10, 10), _PLACEHOLDER_RGB)
                        img_hash = _PLACEHOLDER_HASH
                    else:
                        self._images[img_hash] = image
                        await asyncio.to_thread(self._persist_image, img_hash, image)
                        async with self._map_lock:
                            self._hashes[fetch_url] = {
                                "hash": img_hash,
                                "expires": self._new_expires(),
                            }
                            self._altered = True
            assert img_hash is not None
            if not fut.done():
                fut.set_result((img_hash, image))
        except Exception as exc:
            if not fut.done():
                fut.set_exception(exc)
        finally:
            async with self._map_lock:
                if self._inflight.get(fetch_url) is fut:
                    del self._inflight[fetch_url]

    async def get(self, url: URLType, size: ImageSize | None = None) -> PhotoImage:
        fetch_url = self._resolve_fetch_url(url, size)
        try:
            img_hash, image = await self._ensure_image(fetch_url)
        except Exception:
            return self.placeholder(size or (10, 10))
        if img_hash == _PLACEHOLDER_HASH or self._is_poison_image(image):
            return self.placeholder(size or image.size)
        if size is None:
            size = image.size
        photo_key = (img_hash, size)
        existing = self._photos.get(photo_key)
        if existing is not None:
            return existing

        def _resize() -> Image:
            if image.size == size:
                return image
            try:
                return image.resize(size, Image_module.Resampling.LANCZOS)
            except OSError:
                return Image_module.new("RGB", size, _PLACEHOLDER_RGB)

        resized = await asyncio.to_thread(_resize) if image.size != size else image
        # PhotoImage must be created on the Tk thread (this app's asyncio loop).
        photo = PhotoImage(master=self._root, image=resized)
        # Another concurrent get() may have won the race — reuse the stored one.
        return self._photos.setdefault(photo_key, photo)
