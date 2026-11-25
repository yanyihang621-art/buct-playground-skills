"""Exercise project-level citation reconstruction with local synthetic TeX material."""
from pathlib import Path
import json
import shutil
import tempfile
from unittest.mock import patch

from dedup_references import deduplicate_library
from parse_bib_sources import parse_library_sources
from sync_manuscript_citations import sync_manuscript
from tex_project import export_tex_project, prepare_tex_project, readable_tex


def run():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        project = base / "input" / "project"
        (project / "ch").mkdir(parents=True)
        main = project / "main.tex"
        main.write_text(
            "\\documentclass{article}\n\\begin{document}\n"
            "% \\cite{comment} is a comment.\n\\input{ch/body}\n\\appendix\\include{ch/notes}\n"
            "\\bibliographystyle{unsrt}\n\\bibliography{library}\n\\end{document}\n", encoding="utf-8")
        body = project / "ch/body.tex"
        body.write_text("\\section{Body} Body \\cite{sourceA}.\n"
                        "\\begin{figure}\\caption{Caption \\cite{sourceB}.}\\end{figure}\n"
                        "\\verb|\\cite{literal}|\n", encoding="utf-8")
        appendix = project / "ch/notes.tex"
        appendix.write_text("\\section{Appendix} Appendix \\cite{sourceA}.\n", encoding="utf-8")
        library = project / "library.bib"
        library.write_text("\n".join("@article{" + k + ",title={" + t + "},author={A. " + t
                                     + "},journal={Fixture Journal},year={2020}}" for k, t in
                                     [("sourceA", "Heat"), ("sourceB", "Cooling")]), encoding="utf-8")
        canonical, _ = deduplicate_library(parse_library_sources([library]))
        original = {f: f.read_bytes() for f in (main, body, appendix, library)}
        out = base / "output"
        plan = prepare_tex_project(project.parent, main)
        printable = readable_tex(plan, plan.text)
        assert plan.tag not in printable and plan.text.count("\n") == printable.count("\n")
        assert "% \\cite{comment}" in printable and r"\verb|\cite{literal}|" in printable
        sync = sync_manuscript(plan.text, canonical, is_latex=True)
        compile_pdf = bool(shutil.which("pdflatex") and shutil.which("bibtex"))
        artifacts, issues = export_tex_project(plan, sync, out, compile_pdf=compile_pdf)
        assert not issues, issues
        assert all(p.read_bytes() == old for p, old in original.items())
        occ = sync["citation_map"]["in_text_occurrences"]
        assert [(o["source_file"], o["source_line"]) for o in occ] == [
            ("project/ch/body.tex", 1), ("project/ch/body.tex", 2), ("project/ch/notes.tex", 1)]
        assert Path(artifacts["tex_project_entry"]).parent.joinpath("ch/body.tex").read_text() == body.read_text()
        assert r"\bibliography{references_synced}" in Path(artifacts["tex_project_entry"]).read_text()
        if compile_pdf:
            rep = json.loads(Path(artifacts["tex_project_report"]).read_text())
            assert rep["compile"]["unresolved_citation_count"] == 0
            assert set(rep["compile"]["bibliography_keys"]) == {"sourceA", "sourceB"}
            assert Path(artifacts["tex_project_pdf"]).read_bytes().startswith(b"%PDF")
        with patch("tex_project.shutil.which", return_value=None):
            _, issues = export_tex_project(plan, sync, out / "missing_engine")
            assert issues[0]["compile"]["status"] == "unverified"
        body.write_text(body.read_text() + "\n\\input{ch/body}\\input{../../outside}\n", encoding="utf-8")
        bad = prepare_tex_project(project.parent, main)
        assert len(bad.issues) == 2
        assert all(i["source_file"] == "project/ch/body.tex" for i in bad.issues)
        if compile_pdf:
            body.write_bytes(original[body] + b"\nClaim \\cite{unlisted}.\n")
            bad = prepare_tex_project(project.parent, main)
            sync = sync_manuscript(bad.text, canonical, is_latex=True)
            artifacts, issues = export_tex_project(bad, sync, out / "unresolved")
            rep = json.loads(Path(artifacts["tex_project_report"]).read_text())
            assert rep["compile"]["status"] == "partial", rep
            assert rep["compile"]["unresolved_citation_count"] == 1, rep
        return {"passed": True, "real_tex_compile": compile_pdf, "nested_citation_sites": len(occ)}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
