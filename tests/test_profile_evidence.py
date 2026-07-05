from __future__ import annotations

import yaml


def _profile() -> dict:
    return {
        "candidate": {
            "experience": [
                {
                    "company": "DocuSign",
                    "title": "Senior Product Manager",
                    "highlights": [
                        "Automated agreement type extraction across structured and unstructured agreement data.",
                        "Led GTM field enablement with Engineering and Executive stakeholders.",
                    ],
                }
            ],
            "education": [
                {
                    "institution": "UIUC",
                    "degree": "Master of Computer Science in Data Science",
                }
            ],
            "skills": ["data analytics", "Azure CLI"],
        }
    }


def test_derive_themes_uses_normalized_taxonomy():
    from metis.profile_evidence import derive_themes

    themes = derive_themes("Semantic modeling for structured and unstructured agreement data")

    assert "enterprise data" in themes
    assert "data modeling" in themes


def test_build_evidence_index_is_compact_and_hashes_source(tmp_path):
    from metis.profile_evidence import build_evidence_index

    source = tmp_path / "profile.yaml"
    source.write_text(yaml.dump(_profile()), encoding="utf-8")

    index = build_evidence_index(_profile(), source_path=source)

    assert "candidate" not in index
    assert index["source"]["sha256"]
    assert len(index["evidence_items"]) == 3
    assert index["evidence_items"][0]["anchors"]["profile_path"] == "candidate.experience[0].highlights[0]"
    assert index["skill_index"][0]["evidence_role"] == "retrieval_hint"


def test_build_evidence_index_ids_are_stable(tmp_path):
    from metis.profile_evidence import build_evidence_index

    source = tmp_path / "profile.yaml"
    source.write_text(yaml.dump(_profile()), encoding="utf-8")

    first = build_evidence_index(_profile(), source_path=source)
    second = build_evidence_index(_profile(), source_path=source)

    assert first["evidence_items"][0]["id"] == second["evidence_items"][0]["id"]


def test_evidence_index_stale_when_source_hash_changes(tmp_path, monkeypatch):
    import metis.profile_evidence as profile_evidence

    source = tmp_path / "profile.yaml"
    index_path = tmp_path / "profile.evidence.index.yaml"
    source.write_text(yaml.dump(_profile()), encoding="utf-8")
    monkeypatch.setattr(profile_evidence, "YAML_PATH", source)
    monkeypatch.setattr(profile_evidence, "load_profile_yaml", lambda: _profile())

    profile_evidence.write_evidence_index(str(index_path), allow_unsafe_path=True)
    assert profile_evidence.evidence_index_is_stale(index_path) is False

    changed = _profile()
    changed["candidate"]["experience"][0]["highlights"].append("New developer platform evidence.")
    source.write_text(yaml.dump(changed), encoding="utf-8")

    assert profile_evidence.evidence_index_is_stale(index_path) is True


def test_write_evidence_index_rejects_output_outside_data_dir(tmp_path, monkeypatch):
    import pytest
    import metis.profile_evidence as profile_evidence

    source = tmp_path / "profile.yaml"
    source.write_text(yaml.dump(_profile()), encoding="utf-8")
    monkeypatch.setattr(profile_evidence, "YAML_PATH", source)
    monkeypatch.setattr(profile_evidence, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(profile_evidence, "load_profile_yaml", lambda: _profile())

    with pytest.raises(ValueError):
        profile_evidence.write_evidence_index(str(tmp_path / "outside.yaml"))


def test_tailor_converts_profile_index_to_evidence_units(tmp_path):
    from metis.profile_evidence import build_evidence_index
    from metis.tailor import build_evidence_units_from_profile_index

    source = tmp_path / "profile.yaml"
    source.write_text(yaml.dump(_profile()), encoding="utf-8")
    index = build_evidence_index(_profile(), source_path=source)

    units = build_evidence_units_from_profile_index(index)

    assert units
    assert units[0].id.startswith("exp_docusign")
    assert "Automated agreement type extraction" in units[0].text
    assert any(unit.id.startswith("skill_") for unit in units)
