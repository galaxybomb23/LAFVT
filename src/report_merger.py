import os
from pathlib import Path
from typing import List, Dict, Any

class ReportMerger:
    def merge(self, build_dir: Path):
        """
        Aggregates existing index.html reports within the build directory
        into a new main index.html with links to sub-reports.
        """
        build_dir = Path(build_dir)
        build_dir.mkdir(parents=True, exist_ok=True)

        html_content = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Aggregated LAFVT Reports</title>
            <style>
                body { font-family: sans-serif; margin: 20px; }
                ul { list-style-type: none; padding: 0; }
                li { margin-bottom: 10px; }
                a { text-decoration: none; color: #007bff; }
                a:hover { text-decoration: underline; }
            </style>
        </head>
        <body>
            <h1>Aggregated LAFVT Reports</h1>
            <ul>
        """

        # Find all index.html files within subdirectories of build_dir
        # and create links to them.
        for report_path in sorted(build_dir.glob('**/index.html')):
            if report_path == build_dir / "index.html":
                continue

            relative_path = report_path.relative_to(build_dir)
            # Use the parent directory name as the display name for the link
            # If it's directly in build_dir, use its own name or a default.
            display_name = str(relative_path.parent) if relative_path.parent != Path('.') else relative_path.name.replace('.html', '').replace('_', ' ').title()
            if not display_name: # Handle cases where parent is empty string for direct children
                display_name = relative_path.name.replace('.html', '').replace('_', ' ').title()

            html_content += f'                <li><a href="{relative_path}">{display_name}</a></li>\n'

        html_content += """
            </ul>
        </body>
        </html>
        """

        with open(build_dir / "index.html", "w") as f:
            f.write(html_content)
            
        print(f"Report generated at {build_dir / 'index.html'}")
