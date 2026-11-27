"""POST-04 temas importables (Fase A): WCAG math, esquema v1, ThemeStore y rutas /themes.

Fase A cubre SOLO el backend: el módulo puro (S1/S2), el almacén seguro
(S3/S4) y el contrato HTTP (S5/S6). El cableado de UI en static/ pertenece
a la fase B (otro hito) y aquí está prohibido por brief (C2/C3).
"""

from __future__ import annotations

import typing

import pytest

from herdr_brain import themes
from herdr_brain.themes import (
    MAX_FILE_BYTES,
    ThemeValidationError,
    contrast_ratio,
    contrast_report,
    relative_luminance,
    validate_theme,
)

# ── S2: matemática WCAG 2.1 (luminancia relativa exacta) ────────────────────


class TestWcagMath:
    def test_luminance_extremes(self):
        assert relative_luminance("#ffffff") == pytest.approx(1.0)
        assert relative_luminance("#000000") == pytest.approx(0.0, abs=1e-12)

    def test_luminance_mid_gray_is_the_reference_value(self):
        # WCAG reference: #808080 has relative luminance ~0.2158.
        assert relative_luminance("#808080") == pytest.approx(0.2158, abs=5e-4)

    def test_contrast_is_symmetric_and_order_independent(self):
        assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)
        assert contrast_ratio("#ffffff", "#000000") == pytest.approx(21.0)

    def test_the_classic_near_miss_fails_45(self):
        # #777777 on white sits just below the 4.5:1 line — the validator
        # must see it too (4.478, not a rounded 4.5).
        ratio = contrast_ratio("#777777", "#ffffff")
        assert ratio == pytest.approx(4.478, abs=2e-3)
        assert ratio < 4.5

    def test_real_css_tokens_land_where_expected(self):
        # Real tokens from static/index.html (light + dark), pinned so a
        # formula regression cannot silently move the gate.
        assert contrast_ratio("#12855c", "#ffffff") == pytest.approx(4.630, abs=2e-3)
        assert contrast_ratio("#17202b", "#f4f6f9") == pytest.approx(15.178, abs=2e-3)
        assert contrast_ratio("#808080", "#1b1d22") == pytest.approx(4.269, abs=2e-3)
        assert contrast_ratio("#7fd88f", "#141414") == pytest.approx(10.634, abs=2e-3)


# ── S1: esquema v1 — validación total y pura ────────────────────────────────


def valid_tokens() -> dict:
    """The 9 contrast-validated tokens, sun-palette values (all pass)."""
    return {
        "bg": "#ffffff",
        "panel": "#ffffff",
        "chip-bg": "#f0f0f0",
        "text": "#000000",
        "muted": "#262626",
        "accent": "#006400",
        "danger": "#b91c1c",
        "warn": "#7a4d00",
        "blue": "#0b3d91",
    }


def make_doc(**overrides) -> dict:
    doc = {
        "schema": 1,
        "id": "mi-tema",
        "name": "Mi tema",
        "description": "tema de prueba",
        "tokens": valid_tokens(),
    }
    doc.update(overrides)
    return doc


