"""Tests for the human-player avatar feature (schema v118).

Covers `UserAvatarRepository`, the shared image-processing helper, the bio
accessors on `UserPreferencesRepository`, and `UserAvatarService` (with the
image LLM and network download stubbed out).
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

import pytest
from PIL import Image

from poker.image_processing import detect_image_mimetype, process_avatar_image
from poker.repositories.schema_manager import SchemaManager
from poker.repositories.user_avatar_repository import UserAvatarRepository
from poker.repositories.user_preferences_repository import (
    MAX_BIO_LENGTH,
    UserPreferencesRepository,
)
from poker.user_avatar_service import (
    UserAvatarError,
    UserAvatarService,
    _validate_public_url,
)


def _png_bytes(size=(300, 200), color=(10, 20, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new('RGB', size, color).save(buf, 'PNG')
    return buf.getvalue()


@pytest.fixture
def db_path():
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "avatars.db")
        SchemaManager(path).ensure_schema()
        yield path


@pytest.fixture
def repo(db_path):
    r = UserAvatarRepository(db_path)
    yield r
    r.close()


@pytest.fixture
def prefs(db_path):
    r = UserPreferencesRepository(db_path)
    yield r
    r.close()


# --- image processing -------------------------------------------------------


def test_detect_mimetype():
    assert detect_image_mimetype(_png_bytes()) == 'image/png'
    assert detect_image_mimetype(b'\xff\xd8\xff\xe0junk') == 'image/jpeg'
    assert detect_image_mimetype(b'not an image') is None


def test_process_normalizes_to_square_full_and_circular_icon():
    icon, full, fw, fh = process_avatar_image(_png_bytes(size=(300, 200)))
    assert (fw, fh) == (512, 512)
    # Both artifacts are valid PNGs; the icon carries an alpha channel (circle).
    assert Image.open(io.BytesIO(full)).size == (512, 512)
    icon_img = Image.open(io.BytesIO(icon))
    assert icon_img.size == (256, 256)
    assert icon_img.mode == 'RGBA'


# --- repository -------------------------------------------------------------


def test_upsert_and_fetch(repo):
    icon, full, _, _ = process_avatar_image(_png_bytes())
    public_id = repo.upsert_avatar('guest_a', icon, full, 'image/png', 'upload')
    assert public_id
    assert repo.get_public_id('guest_a') == public_id
    assert repo.get_image_by_public_id(public_id)['image_data'] == icon
    assert repo.get_image_by_public_id(public_id, full=True)['image_data'] == full


def test_public_id_stable_across_reupload(repo):
    icon, full, _, _ = process_avatar_image(_png_bytes())
    first = repo.upsert_avatar('guest_a', icon, full, 'image/png', 'upload')
    second = repo.upsert_avatar('guest_a', icon, full, 'image/png', 'generated')
    assert first == second  # URL already embedded elsewhere stays valid


def test_guest_and_google_ids_both_work(repo):
    # No FK to users — a guest id (not in the users table) is accepted.
    icon, full, _, _ = process_avatar_image(_png_bytes())
    repo.upsert_avatar('guest_xyz', icon, full, 'image/png', 'upload')
    repo.upsert_avatar('google_123', icon, full, 'image/png', 'upload')
    assert repo.get_public_id('guest_xyz')
    assert repo.get_public_id('google_123')


def test_missing_and_delete(repo):
    assert repo.get_public_id('nobody') is None
    assert repo.get_image_by_public_id('no-such-id') is None
    icon, full, _, _ = process_avatar_image(_png_bytes())
    repo.upsert_avatar('guest_a', icon, full, 'image/png', 'upload')
    assert repo.delete('guest_a') is True
    assert repo.delete('guest_a') is False
    assert repo.get_public_id('guest_a') is None


# --- bio accessors ----------------------------------------------------------


def test_bio_round_trip_and_default(prefs):
    assert prefs.get_bio('nobody') == ''
    prefs.set_bio('u1', '  I bluff every hand  ')
    assert prefs.get_bio('u1') == 'I bluff every hand'  # trimmed


def test_bio_capped(prefs):
    prefs.set_bio('u1', 'x' * (MAX_BIO_LENGTH + 200))
    assert len(prefs.get_bio('u1')) == MAX_BIO_LENGTH


def test_bio_clear(prefs):
    prefs.set_bio('u1', 'something')
    prefs.set_bio('u1', '   ')
    assert prefs.get_bio('u1') == ''


def test_bio_and_world_pace_coexist(prefs):
    # Bio shares the user_preferences row with world_pace.
    prefs.set_world_pace('u1', 'subtle')
    prefs.set_bio('u1', 'hello')
    assert prefs.get_world_pace('u1') == 'subtle'
    assert prefs.get_bio('u1') == 'hello'


# --- service ----------------------------------------------------------------


def test_service_store_from_bytes(repo):
    svc = UserAvatarService(repo)
    url = svc.store_from_bytes('guest_a', _png_bytes())
    public_id = repo.get_public_id('guest_a')
    assert url.startswith(f'/api/user-avatar/{public_id}')
    assert '?v=' in url  # cache-busting version token
    assert svc.get_avatar_url('guest_a') == url


def test_service_rejects_bad_format(repo):
    svc = UserAvatarService(repo)
    with pytest.raises(UserAvatarError):
        svc.store_from_bytes('guest_a', b'this is not an image')


def test_service_generate_from_prompt(repo, monkeypatch):
    """Generation: stub the LLM client and the image download."""
    png = _png_bytes()

    class _FakeResponse:
        is_error = False
        error_message = None
        error_code = None
        url = 'http://fake/image.png'

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        def generate_image(self, **kwargs):
            assert 'playing poker' in kwargs['prompt']  # prompt was wrapped
            return _FakeResponse()

    class _FakeUrlOpen:
        def __init__(self, url, timeout=None):
            self._url = url

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, *args):
            return png

    monkeypatch.setattr('poker.user_avatar_service.LLMClient', _FakeClient)
    monkeypatch.setattr('urllib.request.urlopen', _FakeUrlOpen)

    svc = UserAvatarService(repo)
    url = svc.generate_from_prompt('guest_a', 'a grizzled cowboy')
    assert url.startswith('/api/user-avatar/')
    assert repo.get_image_by_public_id(repo.get_public_id('guest_a')) is not None


def test_service_generate_empty_prompt_rejected(repo):
    svc = UserAvatarService(repo)
    with pytest.raises(UserAvatarError):
        svc.generate_from_prompt('guest_a', '   ')


@pytest.mark.parametrize(
    'bad_url',
    [
        'ftp://example.com/x.png',  # non-http scheme
        'file:///etc/passwd',  # file scheme
        'http://127.0.0.1/x.png',  # loopback
        'http://localhost/x.png',  # loopback (resolved)
        'http://169.254.169.254/latest/meta-data/',  # link-local metadata endpoint
        'http://10.0.0.5/x.png',  # private range
        'http:///nohost',  # missing host
    ],
)
def test_validate_public_url_rejects_internal(bad_url):
    with pytest.raises(UserAvatarError):
        _validate_public_url(bad_url)


def test_validate_public_url_allows_public():
    # A literal public IP passes (avoids depending on external DNS in tests).
    _validate_public_url('https://8.8.8.8/avatar.png')


def test_service_generation_error_surfaces(repo, monkeypatch):
    class _ErrResponse:
        is_error = True
        error_message = 'content_policy_violation'
        error_code = 'content_policy_violation'
        url = None

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        def generate_image(self, **kwargs):
            return _ErrResponse()

    monkeypatch.setattr('poker.user_avatar_service.LLMClient', _FakeClient)
    svc = UserAvatarService(repo)
    with pytest.raises(UserAvatarError):
        svc.generate_from_prompt('guest_a', 'something blocked')
