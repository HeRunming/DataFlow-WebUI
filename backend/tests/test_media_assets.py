import json
import os

from app.services import media_assets as ma


def test_sniff_value_modality():
    assert ma.sniff_value_modality("/data/a.JPG") == ma.IMAGE
    assert ma.sniff_value_modality("clips/b.mp4") == ma.VIDEO
    assert ma.sniff_value_modality("https://x.org/s.wav?sig=1") == ma.AUDIO
    assert ma.sniff_value_modality("data:image/png;base64,AAAA") == ma.IMAGE
    assert ma.sniff_value_modality("a cat sitting on a mat.") == ma.TEXT
    assert ma.sniff_value_modality("line one\nsee photo.png") == ma.TEXT
    assert ma.sniff_value_modality(3) == ma.TEXT
    assert ma.sniff_value_modality("") == ma.TEXT


def test_infer_field_modalities_majority_and_lists():
    records = [
        {"id": 1, "image": "/a/1.png", "audio": "x.wav", "frames": ["f1.jpg", "f2.jpg"], "text": "hi"},
        {"id": 2, "image": "/a/2.png", "audio": "", "frames": ["f3.jpg"], "text": "hello"},
        {"id": 3, "image": None, "audio": "y.mp3", "frames": [], "text": "x.png is a file name"},
    ]
    result = ma.infer_field_modalities(records)
    assert result == {
        "id": ma.TEXT,
        "image": ma.IMAGE,
        "audio": ma.AUDIO,
        "frames": ma.IMAGE,
        "text": ma.TEXT,
    }


def test_infer_field_modalities_uses_name_for_extensionless_urls():
    records = [{"video_url": "https://cdn.example.com/v/123"}, {"video_url": "https://cdn.example.com/v/456"}]
    assert ma.infer_field_modalities(records) == {"video_url": ma.VIDEO}
    # Same values under a neutral name stay text.
    assert ma.infer_field_modalities([{"link": r["video_url"]} for r in records]) == {"link": ma.TEXT}


def test_infer_param_modality():
    assert ma.infer_param_modality("input_image_key") == ma.IMAGE
    assert ma.infer_param_modality("input_images_key") == ma.IMAGE
    assert ma.infer_param_modality("input_audio_key") == ma.AUDIO
    assert ma.infer_param_modality("speech_key") == ma.AUDIO
    assert ma.infer_param_modality("input_video_key") == ma.VIDEO
    assert ma.infer_param_modality("input_key") == ma.TEXT
    # "imagine" must not match "image"
    assert ma.infer_param_modality("imagine_key") == ma.TEXT


def test_normalize_media_path(tmp_path):
    base = str(tmp_path)
    assert ma.normalize_media_path("imgs/a.png", base) == os.path.join(base, "imgs", "a.png")
    assert ma.normalize_media_path("./imgs/../b.png", base) == os.path.join(base, "b.png")
    assert ma.normalize_media_path("/abs/c.png", base) == "/abs/c.png"
    assert ma.normalize_media_path("https://x.org/d.png", base) == "https://x.org/d.png"
    assert ma.normalize_media_path(["a.png", "/b.png"], base) == [os.path.join(base, "a.png"), "/b.png"]
    assert ma.normalize_media_path(None, base) is None


def test_normalize_input_file(tmp_path):
    src = tmp_path / "in.jsonl"
    rows = [{"image": "a.png", "text": "rel/not/a/media"}, {"image": "/abs/b.png", "text": "t"}]
    src.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    dst = tmp_path / "out" / "norm.jsonl"

    assert ma.needs_normalization(rows, ["image"]) is True
    changed = ma.normalize_input_file(str(src), str(dst), ["image"])
    assert changed == 1
    out = [json.loads(line) for line in dst.read_text(encoding="utf-8").splitlines()]
    assert out[0]["image"] == str(tmp_path / "a.png")
    assert out[0]["text"] == "rel/not/a/media"  # non-media field untouched
    assert out[1]["image"] == "/abs/b.png"
    assert ma.needs_normalization(out, ["image"]) is False


def test_validate_media_references(tmp_path):
    (tmp_path / "ok.png").write_bytes(b"x")
    records = [{"image": "ok.png"}, {"image": "missing.png"}, {"image": "https://x.org/r.png"}]
    missing = ma.validate_media_references(records, "image", base_dir=str(tmp_path))
    assert missing == [str(tmp_path / "missing.png")]


def test_is_safe_media_path(tmp_path):
    media_root = tmp_path / "media"
    media_root.mkdir()
    img = media_root / "a.png"
    img.write_bytes(b"x")
    secret = tmp_path / "secret.txt"
    secret.write_text("s")
    disguised = media_root / "evil.png"
    os.symlink(secret, disguised)
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"x")

    assert ma.is_safe_media_path(str(img)) is True
    assert ma.is_safe_media_path(str(img), [str(media_root)]) is True
    # traversal out of the allowed root
    assert ma.is_safe_media_path(str(media_root / ".." / "outside.png"), [str(media_root)]) is False
    # non-media extension
    assert ma.is_safe_media_path(str(secret)) is False
    # symlink with media name pointing at a non-media file
    assert ma.is_safe_media_path(str(disguised)) is False
    # missing file / directory
    assert ma.is_safe_media_path(str(media_root / "nope.png")) is False
    assert ma.is_safe_media_path(str(outside)) is True


def test_guess_media_type():
    assert ma.guess_media_type("a.png") == "image/png"
    assert ma.guess_media_type("a.webp") == "image/webp"
    assert ma.guess_media_type("a.mp4") == "video/mp4"
    assert ma.guess_media_type("a.flac") == "audio/flac"