class TestValidateSchema:
    def test_a_valid_doc_normalizes_and_survives(self):
        doc = make_doc(tokens={**valid_tokens(), "bg": "#FFF"})
        clean = validate_theme(doc)
        assert clean["schema"] == 1
        assert clean["id"] == "mi-tema"
        assert clean["name"] == "Mi tema"
        assert clean["tokens"]["bg"] == "#ffffff"  # #FFF expanded + lowered

    def test_schema_must_be_exactly_one(self):
        for bad in (2, "1", None):
            with pytest.raises(ThemeValidationError):
                validate_theme(make_doc(schema=bad))
        with pytest.raises(ThemeValidationError):
            doc = make_doc()
            del doc["schema"]
            validate_theme(doc)

    def test_unknown_top_level_keys_are_rejected(self):
        with pytest.raises(ThemeValidationError):
            validate_theme(make_doc(fonts={"mono": "x"}))

    def test_slug_rules_pin_the_id(self):
        assert validate_theme(make_doc(id="a" * 48))["id"] == "a" * 48
        for bad in ("../evil", "", "-lead", "Mi-Tema", "a" * 49, "a_b", "a b"):
            with pytest.raises(ThemeValidationError):
                validate_theme(make_doc(id=bad))

    def test_unknown_tokens_are_rejected(self):
        tokens = {**valid_tokens(), "sparkle": "#123456"}
        with pytest.raises(ThemeValidationError):
            validate_theme(make_doc(tokens=tokens))

    def test_required_tokens_must_all_be_present(self):
        tokens = valid_tokens()
        del tokens["muted"]
        with pytest.raises(ThemeValidationError):
            validate_theme(make_doc(tokens=tokens))

    def test_color_formats(self):
        ok = {**valid_tokens(), "line": "#ABC"}
        assert validate_theme(make_doc(tokens=ok))["tokens"]["line"] == "#aabbcc"
        for bad in ("not-a-color", "#12345", "#12345678", "#", 123, None, ""):
            with pytest.raises(ThemeValidationError):
                validate_theme(make_doc(tokens={**valid_tokens(), "line": bad}))

    def test_rgba_is_confined_to_the_wash_tokens(self):
        ok = {**valid_tokens(), "user-wash": "rgba(18, 133, 92, 0.15)"}
        clean = validate_theme(make_doc(tokens=ok))
        assert clean["tokens"]["user-wash"] == "rgba(18,133,92,0.15)"
        with pytest.raises(ThemeValidationError):
            validate_theme(make_doc(tokens={**valid_tokens(), "bg": "rgba(1,2,3,0.5)"}))
        with pytest.raises(ThemeValidationError):
            validate_theme(
                make_doc(tokens={**valid_tokens(), "user-line": "rgba(300,0,0,1)"})
            )
        with pytest.raises(ThemeValidationError):
            validate_theme(
                make_doc(tokens={**valid_tokens(), "user-line": "rgba(0,0,0,2)"})
            )

    def test_field_limits(self):
        with pytest.raises(ThemeValidationError):
            validate_theme(make_doc(name="x" * 65))
        with pytest.raises(ThemeValidationError):
            validate_theme(make_doc(name=""))
        with pytest.raises(ThemeValidationError):
            validate_theme(make_doc(description="x" * 201))
        assert validate_theme(make_doc(description=None))["description"] == ""

    def test_imports_cannot_claim_to_be_built_in(self):
        with pytest.raises(ThemeValidationError):
            validate_theme(make_doc(built_in=True))


# ── S2: matriz de contraste — rechazo con motivo, sin aplicar nada ──────────


class TestContrastMatrix:
    def test_report_covers_exactly_the_twelve_pairs(self):
        report = contrast_report(make_doc())
        pairs = {(entry["fg"], entry["bg"]) for entry in report}
        assert pairs == {
            ("text", "bg"), ("text", "panel"), ("text", "chip-bg"),
            ("muted", "panel"), ("accent", "panel"), ("danger", "panel"),
            ("warn", "panel"), ("blue", "panel"),
            ("accent", "bg"), ("muted", "bg"), ("muted", "chip-bg"),
            ("danger", "bg"),
        }
        minimums = {(e["fg"], e["bg"]): e["minimum"] for e in report}
        for pair in [("text", "bg"), ("text", "panel"), ("text", "chip-bg"),
                     ("muted", "panel"), ("accent", "panel"), ("danger", "panel"),
                     ("warn", "panel"), ("blue", "panel")]:
            assert minimums[pair] == 4.5
        for pair in [("accent", "bg"), ("muted", "bg"), ("muted", "chip-bg"),
                     ("danger", "bg")]:
            assert minimums[pair] == 3.0

    def test_a_text_pair_below_45_is_refused_with_pair_and_ratio(self):
        tokens = {**valid_tokens(), "text": "#777777", "bg": "#ffffff"}
        with pytest.raises(ThemeValidationError) as excinfo:
            validate_theme(make_doc(id="contraste-bajo", tokens=tokens))
        reason = str(excinfo.value)
        assert "text/bg" in reason
        assert "4.48" in reason

    def test_a_control_pair_below_30_is_refused(self):
        tokens = {**valid_tokens(), "accent": "#b0b0b0"}
        with pytest.raises(ThemeValidationError):
            validate_theme(make_doc(tokens=tokens))

    def test_all_pairs_must_pass_not_just_one(self):
        # One failing pair among otherwise perfect tokens still refuses.
        tokens = {**valid_tokens(), "warn": "#c0c0c0"}
        with pytest.raises(ThemeValidationError):
            validate_theme(make_doc(tokens=tokens))

    def test_field_caps_bound_the_serialized_document(self):
        # Per-field limits keep every VALID document far below the file
        # cap; the raw-body bound lives at the route (413), not here.
        doc = make_doc(description="x" * 200)
        assert len(themes.serialize_theme(doc)) < MAX_FILE_BYTES


