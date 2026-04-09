from pathlib import Path

from supervisor import prime_runner


def test_extract_system_prompt_from_spec_file() -> None:
    spec_path = Path(__file__).resolve().parents[1] / "spec" / "CAMBRIAN-SPEC-005.md"
    spec_text = spec_path.read_text()

    prompt = prime_runner.extract_system_prompt(spec_text)

    assert prompt is not None
    assert "CORRECT PATTERNS" in prompt
    assert 'Use "python src/prime.py" in entry.start' in prompt


def test_resolve_system_prompt_falls_back_when_block_missing() -> None:
    resolved = prime_runner.resolve_system_prompt("# no prompt block here")
    assert resolved == prime_runner.DEFAULT_SYSTEM_PROMPT
