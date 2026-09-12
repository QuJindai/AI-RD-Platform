"""Build the standalone GUI design preview without runtime dependencies."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    source = ROOT / "docs/gui/preview-fragment.html"
    destination = source.with_name("gui-preview.html")
    fragment = source.read_text(encoding="utf-8")
    document = (
        '<!doctype html>\n<html lang="zh-CN">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="description" content="AI-RD Platform 五页面交互设计预览；仅含合成演示内容">\n'
        '<title>AI-RD Platform · GUI 设计预览</title>\n'
        '<style>body{margin:0}button,input,select,textarea{font:inherit}</style>\n'
        '</head>\n<body>\n'
        + fragment
        + "\n</body>\n</html>\n"
    )
    destination.write_text(document, encoding="utf-8")
    print(destination)


if __name__ == "__main__":
    main()