# ── S3/S4: presets built-in y almacén seguro ────────────────────────────────

import json
import logging
import os
import stat

from herdr_brain.themes import ThemeStore, built_in


class TestBuiltInPresets:
    EXPECTED_IDS: typing.ClassVar[frozenset[str]] = frozenset(
        {"claro", "oscuro", "alto-contraste-sol", "alto-contraste-noche"}
    )

    def test_the_four_approved_presets_exist(self):
        assert {doc["id"] for doc in themes.BUILT_INS} == self.EXPECTED_IDS

    def test_every_preset_passes_its_own_contrast_matrix(self):
        for doc in themes.BUILT_INS:
            clean = validate_theme(doc)  # raises on any failure
            assert all(entry["ok"] for entry in contrast_report(clean)), doc["id"]

    def test_sun_preset_keeps_the_promised_margins(self):
        sol = built_in("alto-contraste-sol")
        assert sol is not None
        for entry in contrast_report(sol):
            pair = (entry["fg"], entry["bg"])
            if pair in (("text", "bg"), ("text", "panel"), ("text", "chip-bg"), ("muted", "panel")):
                assert entry["ratio"] >= 7.0, pair
            elif pair in (("accent", "panel"), ("danger", "panel"), ("warn", "panel"), ("blue", "panel")):
                assert entry["ratio"] >= 5.0, pair

    def test_night_preset_is_equally_strong(self):
        noche = built_in("alto-contraste-noche")
        assert noche is not None
        assert min(e["ratio"] for e in contrast_report(noche)) >= 7.0

    def test_light_and_dark_presets_mirror_the_stylesheet(self):
        claro = built_in("claro")["tokens"]
        oscuro = built_in("oscuro")["tokens"]
        assert claro["bg"] == "#f4f6f9" and claro["text"] == "#17202b"
        assert claro["accent"] == "#12855c" and claro["muted"] == "#5b6572"
        assert oscuro["bg"] == "#1b1d22" and oscuro["text"] == "#eeeeee"
        assert oscuro["accent"] == "#7fd88f" and oscuro["panel"] == "#141414"


def user_doc(theme_id="mi-tema", *, tokens=None, **fields) -> dict:
    doc = {
        "schema": 1,
        "id": theme_id,
        "name": "Tema usuario",
        "description": "",
        "tokens": {**valid_tokens(), **(tokens or {})},
    }
    doc.update(fields)
    return doc


