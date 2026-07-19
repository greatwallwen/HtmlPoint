from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
from PIL import Image

from course_helper.domain.common import SourceLocator
from course_helper.parsers.markdown_parser import MarkdownParser
from course_helper.source_roots import SourceRootRegistry


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def locator(relative_path: str) -> SourceLocator:
    return SourceLocator(root_id="fixture", relative_path=relative_path)


def parser_for(root: Path) -> MarkdownParser:
    return MarkdownParser(SourceRootRegistry({"fixture": root}))


def demo_parser() -> MarkdownParser:
    configured_root = os.environ.get("COURSE_REFERENCE_ROOT")
    if not configured_root:
        pytest.skip("COURSE_REFERENCE_ROOT is required for reference_demo tests")
    return MarkdownParser(
        SourceRootRegistry({"reference-demo": Path(configured_root)})
    )


def test_markdown_parser_does_not_treat_fenced_python_comments_as_headings(
    tmp_path: Path,
) -> None:
    write(
        tmp_path / "demo.md",
        "# 主题\n```python\n# 加载数据\nprint('ok')\n```\n## 方法\n正文",
    )

    result = parser_for(tmp_path).parse(locator("demo.md"))

    assert [chunk.heading for chunk in result.chunks] == ["主题", "方法"]
    assert "# 加载数据" in result.chunks[0].code_blocks[0]


@pytest.mark.parametrize(
    ("unsafe_target", "reason"),
    (
        ("E:/private/secret.png", "absolute-path"),
        ("https://private.example/secret.png", "external-uri"),
        ("../private/secret.png", "root-escape"),
    ),
)
def test_unsafe_image_creates_opaque_evidence_without_leaking_its_target(
    tmp_path: Path,
    unsafe_target: str,
    reason: str,
) -> None:
    write(tmp_path / "demo.md", f"# 主题\n说明 ![私密图]({unsafe_target})")

    result = parser_for(tmp_path).parse(locator("demo.md"))

    assert result.evidence.checks[0].code == "unresolved-link"
    assert result.evidence.checks[0].status == "warning"
    assert result.evidence.status == "warning"
    assert "私密图" in result.chunks[0].normalized_text
    assert result.evidence.checks[0].details["target_digest"] == hashlib.sha256(
        unsafe_target.encode("utf-8")
    ).hexdigest()
    assert result.evidence.checks[0].details["reason"] == reason
    assert unsafe_target not in result.model_dump_json()


def test_image_only_empty_alt_heading_still_emits_unresolved_evidence(
    tmp_path: Path,
) -> None:
    unsafe_target = "E:/private/heading-secret.png"
    write(tmp_path / "demo.md", f"# ![]({unsafe_target})")

    result = parser_for(tmp_path).parse(locator("demo.md"))

    assert result.chunks == ()
    check = next(check for check in result.evidence.checks if check.code == "unresolved-link")
    assert check.details["target_digest"] == hashlib.sha256(
        unsafe_target.encode("utf-8")
    ).hexdigest()
    assert check.details["reason"] == "absolute-path"
    assert unsafe_target not in result.model_dump_json()


def test_image_only_empty_alt_heading_emits_resolved_visual(tmp_path: Path) -> None:
    image_path = tmp_path / "assets" / "heading.png"
    image_path.parent.mkdir()
    Image.new("RGB", (4, 5), color=(15, 118, 110)).save(image_path)
    write(tmp_path / "demo.md", "# ![](assets/heading.png)")

    result = parser_for(tmp_path).parse(locator("demo.md"))

    assert result.chunks == ()
    assert len(result.visuals) == 1
    assert result.visuals[0].content_digest == hashlib.sha256(
        image_path.read_bytes()
    ).hexdigest()
    assert (result.visuals[0].width, result.visuals[0].height) == (4, 5)
    assert result.visuals[0].asset_url == "assets/heading.png"


