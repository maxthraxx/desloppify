from __future__ import annotations

from desloppify.languages.cxx import CxxConfig


def test_cxx_uses_full_plugin_config():
    cfg = CxxConfig()
    assert cfg.name == "cxx"
    assert callable(cfg.build_dep_graph)
    assert callable(cfg.extract_functions)
    assert cfg.review_guidance