class TestThemeStore:
    def make_store(self, tmp_path) -> ThemeStore:
        return ThemeStore(config_dir=tmp_path)

    def test_empty_catalog_still_serves_the_builtins(self, tmp_path):
        store = self.make_store(tmp_path)
        catalog = store.list_catalog()
        assert {t["id"] for t in catalog["themes"]} >= set(TestBuiltInPresets.EXPECTED_IDS)
        assert all(t["built_in"] is True for t in catalog["themes"])
        assert catalog["skipped"] == []

    def test_import_writes_a_normalized_0600_file(self, tmp_path):
        store = self.make_store(tmp_path)
        stored = store.import_theme(user_doc())
        path = store.themes_dir / "mi-tema.json"
        assert path.read_text(encoding="utf-8") == themes.serialize_theme(stored)
        if os.name == "posix":
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
            assert stat.S_IMODE(store.themes_dir.stat().st_mode) == 0o700
        entry = next(t for t in store.list_catalog()["themes"] if t["id"] == "mi-tema")
        assert entry["built_in"] is False

    def test_reimport_upserts(self, tmp_path):
        store = self.make_store(tmp_path)
        store.import_theme(user_doc())
        store.import_theme(user_doc(name="Otro nombre"))
        mine = [t for t in store.list_catalog()["themes"] if t["id"] == "mi-tema"]
        assert len(mine) == 1 and mine[0]["name"] == "Otro nombre"

    def test_import_of_an_invalid_document_writes_nothing(self, tmp_path):
        store = self.make_store(tmp_path)
        with pytest.raises(ThemeValidationError):
            store.import_theme(user_doc(tokens={"text": "#777777"}))
        assert not (store.themes_dir / "mi-tema.json").exists()

    def test_import_cannot_shadow_a_builtin_id(self, tmp_path):
        store = self.make_store(tmp_path)
        with pytest.raises(ThemeValidationError):
            store.import_theme(user_doc("claro"))
        assert not (store.themes_dir / "claro.json").exists()
        assert store.get_theme("claro") == built_in("claro")

    def test_delete_only_removes_user_themes(self, tmp_path):
        store = self.make_store(tmp_path)
        store.import_theme(user_doc())
        assert store.delete_theme("mi-tema") is True
        assert store.get_theme("mi-tema") is None
        assert store.delete_theme("mi-tema") is False
        assert store.delete_theme("claro") is False
        assert store.get_theme("claro") is not None
        assert store.delete_theme("../evil") is False

    def test_get_refuses_non_slug_ids_without_touching_the_fs(self, tmp_path):
        store = self.make_store(tmp_path)
        assert store.get_theme("../escape") is None
        assert store.get_theme("") is None
        assert store.get_theme("nope") is None

    def test_corrupt_user_files_are_skipped_with_a_reason(self, tmp_path, caplog):
        store = self.make_store(tmp_path)
        store.import_theme(user_doc())
        (store.themes_dir / "roto.json").write_text("not json{", encoding="utf-8")
        invalid = user_doc("sin-contraste", tokens={"text": "#777777"})
        (store.themes_dir / "sin-contraste.json").write_text(
            json.dumps(invalid), encoding="utf-8"
        )
        (store.themes_dir / "Weird_Name.json").write_text("{}", encoding="utf-8")
        with caplog.at_level(logging.WARNING):
            catalog = store.list_catalog()
        ids = {t["id"] for t in catalog["themes"]}
        assert "mi-tema" in ids
        assert "roto" not in ids and "sin-contraste" not in ids
        assert "Weird_Name" not in ids
        skipped = {s["id"]: s["reason"] for s in catalog["skipped"]}
        assert set(skipped) == {"roto", "sin-contraste"}
        assert all(skipped.values())


# ── S5/S6: contrato HTTP /themes (fase A: solo backend) ─────────────────────

from fastapi.testclient import TestClient

from herdr_brain.config import Settings
from herdr_brain.server import create_app
from tests.conftest import SETTINGS_KWARGS


def make_client(tmp_path) -> TestClient:
    settings = Settings(
        **{
            **SETTINGS_KWARGS,
            "state_dir": str(tmp_path / "state"),
            "audio_dir": str(tmp_path / "audio"),
        }
    )
    store = ThemeStore(config_dir=tmp_path / "config")
    return TestClient(create_app(settings=settings, themes=store))


def audit_text(tmp_path) -> str:
    path = tmp_path / "state" / "audit.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


