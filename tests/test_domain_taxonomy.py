from __future__ import annotations


def test_domain_taxonomy_loads_packaged_yaml():
    from metis.domain_taxonomy import load_domain_taxonomy

    taxonomy = load_domain_taxonomy()

    assert taxonomy["version"] == 1
    assert "cloud_infrastructure" in taxonomy["domains"]
    assert "Kubernetes operations" in taxonomy["domains"]["cloud_infrastructure"]["hard_barriers"]


def test_render_domain_taxonomy_includes_hard_barriers():
    from metis.domain_taxonomy import render_domain_taxonomy

    rendered = render_domain_taxonomy({
        "rules": ["Do not overgeneralize."],
        "domains": {
            "cloud_infrastructure": {
                "native_signals": ["cloud platform services"],
                "adjacent_signals": ["developer platform"],
                "hard_barriers": ["Kubernetes operations"],
            }
        },
    })

    assert "Do not overgeneralize." in rendered
    assert "cloud infrastructure" in rendered
    assert "Kubernetes operations" in rendered
