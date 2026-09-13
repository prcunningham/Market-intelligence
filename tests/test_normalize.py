import textwrap

import pytest

from market_intel.normalize import CompanyNormalizer, clean_name


@pytest.fixture
def normalizer(tmp_path):
    aliases = tmp_path / "company_aliases.yaml"
    aliases.write_text(textwrap.dedent("""
        companies:
          - canonical: Philips
            aliases:
              - Respironics
              - Respironics Inc
              - Philips RS North America
          - canonical: Medtronic
            aliases:
              - Restore Medical
              - Pi Medical
    """))
    categories = tmp_path / "company_categories.yaml"
    categories.write_text(textwrap.dedent("""
        categories:
          Philips: Strategic/Incumbent Medtech
          Medtronic: Strategic/Incumbent Medtech
    """))
    return CompanyNormalizer(aliases_path=aliases, categories_path=categories)


def test_clean_name_strips_legal_suffixes_and_punctuation():
    assert clean_name("Respironics, Inc.") == "respironics"
    assert clean_name("RESPIRONICS INCORPORATED") == "respironics"
    assert clean_name("Acme Medical, LLC") == "acme medical"


def test_alias_resolves_to_canonical(normalizer):
    assert normalizer.normalize("Respironics Inc") == "Philips"
    assert normalizer.normalize("RESPIRONICS, INC.") == "Philips"
    assert normalizer.normalize("Philips RS North America") == "Philips"
    assert normalizer.normalize("Restore Medical") == "Medtronic"
    assert normalizer.normalize("Pi Medical") == "Medtronic"


def test_canonical_name_normalizes_to_itself(normalizer):
    assert normalizer.normalize("Philips") == "Philips"


def test_unmapped_company_falls_back_to_cleaned_title_case(normalizer):
    result = normalizer.normalize("Acme Sleep Devices, LLC")
    assert result == "Acme Sleep Devices"
    assert "Acme Sleep Devices, LLC" in normalizer.unmapped_companies()


def test_category_lookup(normalizer):
    assert normalizer.category("Philips") == "Strategic/Incumbent Medtech"
    assert normalizer.category("Some Unknown Co") == "Unclassified"
    assert normalizer.category(None) == "Unclassified"


def test_none_and_empty_input(normalizer):
    assert normalizer.normalize(None) is None
    assert normalizer.normalize("") is None
    assert normalizer.normalize("   ") is None