class TestThemesRoutes:
    def test_catalog_lists_the_builtins(self, tmp_path):
        response = make_client(tmp_path).get("/themes")
        assert response.status_code == 200
        payload = response.json()
        ids = {t["id"] for t in payload["themes"]}
        assert set(TestBuiltInPresets.EXPECTED_IDS) <= ids
        assert all("tokens" in t for t in payload["themes"])
        assert payload["skipped"] == []

    def test_theme_detail_serves_the_full_document(self, tmp_path):
        client = make_client(tmp_path)
        detail = client.get("/themes/claro")
        assert detail.status_code == 200
        assert detail.json()["tokens"]["bg"] == "#f4f6f9"
        assert client.get("/themes/nope").status_code == 404

    def test_import_writes_audits_and_answers_normalized(self, tmp_path):
        client = make_client(tmp_path)
        response = client.post("/themes/import", json=user_doc("sol-propio"))
        assert response.status_code == 200
        assert response.json()["id"] == "sol-propio"
        assert response.json()["built_in"] is False
        assert (tmp_path / "config" / "themes" / "sol-propio.json").exists()
        assert '"themes.import"' in audit_text(tmp_path)

    def test_import_of_low_contrast_is_422_and_writes_nothing(self, tmp_path):
        client = make_client(tmp_path)
        response = client.post(
            "/themes/import", json=user_doc(tokens={"text": "#777777"})
        )
        assert response.status_code == 422
        assert "text/bg" in response.json()["detail"]
        assert not (tmp_path / "config" / "themes").exists() or not list(
            (tmp_path / "config" / "themes").glob("*.json")
        )
        assert '"themes.import"' not in audit_text(tmp_path)

    def test_import_of_nonsense_json_is_422(self, tmp_path):
        client = make_client(tmp_path)
        assert client.post("/themes/import", json={"hola": 1}).status_code == 422
        assert client.post("/themes/import", json="hola").status_code == 422

    def test_import_cannot_shadow_a_builtin(self, tmp_path):
        client = make_client(tmp_path)
        response = client.post("/themes/import", json=user_doc("claro"))
        assert response.status_code == 422
        assert not (tmp_path / "config" / "themes" / "claro.json").exists()

    def test_import_oversize_is_413(self, tmp_path):
        client = make_client(tmp_path)
        huge = user_doc(description="x" * (themes.MAX_FILE_BYTES + 10))
        assert client.post("/themes/import", json=huge).status_code == 413

    def test_delete_removes_user_themes_and_audits(self, tmp_path):
        client = make_client(tmp_path)
        client.post("/themes/import", json=user_doc("efimero"))
        response = client.delete("/themes/efimero")
        assert response.status_code == 200
        assert response.json() == {"ok": True}
        assert not (tmp_path / "config" / "themes" / "efimero.json").exists()
        assert '"themes.delete"' in audit_text(tmp_path)

    def test_delete_refuses_builtins_and_unknown(self, tmp_path):
        client = make_client(tmp_path)
        assert client.delete("/themes/claro").status_code == 422
        assert client.delete("/themes/nope").status_code == 404
        assert client.get("/themes/claro").status_code == 200

    def test_default_wiring_builds_a_config_dir_store(self, tmp_path):
        settings = Settings(
            **{
                **SETTINGS_KWARGS,
                "state_dir": str(tmp_path / "state"),
                "audio_dir": str(tmp_path / "audio"),
            }
        )
        app = create_app(settings=settings)
        assert isinstance(app.state.themes, ThemeStore)
        assert app.state.themes.themes_dir.name == "themes"


# ── ronda odd-verifier: anclas \Z, ASCII, seguridad de rutas, streaming ─────