def test_markdown_parser_preserves_hierarchy_tables_and_relative_images(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "assets" / "chart.png"
    image_path.parent.mkdir()
    Image.new("RGB", (2, 3), color=(36, 99, 235)).save(image_path)
    write(
        tmp_path / "demo.md",
        """# 单元
导言 ![趋势图](assets/chart.png)

## 分析
| 指标 | 值 |
| --- | ---: |
| 需求 | 42 |

```python
print(42)
```
""",
    )

    result = parser_for(tmp_path).parse(locator("demo.md"))

    assert [chunk.heading for chunk in result.chunks] == ["单元", "分析"]
    assert result.chunks[0].breadcrumb == ("单元",)
    assert result.chunks[1].breadcrumb == ("单元", "分析")
    assert "导言" in result.chunks[0].normalized_text
    assert "趋势图" in result.chunks[0].normalized_text
    assert result.chunks[1].table_rows == (("指标", "值"), ("需求", "42"))
    assert result.chunks[1].code_blocks == ("print(42)\n",)
    assert len(result.visuals) == 1
    assert (result.visuals[0].width, result.visuals[0].height) == (2, 3)
    assert result.chunks[0].media_version_ids == (result.visuals[0].version_id,)
    assert not any(check.code == "unresolved-link" for check in result.evidence.checks)


def test_selected_h1_includes_descendants_until_the_next_h1(tmp_path: Path) -> None:
    write(
        tmp_path / "demo.md",
        """# 第一单元
第一单元导言
## 第一节
第一节正文
# 第二单元
第二单元导言
## 第二节
第二节正文
""",
    )

    result = parser_for(tmp_path).parse(
        locator("demo.md"),
        heading_selectors=("第一单元",),
    )

    assert [chunk.heading for chunk in result.chunks] == ["第一单元", "第一节"]
    assert all(chunk.breadcrumb[0] == "第一单元" for chunk in result.chunks)


def test_selected_nested_heading_includes_descendants_until_its_next_peer(
    tmp_path: Path,
) -> None:
    write(
        tmp_path / "demo.md",
        """# Prompt概论
## 正确提问
### 元素一
正文
## 添加参照
不应返回
""",
    )

    result = parser_for(tmp_path).parse(
        locator("demo.md"),
        heading_selectors=("正确提问",),
    )

    assert [chunk.heading for chunk in result.chunks] == ["正确提问", "元素一"]


def test_skipped_heading_levels_have_unique_ast_paths_and_chunk_ids(
    tmp_path: Path,
) -> None:
    write(
        tmp_path / "demo.md",
        """# Root
### Same
Body
## Same
Body
""",
    )

    result = parser_for(tmp_path).parse(locator("demo.md"))

    ast_paths = [chunk.locator.ast_path for chunk in result.chunks]
    chunk_ids = [chunk.chunk_id for chunk in result.chunks]
    assert len(set(ast_paths)) == len(ast_paths)
    assert len(set(chunk_ids)) == len(chunk_ids)


def test_missing_relative_image_is_visible_in_chunk_and_evidence(tmp_path: Path) -> None:
    missing_target = "assets/missing.png"
    write(tmp_path / "demo.md", f"# 主题\n正文 ![缺失图]({missing_target})")

    result = parser_for(tmp_path).parse(locator("demo.md"))

    check = next(check for check in result.evidence.checks if check.code == "unresolved-link")
    assert check.status == "warning"
    target_digest = hashlib.sha256(missing_target.encode("utf-8")).hexdigest()
    assert check.details["target_digest"] == target_digest
    assert check.details["reason"] == "not-found"
    assert "缺失图" in result.chunks[0].normalized_text
    assert missing_target not in result.model_dump_json()
    assert result.chunks[0].warnings == (
        f"Unresolved image link (not-found; ref=sha256:{target_digest[:12]})",
    )


def test_markdown_parser_is_deterministic_for_the_same_source(tmp_path: Path) -> None:
    write(tmp_path / "demo.md", "# 稳定结果\n正文")
    parser = parser_for(tmp_path)
    source = locator("demo.md")

    assert parser.parse(source) == parser.parse(source)


@pytest.mark.reference_demo
@pytest.mark.parametrize(
    ("relative_path", "heading_selectors"),
    (
        ("AIGC实操 -数据分析.md", ("自行车共享需求",)),
        ("AIGC实操-Prompt工程.md", ("Prompt概论", "正确提问")),
    ),
)
def test_allowlisted_markdown_demo_selectors_return_only_requested_units(
    relative_path: str,
    heading_selectors: tuple[str, ...],
) -> None:
    result = demo_parser().parse(
        SourceLocator(root_id="reference-demo", relative_path=relative_path),
        heading_selectors=heading_selectors,
    )

    assert result.chunks
    extracted_headings = {chunk.heading for chunk in result.chunks}
    assert extracted_headings.issuperset(heading_selectors)
    assert all(chunk.breadcrumb[0] == heading_selectors[0] for chunk in result.chunks)
