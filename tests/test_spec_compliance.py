"""Spec compliance integration tests.

These tests parse the active specs (CAMBRIAN-SPEC-005 and BOOTSTRAP-SPEC-002)
and verify internal consistency, cross-spec agreement, and that code implements
what the specs declare. This prevents the class of bugs found in the pre-M2
quality review: field naming inconsistencies, version mismatches, contradictory
schema constraints, and missing fields.

Run with: uv run pytest tests/test_spec_compliance.py -v
"""

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SPEC_DIR = PROJECT_ROOT / "spec"
CAMBRIAN_SPEC = SPEC_DIR / "CAMBRIAN-SPEC-005.md"
BOOTSTRAP_SPEC = SPEC_DIR / "BOOTSTRAP-SPEC-002.md"
DOCKERFILE = PROJECT_ROOT / "docker" / "Dockerfile"
DOCKERIGNORE = PROJECT_ROOT / ".dockerignore"


def _read_spec(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Version consistency
# ---------------------------------------------------------------------------


class TestSpecVersionConsistency:
    """Prevent version mismatches between frontmatter and footer YAML blocks."""

    @staticmethod
    def _extract_frontmatter_version(text: str) -> str | None:
        m = re.search(r"^version:\s*(\S+)", text, re.MULTILINE)
        return m.group(1).strip("\"'") if m else None

    @staticmethod
    def _extract_footer_version(text: str) -> str | None:
        # Footer YAML block is the last ```yaml block in the file
        footer_blocks = re.findall(r"```yaml\s*\n(.*?)```", text, re.DOTALL)
        if not footer_blocks:
            return None
        last_block = footer_blocks[-1]
        m = re.search(r'^version:\s*"?(\S+?)"?\s*$', last_block, re.MULTILINE)
        return m.group(1) if m else None

    def test_cambrian_spec_versions_match(self) -> None:
        text = _read_spec(CAMBRIAN_SPEC)
        fm = self._extract_frontmatter_version(text)
        ft = self._extract_footer_version(text)
        assert fm is not None, "Missing frontmatter version"
        assert ft is not None, "Missing footer version"
        assert fm == ft, f"CAMBRIAN-SPEC-005 frontmatter ({fm}) != footer ({ft})"

    def test_bootstrap_spec_versions_match(self) -> None:
        text = _read_spec(BOOTSTRAP_SPEC)
        fm = self._extract_frontmatter_version(text)
        ft = self._extract_footer_version(text)
        assert fm is not None, "Missing frontmatter version"
        assert ft is not None, "Missing footer version"
        assert fm == ft, f"BOOTSTRAP-SPEC-002 frontmatter ({fm}) != footer ({ft})"

    def test_versions_are_valid_semver(self) -> None:
        semver_re = re.compile(r"^\d+\.\d+\.\d+$")
        for spec_path in (CAMBRIAN_SPEC, BOOTSTRAP_SPEC):
            text = _read_spec(spec_path)
            v = self._extract_frontmatter_version(text)
            assert v is not None
            assert semver_re.match(v), f"{spec_path.name} version {v!r} is not valid semver"


# ---------------------------------------------------------------------------
# 2. Field naming convention: all wire-format fields use kebab-case
# ---------------------------------------------------------------------------


class TestFieldNamingConvention:
    """Verify that all JSON wire-format fields in specs use kebab-case (no underscores).

    This catches the artifact_ref / created_at class of bugs.
    """

    @staticmethod
    def _extract_table_field_names(text: str) -> list[tuple[int, str]]:
        """Extract field names from markdown pipe-tables (| `field-name` | ...)."""
        results = []
        for i, line in enumerate(text.splitlines(), 1):
            m = re.match(r"\|\s*`([^`]+)`\s*\|", line)
            if m:
                results.append((i, m.group(1)))
        return results

    @staticmethod
    def _extract_json_keys_from_code_blocks(text: str) -> list[tuple[int, str]]:
        """Extract JSON keys from fenced code blocks containing JSON."""
        results = []
        in_json_block = False
        for i, line in enumerate(text.splitlines(), 1):
            if re.match(r"```json", line):
                in_json_block = True
                continue
            if line.strip() == "```":
                in_json_block = False
                continue
            if in_json_block:
                m = re.search(r'"([^"]+)"\s*:', line)
                if m:
                    results.append((i, m.group(1)))
        return results

    # Allowed underscore names in spec tables. These are NOT wire-format fields
    # that flow between components — they are env vars, internal report fields,
    # or nested diagnostic keys.
    _ALLOWED_UNDERSCORES = {
        # Environment variables (SCREAMING_SNAKE_CASE by convention)
        "ANTHROPIC_API_KEY",
        "CAMBRIAN_MODEL",
        "CAMBRIAN_ESCALATION_MODEL",
        "CAMBRIAN_MAX_GENS",
        "CAMBRIAN_MAX_RETRIES",
        "CAMBRIAN_MAX_PARSE_RETRIES",
        "CAMBRIAN_SUPERVISOR_URL",
        "CAMBRIAN_SPEC_PATH",
        "CAMBRIAN_TOKEN_BUDGET",
        "CAMBRIAN_ARTIFACTS_ROOT",
        "DOCKER_HOST",
        "CAMBRIAN_CONTAINER_TIMEOUT",
        "CAMBRIAN_SUPERVISOR_PORT",
        "CAMBRIAN_DOCKER_IMAGE",
        # Viability report internal fields (not cross-component wire format)
        "failure_stage",
        "completed_at",
        "exit_code",
        "stdout_tail",
        "stderr_tail",
        "tests_run",
        "tests_passed",
        "duration_ms",
        # Fitness metrics (internal to test rig, snake_case by convention)
        "build_duration_ms",
        "test_duration_ms",
        "start_duration_ms",
        "health_duration_ms",
        "total_duration_ms",
        "test_count",
        "test_pass_rate",
        "source_files",
        "source_lines",
        "test_files",
        "test_lines",
        "dependency_count",
        "token_input",
        "token_output",
        "contract_pass_rate",
        "stages_completed",
        # Campaign-level aggregate metrics (internal analytics)
        "viability_rate",
        "fitness_mean",
        "fitness_trend",
        "failure_distribution",
        "stages_completed_distribution",
        "generation_count",
        # M2 mode gate
        "CAMBRIAN_MODE",
        # M2 campaign / mutation env vars
        "CAMBRIAN_CAMPAIGN_LENGTH",
        "CAMBRIAN_MINI_CAMPAIGN_N",
        "CAMBRIAN_CAMPAIGN_POLL_INTERVAL",
        "CAMBRIAN_MUTATION_MODEL",
        "CAMBRIAN_MUTATION_MAX_TOKENS",
        "CAMBRIAN_MUTATION_MAX_ATTEMPTS",
        "CAMBRIAN_BO_BUDGET",
        "CAMBRIAN_BO_INITIAL_POINTS",
        "CAMBRIAN_BO_DIM_RANGE",
        "CAMBRIAN_SCREEN_THRESHOLD",
        "CAMBRIAN_ADAPTIVE_EXPIRE_AFTER",
        "CAMBRIAN_ADAPTIVE_MAX_ACTIVE",
        "CAMBRIAN_ADAPTIVE_TESTS_PER_GENERATION",
        "CAMBRIAN_ADAPTIVE_MODEL",
        "CAMBRIAN_ADAPTIVE_MAX_TOKENS",
        # Verification layer fitness metrics (internal to test rig)
        "spec_vector_pass_rate",
        "examiner_pass_rate",
        "redteam_score",
        "redteam_violations",
        # Verification layer environment variables
        "CAMBRIAN_EXAMINER_MODEL",
        "CAMBRIAN_REDTEAM_MODEL",
        "CAMBRIAN_REDTEAM_THRESHOLD",
        # Container isolation (§2.12)
        "CAMBRIAN_OUTPUT_DIR",
        # Counterfactual baseline battery (§2.13)
        "CAMBRIAN_BASELINE_PATH",
        "baseline_contract_pass_rate",
        # Adaptive test storage schema (§2.14) — internal JSON file, not wire format
        "created_at",
        "failure_summary",
        "test_code",
        "campaigns_remaining",
        # Nested diagnostic/expect fields (dotted paths in table rows)
        "body_contains",
        "body_has_keys",
        "expect.body_contains",
        "expect.body_has_keys",
        "diagnostics.exit_code",
        "diagnostics.stdout_tail",
        "diagnostics.stderr_tail",
    }

    def test_cambrian_spec_table_fields_no_underscores(self) -> None:
        """GenerationRecord and manifest tables should use kebab-case."""
        text = _read_spec(CAMBRIAN_SPEC)
        fields = self._extract_table_field_names(text)
        violations = [
            (line, name)
            for line, name in fields
            if "_" in name and name not in self._ALLOWED_UNDERSCORES
        ]
        assert violations == [], (
            f"CAMBRIAN-SPEC-005 has underscore field names in tables "
            f"(should be kebab-case): {violations}"
        )

    def test_bootstrap_spec_table_fields_no_underscores(self) -> None:
        text = _read_spec(BOOTSTRAP_SPEC)
        fields = self._extract_table_field_names(text)
        violations = [
            (line, name)
            for line, name in fields
            if "_" in name and name not in self._ALLOWED_UNDERSCORES
        ]
        assert violations == [], (
            f"BOOTSTRAP-SPEC-002 has underscore field names in tables "
            f"(should be kebab-case): {violations}"
        )

    def test_cambrian_spec_json_examples_no_underscores(self) -> None:
        """JSON examples should use kebab-case for manifest/record fields."""
        text = _read_spec(CAMBRIAN_SPEC)
        keys = self._extract_json_keys_from_code_blocks(text)
        violations = [
            (line, name)
            for line, name in keys
            if "_" in name and name not in self._ALLOWED_UNDERSCORES
        ]
        assert violations == [], (
            f"CAMBRIAN-SPEC-005 has underscore keys in JSON examples: {violations}"
        )

    def test_bootstrap_spec_json_examples_no_underscores(self) -> None:
        text = _read_spec(BOOTSTRAP_SPEC)
        keys = self._extract_json_keys_from_code_blocks(text)
        violations = [
            (line, name)
            for line, name in keys
            if "_" in name and name not in self._ALLOWED_UNDERSCORES
        ]
        assert violations == [], (
            f"BOOTSTRAP-SPEC-002 has underscore keys in JSON examples: {violations}"
        )


# ---------------------------------------------------------------------------
# 3. Cross-spec schema agreement
# ---------------------------------------------------------------------------


class TestCrossSpecAgreement:
    """Verify that the two specs agree on shared schemas."""

    @staticmethod
    def _find_generation_record_fields(text: str) -> set[str]:
        """Extract GenerationRecord field names from a spec's table."""
        fields = set()
        # Find the GenerationRecord table — look for the header pattern
        in_gen_table = False
        for line in text.splitlines():
            if "| Field |" in line and "Required" in line and "Rule" in line:
                in_gen_table = True
                continue
            if in_gen_table:
                if line.strip().startswith("|---"):
                    continue
                m = re.match(r"\|\s*`([^`]+)`\s*\|", line)
                if m:
                    fields.add(m.group(1))
                elif not line.strip().startswith("|"):
                    in_gen_table = False
        return fields

    def test_generation_record_fields_agree(self) -> None:
        """Both specs define GenerationRecord — fields should match."""
        cambrian_fields = self._find_generation_record_fields(_read_spec(CAMBRIAN_SPEC))
        bootstrap_fields = self._find_generation_record_fields(_read_spec(BOOTSTRAP_SPEC))

        # The genome spec may have fields that the bootstrap spec also has
        # Both should contain the core MUST fields
        core_must_fields = {
            "generation",
            "parent",
            "spec-hash",
            "artifact-hash",
            "outcome",
            "created",
            "container-id",
        }
        for field in core_must_fields:
            assert field in cambrian_fields, (
                f"CAMBRIAN-SPEC-005 GenerationRecord missing MUST field: {field}"
            )
            assert field in bootstrap_fields, (
                f"BOOTSTRAP-SPEC-002 GenerationRecord missing MUST field: {field}"
            )

    def test_outcome_values_agree(self) -> None:
        """Both specs should list the same outcome values."""
        expected = {"in_progress", "tested", "promoted", "failed", "timeout"}
        for spec_path in (CAMBRIAN_SPEC, BOOTSTRAP_SPEC):
            text = _read_spec(spec_path)
            # Check that all expected values appear in the spec
            for value in expected:
                assert value in text, f"{spec_path.name} missing outcome value: {value}"

    def test_manifest_generation_allows_zero(self) -> None:
        """Manifest validation allows generation >= 0 (for test artifacts)."""
        bootstrap = _read_spec(BOOTSTRAP_SPEC)
        # The checklist line should say >= 0
        assert re.search(r"generation.*>=\s*0", bootstrap), (
            "BOOTSTRAP-SPEC-002 manifest validation should allow generation >= 0"
        )

    def test_generation_record_requires_ge_one(self) -> None:
        """GenerationRecord generation field requires >= 1."""
        for spec_path in (CAMBRIAN_SPEC, BOOTSTRAP_SPEC):
            text = _read_spec(spec_path)
            # Find GenerationRecord generation field — should say >= 1
            # Look for the pattern in a table row context
            pattern = re.compile(
                r"\|\s*`generation`\s*\|\s*MUST\s*\|.*?>=\s*1",
                re.DOTALL,
            )
            assert pattern.search(text), (
                f"{spec_path.name} GenerationRecord generation should require >= 1"
            )

    def test_supervisor_status_enum_in_genome_spec(self) -> None:
        """The genome spec must document the Supervisor status enum values."""
        text = _read_spec(CAMBRIAN_SPEC)
        for status in ("idle", "spawning", "testing", "promoting", "rolling-back"):
            assert status in text, f"CAMBRIAN-SPEC-005 missing Supervisor status value: {status}"

    def test_campaign_id_in_both_specs(self) -> None:
        """campaign-id should be documented in both specs."""
        for spec_path in (CAMBRIAN_SPEC, BOOTSTRAP_SPEC):
            text = _read_spec(spec_path)
            assert "campaign-id" in text, f"{spec_path.name} should document campaign-id field"


# ---------------------------------------------------------------------------
# 4. Spec ↔ Code agreement: manifest validation
# ---------------------------------------------------------------------------


class TestManifestSchemaCodeAgreement:
    """Verify that test_rig.py validates exactly the MUST fields from the spec."""

    @staticmethod
    def _get_validated_fields_from_code() -> set[str]:
        """Parse test_rig.py _validate_manifest to find validated field names."""
        import sys

        sys.path.insert(0, str(PROJECT_ROOT / "test-rig"))
        try:
            source = (PROJECT_ROOT / "test-rig" / "test_rig.py").read_text()
            # Find manifest.get("field-name") calls in _validate_manifest
            start = source.index("def _validate_manifest")
            end = source.index("\ndef ", start + 1)
            func_body = source[start:end]
            return set(re.findall(r'manifest\.get\("([^"]+)"\)', func_body))
        finally:
            sys.path.pop(0)

    def test_all_must_fields_validated(self) -> None:
        """Every MUST field from the spec should be validated in code."""
        must_fields = {
            "cambrian-version",
            "generation",
            "parent-generation",
            "spec-hash",
            "artifact-hash",
            "producer-model",
            "token-usage",
            "files",
            "created-at",
            "entry",
        }
        validated = self._get_validated_fields_from_code()
        missing = must_fields - validated
        assert missing == set(), (
            f"_validate_manifest does not validate these MUST fields: {missing}"
        )


# ---------------------------------------------------------------------------
# 5. Spec ↔ Code agreement: Supervisor HTTP endpoints
# ---------------------------------------------------------------------------


class TestSupervisorEndpointAgreement:
    """Verify that the code registers all endpoints declared in the spec."""

    @staticmethod
    def _get_registered_endpoints() -> set[tuple[str, str]]:
        """Parse supervisor.py make_app() to find registered routes."""
        source = (PROJECT_ROOT / "supervisor" / "supervisor.py").read_text()
        # Match app.router.add_get("/path", handler) and add_post
        return set(re.findall(r'app\.router\.add_(get|post)\("([^"]+)"', source))

    @staticmethod
    def _get_spec_endpoints() -> set[tuple[str, str]]:
        """Extract endpoints from BOOTSTRAP-SPEC-002 HTTP API table."""
        text = _read_spec(BOOTSTRAP_SPEC)
        endpoints: set[tuple[str, str]] = set()
        for line in text.splitlines():
            m = re.match(r"\|\s*(GET|POST)\s*\|\s*`(/[^`]+)`", line)
            if m:
                endpoints.add((m.group(1).lower(), m.group(2)))
        return endpoints

    def test_all_spec_endpoints_registered(self) -> None:
        """Every endpoint in the spec should be registered in the code."""
        spec_eps = self._get_spec_endpoints()
        code_eps = self._get_registered_endpoints()
        code_paths = {(method, path) for method, path in code_eps}
        for method, path in spec_eps:
            assert (method, path) in code_paths, (
                f"Spec endpoint {method.upper()} {path} not registered in code"
            )


# ---------------------------------------------------------------------------
# 6. Spec ↔ Code agreement: generation record field names
# ---------------------------------------------------------------------------


class TestGenerationRecordCodeAgreement:
    """Verify that the Supervisor code uses the same field names as the spec."""

    @staticmethod
    def _get_record_fields_from_code() -> set[str]:
        """Extract field names from the generation record dict literal in handle_spawn."""
        source = (PROJECT_ROOT / "supervisor" / "supervisor.py").read_text()
        # Find the record dict literal — use brace depth to find the matching }
        start = source.index("record: dict[str, Any] = {")
        brace_start = source.index("{", start)
        depth = 0
        for i in range(brace_start, len(source)):
            if source[i] == "{":
                depth += 1
            elif source[i] == "}":
                depth -= 1
                if depth == 0:
                    block = source[brace_start : i + 1]
                    break
        else:
            raise ValueError("Could not find closing brace for record dict")
        return set(re.findall(r'"([^"]+)":', block))

    def test_record_fields_use_kebab_case(self) -> None:
        """All record fields in code should use kebab-case (no underscores)."""
        fields = self._get_record_fields_from_code()
        underscore_fields = {f for f in fields if "_" in f}
        assert underscore_fields == set(), (
            f"Generation record uses underscore field names: {underscore_fields}. "
            f"Wire format should use kebab-case."
        )

    def test_record_has_all_must_fields(self) -> None:
        """The record dict in handle_spawn should include all MUST fields."""
        must_fields = {
            "generation",
            "parent",
            "spec-hash",
            "artifact-hash",
            "outcome",
            "created",
            "container-id",
        }
        code_fields = self._get_record_fields_from_code()
        missing = must_fields - code_fields
        assert missing == set(), f"handle_spawn record missing MUST fields: {missing}"


# ---------------------------------------------------------------------------
# 7. Spec required sections (Style Guide compliance)
# ---------------------------------------------------------------------------


class TestStyleGuideCompliance:
    """Verify CAMBRIAN-SPEC-005 has the sections required by SPEC-STYLE-GUIDE."""

    def test_has_required_sections(self) -> None:
        text = _read_spec(CAMBRIAN_SPEC)
        required_sections = [
            "Problem Statement",
            "Goals",
            "Non-Goals",
            "Design Principles",
            "Acceptance Criteria",
            "Examples",
            "References",
        ]
        headings = set(re.findall(r"^## (.+)$", text, re.MULTILINE))
        for section in required_sections:
            assert section in headings, (
                f"CAMBRIAN-SPEC-005 missing required section: '## {section}'"
            )

    def test_has_frozen_blocks(self) -> None:
        """The genome spec must have at least one FROZEN block for invariants."""
        text = _read_spec(CAMBRIAN_SPEC)
        assert "<!-- BEGIN FROZEN:" in text, "Missing FROZEN block markers"
        assert "<!-- END FROZEN:" in text, "Missing FROZEN end markers"

    def test_frozen_blocks_are_paired(self) -> None:
        text = _read_spec(CAMBRIAN_SPEC)
        begins = re.findall(r"<!-- BEGIN FROZEN: (\S+) -->", text)
        ends = re.findall(r"<!-- END FROZEN: (\S+) -->", text)
        assert begins == ends, f"FROZEN blocks not properly paired: begins={begins}, ends={ends}"


# ---------------------------------------------------------------------------
# 8. Docker hardening
# ---------------------------------------------------------------------------


class TestDockerHardening:
    """Verify Docker configuration follows security best practices."""

    def test_dockerfile_has_non_root_user(self) -> None:
        """Container must not run as root."""
        text = DOCKERFILE.read_text()
        assert "USER " in text, "Dockerfile missing USER directive — container runs as root"
        assert "useradd" in text or "adduser" in text, "Dockerfile should create a non-root user"

    def test_dockerfile_user_is_not_root(self) -> None:
        text = DOCKERFILE.read_text()
        user_lines = re.findall(r"^USER\s+(\S+)", text, re.MULTILINE)
        assert user_lines, "No USER directive found"
        for user in user_lines:
            assert user != "root", "USER directive should not be 'root'"

    def test_dockerignore_exists(self) -> None:
        assert DOCKERIGNORE.exists(), ".dockerignore missing — build context may be too large"

    def test_dockerignore_excludes_sensitive_dirs(self) -> None:
        text = DOCKERIGNORE.read_text()
        for item in (".git", ".env", ".beads", "__pycache__"):
            assert item in text, f".dockerignore should exclude {item}"

    def test_dockerfile_has_pythondontwritebytecode(self) -> None:
        text = DOCKERFILE.read_text()
        assert "PYTHONDONTWRITEBYTECODE" in text, "Dockerfile should set PYTHONDONTWRITEBYTECODE=1"

    def test_dockerfile_pip_no_cache(self) -> None:
        text = DOCKERFILE.read_text()
        assert "--no-cache-dir" in text, "pip install should use --no-cache-dir"

    def test_dockerfile_apt_cleanup(self) -> None:
        """apt-get should clean up lists in the same layer."""
        text = DOCKERFILE.read_text()
        # Find RUN lines with apt-get
        for line in text.splitlines():
            if "apt-get install" in line:
                assert "rm -rf /var/lib/apt/lists" in line, (
                    "apt-get install should clean up lists in the same RUN layer"
                )


# ---------------------------------------------------------------------------
# 9. Archived specs not referenced
# ---------------------------------------------------------------------------


class TestArchivedSpecsNotReferenced:
    """Active specs must not reference archived specs."""

    def test_cambrian_spec_no_archive_refs(self) -> None:
        text = _read_spec(CAMBRIAN_SPEC)
        for old_spec in (
            "CAMBRIAN-SPEC-001",
            "CAMBRIAN-SPEC-002",
            "CAMBRIAN-SPEC-003",
            "CAMBRIAN-SPEC-004",
            "BOOTSTRAP-SPEC-001",
        ):
            assert old_spec not in text, f"CAMBRIAN-SPEC-005 references archived spec {old_spec}"

    def test_bootstrap_spec_no_archive_refs(self) -> None:
        text = _read_spec(BOOTSTRAP_SPEC)
        for old_spec in (
            "CAMBRIAN-SPEC-001",
            "CAMBRIAN-SPEC-002",
            "CAMBRIAN-SPEC-003",
            "CAMBRIAN-SPEC-004",
        ):
            # BOOTSTRAP-SPEC-001 may be referenced as ancestor in frontmatter — skip that
            occurrences = [m.start() for m in re.finditer(old_spec, text)]
            # Filter out frontmatter ancestor reference
            non_frontmatter = [
                pos
                for pos in occurrences
                if pos > text.index("---", 4)  # after second ---
            ]
            assert non_frontmatter == [], (
                f"BOOTSTRAP-SPEC-002 references archived spec {old_spec} outside frontmatter"
            )