class TestRegexAnchorsRound2:
    """B1: '$' admits a trailing newline in Python; '\\Z' does not."""

    @pytest.mark.parametrize("color", ["#fff\n", "#ffffff\n", "#ABC\n"])
    def test_trailing_newline_colors_are_rejected(self, color):
        with pytest.raises(ThemeValidationError):
            validate_theme(make_doc(tokens={**valid_tokens(), "line": color}))

    def test_a_trailing_newline_cannot_reach_a_required_token(self):
        with pytest.raises(ThemeValidationError):
            validate_theme(make_doc(tokens={**valid_tokens(), "bg": "#ffffff\n"}))

    @pytest.mark.parametrize("bad_id", ["evil\n", "mi-tema\n", "a\nb"])
    def test_trailing_newline_ids_are_rejected(self, bad_id):
        with pytest.raises(ThemeValidationError):
            validate_theme(make_doc(id=bad_id))


class TestRgbaAsciiRound2:
    """B2: '\\d' matches Unicode digits without re.ASCII."""

    def test_unicode_digits_in_alpha_are_rejected(self):
        with pytest.raises(ThemeValidationError):
            validate_theme(
                make_doc(tokens={**valid_tokens(), "user-line": "rgba(0,0,0,0.٥)"})
            )

    def test_alpha_1_point_0_is_valid_css_and_accepted(self):
        clean = validate_theme(
            make_doc(tokens={**valid_tokens(), "user-wash": "rgba(1,2,3,1.0)"})
        )
        assert clean["tokens"]["user-wash"] == "rgba(1,2,3,1.0)"

    def test_alpha_above_one_is_rejected(self):
        with pytest.raises(ThemeValidationError):
            validate_theme(
                make_doc(tokens={**valid_tokens(), "user-wash": "rgba(1,2,3,1.5)"})
            )


class TestStorePathSafetyRound2:
    """B3: the non-slug sentinel must not be a reachable filename."""

    def test_get_with_non_slug_never_returns_a_file(self, tmp_path):
        store = TestThemeStore().make_store(tmp_path)
        store.themes_dir.mkdir(parents=True, exist_ok=True)
        sentinel = "definitely-not-a-slug"
        (store.themes_dir / f"{sentinel}.json").write_text(
            themes.serialize_theme(validate_theme(user_doc(sentinel))),
            encoding="utf-8",
        )
        assert store.get_theme("../escape") is None
        assert store.get_theme(sentinel) is not None  # reachable by its own id

    def test_get_never_returns_a_file_whose_id_disagrees(self, tmp_path):
        store = TestThemeStore().make_store(tmp_path)
        store.import_theme(user_doc())
        path = store.themes_dir / "mi-tema.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["id"] = "otro-tema"
        path.write_text(themes.serialize_theme(doc), encoding="utf-8")
        assert store.get_theme("mi-tema") is None
        catalog = store.list_catalog()
        assert "mi-tema" not in {t["id"] for t in catalog["themes"]}
        assert any(s["id"] == "mi-tema" for s in catalog["skipped"])


class TestBuiltinShadowRound2:
    """B4: a manually dropped '<builtin-id>.json' must not duplicate."""

    def test_manual_builtin_file_is_skipped_as_reserved(self, tmp_path):
        store = TestThemeStore().make_store(tmp_path)
        store.themes_dir.mkdir(parents=True, exist_ok=True)
        (store.themes_dir / "claro.json").write_text(
            themes.serialize_theme(built_in("claro")), encoding="utf-8"
        )
        catalog = store.list_catalog()
        claros = [t for t in catalog["themes"] if t["id"] == "claro"]
        assert len(claros) == 1 and claros[0]["built_in"] is True
        assert any(s["id"] == "claro" for s in catalog["skipped"])


class TestStoreHardeningRound2:
    """B6: symlinks, size bounds, control chars, copy isolation."""

    def test_symlinked_theme_files_are_skipped(self, tmp_path):
        store = TestThemeStore().make_store(tmp_path)
        store.themes_dir.mkdir(parents=True, exist_ok=True)
        outside = tmp_path / "fuera.json"
        outside.write_text(
            themes.serialize_theme(validate_theme(user_doc("fuera"))),
            encoding="utf-8",
        )
        (store.themes_dir / "symlink.json").symlink_to(outside)
        catalog = store.list_catalog()
        assert "fuera" not in {t["id"] for t in catalog["themes"]}
        assert any(s["id"] == "symlink" for s in catalog["skipped"])

    def test_oversized_user_files_are_skipped_before_parsing(self, tmp_path):
        store = TestThemeStore().make_store(tmp_path)
        store.import_theme(user_doc())
        path = store.themes_dir / "mi-tema.json"
        path.write_text(
            path.read_text(encoding="utf-8") + " " * (themes.MAX_FILE_BYTES + 1),
            encoding="utf-8",
        )
        catalog = store.list_catalog()
        assert "mi-tema" not in {t["id"] for t in catalog["themes"]}
        assert any(s["id"] == "mi-tema" for s in catalog["skipped"])

    def test_control_characters_in_name_are_rejected(self):
        for bad in ("malicious\nname", "tab\tname"):
            with pytest.raises(ThemeValidationError):
                validate_theme(make_doc(name=bad))

    def test_catalog_entries_do_not_share_builtin_token_dicts(self, tmp_path):
        store = TestThemeStore().make_store(tmp_path)
        entry = next(
            t for t in store.list_catalog()["themes"] if t["id"] == "claro"
        )
        entry["tokens"]["bg"] = "#ff0000"
        assert built_in("claro")["tokens"]["bg"] == "#f4f6f9"


class TestImportSizeGuardRound2:
    """B5: the cap must bound memory, not just the parsed object."""

    def test_spoofed_small_content_length_with_big_body_is_413(self, tmp_path):
        client = make_client(tmp_path)
        body = json.dumps(user_doc()).encode("utf-8")
        body += b" " * (themes.MAX_FILE_BYTES + 10)
        response = client.post(
            "/themes/import",
            content=body,
            headers={"content-type": "application/json", "content-length": "10"},
        )
        assert response.status_code == 413

    def test_chunked_big_body_is_413(self, tmp_path):
        client = make_client(tmp_path)
        big = json.dumps(user_doc()).encode("utf-8")
        big += b" " * (themes.MAX_FILE_BYTES + 10)
        response = client.post(
            "/themes/import",
            content=iter([big[:1000], big[1000:]]),
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 413


class TestThemesPairingE2ERound2:
    """B6 E2E: under enforced pairing the reads are gated too, and the
    writes attribute the paired device in the audit log."""

    def make_paired(self, tmp_path):
        from herdr_brain import pairing

        state = tmp_path / "state"
        settings = Settings(
            **{
                **SETTINGS_KWARGS,
                "state_dir": str(state),
                "audio_dir": str(tmp_path / "audio"),
                "allow_any_host": False,
            }
        )
        store = ThemeStore(config_dir=tmp_path / "config")
        client = TestClient(
            create_app(settings=settings, themes=store),
            base_url="http://127.0.0.1",
        )
        minted = pairing.PairingStore(state).mint()
        response = client.post("/pair", json={"code": minted["code"], "label": "phone"})
        assert response.status_code == 200, response.text
        return client, response.json()["token"], state

    def test_gate_blocks_unbearer_and_attributes_audits(self, tmp_path):
        client, token, state = self.make_paired(tmp_path)
        assert client.get("/themes").status_code == 403
        assert client.get("/themes/claro").status_code == 403
        assert client.post("/themes/import", json=user_doc()).status_code == 403
        assert client.delete("/themes/claro").status_code == 403
        auth = {"Authorization": f"Bearer {token}"}
        assert client.get("/themes", headers=auth).status_code == 200
        imported = client.post(
            "/themes/import", json=user_doc("con-par"), headers=auth
        )
        assert imported.status_code == 200
        assert client.delete("/themes/con-par", headers=auth).status_code == 200
        lines = [
            json.loads(line)
            for line in (state / "audit.log").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        actions = {entry["action"]: entry for entry in lines}
        assert actions["themes.import"]["device"] == "phone"
        assert actions["themes.import"]["detail"] == {"id": "con-par"}
        assert actions["themes.delete"]["device"] == "phone"
